"""cedar-shim — translates agentgateway's extAuthz into a Cedar decision.

WHY THIS EXISTS
    agentgateway's HTTP external-authorization mode treats **any 2xx as allow**.
    cedar-agent returns HTTP 200 for *both* Allow and Deny, with the verdict in
    the response body. Wiring them together directly would allow everything.
    This reads the verdict and maps it onto the status code agentgateway needs.

WHAT IT DOES NOT TRUST
    The calling agent controls its own request headers. Anything an agent could
    set is treated as a claim, not a fact:

      * agent identity comes from the header agentgateway populates *after* it
        has authenticated the caller (AGENT_ID_HEADER). agentgateway must be
        configured so a client cannot inject it -- verify this on first boot.
      * approval is never read from the request. A request may *reference* a
        Temporal workflow id, but the verdict is fetched from Temporal. An
        agent naming a workflow it did not get approved gains nothing.

    Anything indeterminate fails closed (403).
"""

import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CEDAR_AGENT_URL = os.getenv("CEDAR_AGENT_URL", "http://cedar-agent:8180").rstrip("/")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "9000"))
AGENT_ID_HEADER = os.getenv("AGENT_ID_HEADER", "x-agentmesh-agent").lower()
HITL_WORKFLOW_HEADER = os.getenv("HITL_WORKFLOW_HEADER", "x-agentmesh-hitl-workflow").lower()
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
MAX_BODY = 1 * 1024 * 1024

NS = "AgentMesh"

logging.basicConfig(level=logging.INFO, format="%(asctime)s cedar-shim %(message)s")
log = logging.getLogger("cedar-shim")


def classify(path: str, body: dict):
    """Map an inbound request to a Cedar (action, resource).

    Returns (action, resource_type, resource_id) or None when the request
    cannot be classified -- which is a denial, not a pass-through.
    """
    p = path.lower()
    if "/chat/completions" in p or "/v1/messages" in p or "/completions" in p:
        model = body.get("model")
        if isinstance(model, str) and model:
            return "call_model", "Model", model
        return None
    if "/mcp" in p or "/tools" in p:
        # MCP tool calls carry the tool name in params.name (JSON-RPC) or tool.
        tool = body.get("tool") or (body.get("params") or {}).get("name")
        if isinstance(tool, str) and tool:
            return "call_tool", "Tool", tool
        return None
    return None


def approval_state(workflow_id: str) -> bool:
    """Ask Temporal whether this approval workflow is approved. Never raises."""
    if not workflow_id or not TEMPORAL_HOST:
        return False
    try:
        import asyncio

        from temporalio.client import Client

        async def query() -> str:
            client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
            return await client.get_workflow_handle(workflow_id).query("get_approval_state")

        return asyncio.run(query()) == "Approved"
    except Exception as exc:
        log.warning("approval lookup failed for %s: %s", workflow_id, exc)
        return False


def cedar_decision(principal: str, action: str, res_type: str, res_id: str, hitl: bool):
    """Call cedar-agent. Returns (allowed, reason)."""
    payload = {
        "principal": f'{NS}::Agent::"{principal}"',
        "action": f'{NS}::Action::"{action}"',
        "resource": f'{NS}::{res_type}::"{res_id}"',
        "context": {"hitl_approved": hitl},
    }
    request = urllib.request.Request(
        f"{CEDAR_AGENT_URL}/v1/is_authorized",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # A non-2xx from cedar-agent is itself a denial: we did not get an
        # Allow, so we must not manufacture one.
        return False, f"cedar-agent HTTP {exc.code}"
    except Exception as exc:
        return False, f"cedar-agent unreachable: {type(exc).__name__}"

    decision = str(result.get("decision", "")).lower()
    reasons = (result.get("diagnostics") or {}).get("reason") or []
    return decision == "allow", f"cedar:{decision or 'unknown'} {reasons}"


class Handler(BaseHTTPRequestHandler):
    server_version = "cedar-shim/1.0"

    def log_message(self, fmt, *args):
        pass  # decisions are logged explicitly below

    def _respond(self, status: int, reason: str):
        body = json.dumps({"allowed": status == 200, "reason": reason}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return {}
        if length <= 0 or length > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return {}

    def _authorize(self):
        body = self._read_body()
        principal = (self.headers.get(AGENT_ID_HEADER) or "").strip()
        if not principal:
            log.info("DENY path=%s reason=no-agent-identity", self.path)
            self._respond(403, "no authenticated agent identity")
            return

        classified = classify(self.path, body)
        if classified is None:
            log.info("DENY agent=%s path=%s reason=unclassifiable", principal, self.path)
            self._respond(403, "request could not be mapped to a Cedar resource")
            return

        action, res_type, res_id = classified
        hitl = approval_state((self.headers.get(HITL_WORKFLOW_HEADER) or "").strip())
        allowed, reason = cedar_decision(principal, action, res_type, res_id, hitl)

        log.info(
            "%s agent=%s action=%s resource=%s hitl=%s (%s)",
            "ALLOW" if allowed else "DENY", principal, action, res_id, hitl, reason,
        )
        self._respond(200 if allowed else 403, reason)

    # agentgateway may probe with any method; treat them all as checks.
    do_POST = _authorize
    do_GET = _authorize
    do_PUT = _authorize
    do_DELETE = _authorize

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True
        except Exception as exc:
            log.error("handler error: %s", exc)
            try:
                self._respond(403, "shim error")  # fail closed
            except Exception:
                self.close_connection = True


def main():
    log.info("listening on :%s -> %s", LISTEN_PORT, CEDAR_AGENT_URL)
    log.info("agent identity header: %s", AGENT_ID_HEADER)
    log.info("temporal approval lookup: %s", TEMPORAL_HOST or "disabled (approvals always false)")
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
