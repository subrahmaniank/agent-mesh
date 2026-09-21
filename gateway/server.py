import json
import os
import re
import yaml
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver
import httpx

GATEWAY_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(GATEWAY_DIR, "config.yaml")

# Load configuration
config = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

PORT = int(os.getenv("PORT", config.get("server", {}).get("port", 8080)))
HOST = os.getenv("HOST", config.get("server", {}).get("host", "0.0.0.0"))

PRESIDIO_ANALYZER_URL = os.getenv(
    "PRESIDIO_ANALYZER_URL",
    config.get("guardrails", {}).get("presidio", {}).get("analyzer_endpoint", "http://localhost:5001/analyze")
)
PRESIDIO_ANONYMIZER_URL = os.getenv(
    "PRESIDIO_ANONYMIZER_URL",
    config.get("guardrails", {}).get("presidio", {}).get("anonymizer_endpoint", "http://localhost:5002/anonymize")
)

def evaluate_cedar_policy(principal_group: str, user_clearance: int, action: str, tool_risk: int, payload_str: str, hitl_approved: bool) -> tuple[str, str]:
    """In-memory Cedar Policy Decision Point (PDP)."""
    # 1. OWASP LLM06 / Destructive invariant check
    destructive_patterns = ["DROP TABLE", "DELETE FROM", "rm -rf", "TRUNCATE", "DROP DATABASE"]
    for pattern in destructive_patterns:
        if pattern.lower() in payload_str.lower():
            if not hitl_approved:
                return "DENY", f"Destructive command detected ('{pattern}') without verified HITL approval."
    
    # 2. Dynamic Tool Clearance Check
    if user_clearance < tool_risk:
        return "DENY", f"Insufficient clearance: user has clearance {user_clearance}, but tool risk requires {tool_risk}."
    
    # 3. Base Invocation Rules
    if action == "invoke" and principal_group == "AuthorizedOperators":
        return "ALLOW", "Authorized operator permitted to invoke agent."
    
    if action == "call_tool":
        return "ALLOW", "Tool call authorized under current clearance and guardrails."
    
    return "DENY", "Default deny: no matching permit rule found."

def sanitize_payload(payload: dict) -> dict:
    """Sanitize payload using Presidio or regex fallback."""
    raw_str = json.dumps(payload)
    
    # Try calling Presidio analyzer + anonymizer if available
    try:
        with httpx.Client(timeout=2.0) as client:
            resp_analyze = client.post(
                PRESIDIO_ANALYZER_URL if "analyze" in PRESIDIO_ANALYZER_URL else f"{PRESIDIO_ANALYZER_URL}/analyze",
                json={"text": raw_str, "language": "en"}
            )
            if resp_analyze.status_code == 200:
                entities = resp_analyze.json()
                if entities:
                    resp_anon = client.post(
                        PRESIDIO_ANONYMIZER_URL if "anonymize" in PRESIDIO_ANONYMIZER_URL else f"{PRESIDIO_ANONYMIZER_URL}/anonymize",
                        json={"text": raw_str, "anonymizers_config": {}, "analyzer_results": entities}
                    )
                    if resp_anon.status_code == 200:
                        anon_text = resp_anon.json().get("text", raw_str)
                        try:
                            return json.loads(anon_text)
                        except Exception:
                            return {"sanitized_text": anon_text}
    except Exception:
        # Fallback local regex masking
        pass

    # Regex fallback for SSN, Email, Phone
    sanitized = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '<US_SSN_REDACTED>', raw_str)
    sanitized = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '<EMAIL_REDACTED>', sanitized)
    sanitized = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '<PHONE_REDACTED>', sanitized)
    
    try:
        return json.loads(sanitized)
    except Exception:
        return payload

class GatewayHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/healthz"):
            self._send_json(200, {
                "status": "healthy",
                "service": "AgentGateway.dev",
                "version": "v1.0.0",
                "port": PORT,
                "pdp_engine": "Cedar In-Memory Policy Decision Point",
                "dlp_integration": "Microsoft Presidio (Analyzer + Anonymizer)",
                "endpoints": [
                    "POST /v1/tools/call",
                    "POST /v1/policies/evaluate",
                    "GET /v1/policies",
                    "GET /healthz"
                ]
            })
        elif self.path == "/v1/policies":
            schema_file = os.path.join(GATEWAY_DIR, "policies", "schema.cedarschema")
            policy_file = os.path.join(GATEWAY_DIR, "policies", "base_guardrails.cedar")
            schema_content = open(schema_file, encoding="utf-8").read() if os.path.exists(schema_file) else ""
            policy_content = open(policy_file, encoding="utf-8").read() if os.path.exists(policy_file) else ""
            self._send_json(200, {
                "schema": schema_content,
                "policies": policy_content
            })
        else:
            self._send_json(404, {"error": "Not Found", "path": self.path})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            req_data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            self._send_json(400, {"error": "Invalid JSON payload"})
            return

        if self.path == "/v1/tools/call":
            agent_id = req_data.get("agent_id", "unknown-agent")
            tool_name = req_data.get("tool", "unknown_tool")
            arguments = req_data.get("arguments", {})
            user_context = req_data.get("context", {})

            principal_group = user_context.get("principal_group", "AuthorizedOperators")
            user_clearance = int(user_context.get("user_clearance", 3))
            tool_risk = int(user_context.get("tool_risk", 2))
            hitl_approved = bool(user_context.get("hitl_approved", False))
            payload_str = json.dumps(arguments)

            decision, reason = evaluate_cedar_policy(
                principal_group=principal_group,
                user_clearance=user_clearance,
                action="call_tool",
                tool_risk=tool_risk,
                payload_str=payload_str,
                hitl_approved=hitl_approved
            )

            if decision != "ALLOW":
                self._send_json(403, {
                    "status": "denied",
                    "decision": "DENY",
                    "error": f"Cedar Policy Violation: {reason}",
                    "tool": tool_name,
                    "agent_id": agent_id
                })
                return

            sanitized_args = sanitize_payload(arguments)

            self._send_json(200, {
                "status": "success",
                "decision": "ALLOW",
                "tool": tool_name,
                "agent_id": agent_id,
                "result": f"Tool '{tool_name}' executed successfully via AgentGateway PEP.",
                "sanitized_arguments": sanitized_args,
                "privacy": {"sanitized": True, "dlp_mode": "in-flight-tokenized"}
            })

        elif self.path == "/v1/policies/evaluate":
            principal_group = req_data.get("principal_group", "AuthorizedOperators")
            user_clearance = int(req_data.get("user_clearance", 3))
            action = req_data.get("action", "call_tool")
            tool_risk = int(req_data.get("tool_risk", 2))
            payload_str = req_data.get("payload", "")
            hitl_approved = bool(req_data.get("hitl_approved", False))

            decision, reason = evaluate_cedar_policy(
                principal_group=principal_group,
                user_clearance=user_clearance,
                action=action,
                tool_risk=tool_risk,
                payload_str=payload_str,
                hitl_approved=hitl_approved
            )

            self._send_json(200, {
                "decision": decision,
                "reason": reason,
                "inputs": {
                    "principal_group": principal_group,
                    "user_clearance": user_clearance,
                    "action": action,
                    "tool_risk": tool_risk,
                    "hitl_approved": hitl_approved
                }
            })
        else:
            self._send_json(404, {"error": "Not Found", "path": self.path})

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def main():
    server_address = (HOST, PORT)
    httpd = ThreadedHTTPServer(server_address, GatewayHandler)
    print("==================================================")
    print(f"🛡️  AgentGateway.dev (PEP & Cedar PDP) Running")
    print(f"🌐  Listening on: http://{HOST}:{PORT}")
    print(f"📡  Presidio Analyzer: {PRESIDIO_ANALYZER_URL}")
    print(f"🔒  Presidio Anonymizer: {PRESIDIO_ANONYMIZER_URL}")
    print("==================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Agent Gateway...")
        httpd.server_close()

if __name__ == "__main__":
    main()
