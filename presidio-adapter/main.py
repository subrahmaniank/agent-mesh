"""presidio-adapter — Microsoft Presidio behind agentgateway's guardrail webhook.

WHY THIS EXISTS
    agentgateway calls POST /request and POST /response with its own envelope;
    Presidio speaks /analyze and /anonymize. Nothing bridges the two.

THE CONTRACT (confirmed from agentgateway's own JEV guardrail example)
    inbound  /request   {"body": {"messages": [{"role","content"}, ...]}}
    inbound  /response  {"body": {"choices": [{"message": {...}}]}}
    outbound pass       {"action": {"reason": "..."}}
    outbound reject     {"action": {"status_code": 403, "body": "...", "reason": "..."}}

    Always HTTP 200 -- `status_code` inside `action` is what rejects the
    original request. Response bodies are deserialized untagged, so the three
    action shapes are distinguished structurally, not by a "type" field.

    ⚠️ The vendor docs disagree on whether a webhook may MASK: the guardrails
    overview lists webhook actions as reject/audit, while the Guardrail Webhook
    API describes Pass/Mask/Reject. v1.5.0 accepts only reject|audit —
    `action: mask` is rejected at config parse time — so a webhook cannot
    redact. Masking is the regex layer's job.

WHAT THIS ADAPTER CANNOT DO, AND WHY
    It cannot vary its strictness by destination. Screening ought to be harsher
    for a call leaving the premises than for one to an on-prem model, but the
    envelope agentgateway sends is exactly:

        {"body": {"messages": [...]}}          keys verified at runtime

    No model, no route, no metadata — and the webhook's `headers:` field did not
    arrive either. Three other routes were tried and rejected:

      * per-model `guardrails:` blocks parse and do apply, but not to every
        message role. With top-level `reject` and per-model `audit`, PII in a
        `user` turn passed while the same text in a `system` or `assistant` turn
        was rejected. Any agent framework accumulates assistant turns, so a
        conversation cannot be governed from there.
      * `headers: {x-agentmesh-model: llm.model}` validates but never reaches
        this endpoint.
      * the request body would carry `model`, but the guardrail envelope strips
        everything except `messages`.

    So destination-based screening needs either a second adapter instance wired
    to hosted model entries, or a gateway version that passes route context.
    Until then PRESIDIO_ENTITIES is global, and Cedar's `allowed_model_tiers` is
    what actually keeps an agent's traffic on-premises.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit

def _csv(name: str, default: str = "") -> list:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


ANALYZER_URL = os.getenv("PRESIDIO_ANALYZER_URL", "http://presidio-analyzer:3000")
ANONYMIZER_URL = os.getenv("PRESIDIO_ANONYMIZER_URL", "http://presidio-anonymizer:3000")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8080"))
ACTION = os.getenv("GUARDRAIL_ACTION", "reject").strip().lower()
SCORE_THRESHOLD = float(os.getenv("PRESIDIO_SCORE_THRESHOLD", "0.5"))
LANGUAGE = os.getenv("PRESIDIO_LANGUAGE", "en")
ENTITIES = _csv("PRESIDIO_ENTITIES")


# Log which span of which message tripped the guard. Off by default: it writes
# the detected value to the log, which is the thing we are trying not to spread
# around. Indispensable when a framework's prompt is blocked and you cannot see
# what the framework actually sent.
DEBUG_MATCHES = os.getenv("ADAPTER_DEBUG_MATCHES", "") == "1"
MAX_BODY = 4 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s presidio-adapter %(message)s")
log = logging.getLogger("presidio-adapter")


def endpoint(base: str, op: str) -> str:
    """Append /<op> unless the URL PATH already ends with it.

    Testing `op in url` would match the *hostname* `presidio-analyzer`, leaving
    the path off and silently posting to the container root.
    """
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    if path.endswith("/" + op):
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/{op}", "", ""))


ANALYZE = endpoint(ANALYZER_URL, "analyze")
ANONYMIZE = endpoint(ANONYMIZER_URL, "anonymize")


def post_json(url: str, payload: dict, timeout: float = 5.0):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def extract_texts(envelope: dict):
    """Pull every inspectable string out of an agentgateway envelope.

    Returns a list of (path, text) where path locates the value so a mask can
    be written back to exactly the field it came from.
    """
    body = envelope.get("body") or {}
    found = []

    for index, message in enumerate(body.get("messages") or []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            found.append((("messages", index, "content"), content))
        elif isinstance(content, list):
            # Multimodal content: inspect the text parts only.
            for part_index, part in enumerate(content):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    found.append((("messages", index, "content", part_index, "text"), part["text"]))

    for index, choice in enumerate(body.get("choices") or []):
        message = choice.get("message") if isinstance(choice, dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            found.append((("choices", index, "message", "content"), message["content"]))

    return found


def set_at(body: dict, path, value) -> None:
    node = body
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def analyze(text: str, entities=None):
    payload = {"text": text, "language": LANGUAGE}
    if entities:
        payload["entities"] = entities
    findings = post_json(ANALYZE, payload)
    return [f for f in findings if float(f.get("score", 0)) >= SCORE_THRESHOLD]


def anonymize(text: str, findings) -> str:
    result = post_json(ANONYMIZE, {"text": text, "analyzer_results": findings})
    return result.get("text", text)


def pass_action(reason: str) -> dict:
    return {"action": {"reason": reason}}


def reject_action(reason: str, detail: str) -> dict:
    return {"action": {"status_code": 403, "body": detail, "reason": reason}}


def inspect(envelope: dict) -> dict:
    """Run Presidio over an envelope and return an agentgateway action."""
    texts = extract_texts(envelope)
    if not texts:
        return pass_action("no inspectable content")

    hits, masked_any = [], False
    matches: list[tuple[str, str]] = []
    body = envelope.get("body") or {}


    for path, text in texts:
        if not text.strip():
            continue
        findings = analyze(text, ENTITIES)
        if not findings:
            continue
        hits.extend(sorted({f.get("entity_type", "UNKNOWN") for f in findings}))
        if DEBUG_MATCHES:
            matches.extend(
                (f"{'.'.join(str(x) for x in path)}:{f.get('entity_type')}",
                 text[f.get("start", 0):f.get("end", 0)])
                for f in findings
            )
        if ACTION == "mask":
            set_at(body, path, anonymize(text, findings))
            masked_any = True

    if not hits:
        return pass_action("no PII detected")

    entities = sorted(set(hits))
    if ACTION == "mask" and masked_any:
        log.info("MASK entities=%s", entities)
        # Mask shape: the modified body is returned for agentgateway to forward.
        return {"action": {"body": body, "reason": f"masked: {', '.join(entities)}"}}

    # "PII-FOUND", not "REJECT": this returns a rejection *envelope*, but whether
    # it blocks anything is the gateway's call — a webhook configured
    # `action: audit` logs the finding and forwards the call regardless. Saying
    # REJECT here made successful requests look blocked in the logs.
    log.info("PII-FOUND entities=%s (blocks only where the webhook action is `reject`)",
             entities)
    if DEBUG_MATCHES:
        for where, what in matches:
            log.info("  match %s -> %r", where, what)
    return reject_action(
        f"PII detected: {', '.join(entities)}",
        f"Request blocked: content contains {', '.join(entities)}.",
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "presidio-adapter/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            self._send({"status": "ok", "action": ACTION,
                        "analyzer": ANALYZE, "anonymizer": ANONYMIZE})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.rstrip("/") not in ("/request", "/response"):
            self._send({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if 0 < length <= MAX_BODY else b"{}"
            envelope = json.loads(raw.decode() or "{}")
        except Exception:
            # Unparseable input is not a reason to let content through.
            self._send(reject_action("malformed guardrail envelope", "Blocked."))
            return

        if DEBUG_MATCHES:
            log.info("envelope %s keys=%s body_keys=%s headers=%s", self.path,
                     sorted(envelope.keys()),
                     sorted((envelope.get("body") or {}).keys()),
                     json.dumps(dict(self.headers.items()))[:400])
        try:
            self._send(inspect(envelope))
        except Exception as exc:
            # Presidio down, timeout, bad response -- fail CLOSED. Inference
            # leaves the building; an unavailable scanner must not mean
            # unscrubbed prompts reach a provider.
            log.error("inspection failed, rejecting: %s: %s", type(exc).__name__, exc)
            self._send(reject_action(
                f"PII scanning unavailable ({type(exc).__name__})",
                "Request blocked: content could not be screened for PII.",
            ))

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True
        except Exception as exc:
            log.error("handler error: %s", exc)
            self.close_connection = True


def main():
    log.info("listening on :%s action=%s threshold=%s", LISTEN_PORT, ACTION, SCORE_THRESHOLD)
    log.info("analyzer=%s anonymizer=%s", ANALYZE, ANONYMIZE)
    if ACTION not in ("reject", "mask"):
        log.warning("unknown GUARDRAIL_ACTION=%r, treating as reject", ACTION)
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
