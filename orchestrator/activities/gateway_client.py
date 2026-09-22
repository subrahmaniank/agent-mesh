"""Temporal activities that reach the outside world through agentgateway.

Everything an agent does — model calls and tool calls alike — goes through the
gateway on one OpenAI-compatible surface, so authentication, Cedar
authorization, PII guardrails and telemetry apply uniformly.

Identity note: these activities send only their bearer credential. The agent
identity Cedar authorizes against is derived by agentgateway from that
credential and forwarded to cedar-shim. A client must not be able to assert its
own identity header, or the authorization model collapses.
"""

import os

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:4000").rstrip("/")
GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "llama3")

# Temporal matches non_retryable_error_types against the ApplicationError
# `type` string. A policy decision will not change by trying again, so denials
# must fail fast rather than burn the retry budget.
CEDAR_POLICY_VIOLATION = "CedarPolicyViolationError"


class GatewayDenied(Exception):
    """agentgateway refused the call (authn, Cedar, or a guardrail)."""

    def __init__(self, status_code: int, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if GATEWAY_TOKEN:
        headers["Authorization"] = f"Bearer {GATEWAY_TOKEN}"
    return headers


def _unwrap(response: httpx.Response) -> dict:
    if response.status_code in (401, 403):
        try:
            detail = response.json()
            reason = detail.get("error") or detail.get("reason") or response.text
        except ValueError:
            reason = response.text
        raise GatewayDenied(response.status_code, str(reason)[:500])
    response.raise_for_status()
    return response.json()


def _as_non_retryable(exc: GatewayDenied, what: str) -> ApplicationError:
    activity.logger.warning("gateway denied %s: %s", what, exc.reason)
    return ApplicationError(
        f"Gateway denied {what}: {exc.reason}",
        type=CEDAR_POLICY_VIOLATION,
        non_retryable=True,
    )


@activity.defn
async def invoke_agent_step(params: dict) -> dict:
    """Orchestration-plane provisioning step.

    Still a stub; it models infrastructure the saga must be able to roll back.
    """
    action = params.get("action")
    if action == "create_staging_workspace":
        return {"workspace_id": "ws-98721"}
    if action == "provision_test_db":
        return {"db_id": "db-sandbox-401"}
    return {"status": "ok"}


@activity.defn
async def call_model(params: dict) -> dict:
    """Run inference through agentgateway.

    `model` names a backend configured in agentgateway/config.yaml. Which
    provider serves it is a gateway configuration decision, so switching
    providers never touches this code.
    """
    body = {
        "model": params.get("model", DEFAULT_MODEL),
        "messages": params["messages"],
    }
    for optional in ("max_tokens", "temperature", "stream", "tools"):
        if optional in params:
            body[optional] = params[optional]

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            return _unwrap(
                await client.post(
                    f"{GATEWAY_URL}/v1/chat/completions", json=body, headers=_headers()
                )
            )
    except GatewayDenied as denied:
        raise _as_non_retryable(denied, f"model '{body['model']}'") from denied


@activity.defn
async def call_gateway_tool(params: dict) -> dict:
    """Invoke an MCP tool through agentgateway."""
    body = {"tool": params["tool"], "arguments": params.get("arguments", {})}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            return _unwrap(
                await client.post(f"{GATEWAY_URL}/mcp", json=body, headers=_headers())
            )
    except GatewayDenied as denied:
        raise _as_non_retryable(denied, f"tool '{params['tool']}'") from denied


@activity.defn
async def request_human_approval(payload: dict) -> bool:
    activity.logger.info("Notification: action %s requires approval.", payload)
    return True
