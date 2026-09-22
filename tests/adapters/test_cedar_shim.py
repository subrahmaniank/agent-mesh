"""cedar-shim tests.

cedar-agent is mocked, so these run with no Docker. The central case is
`test_deny_with_http_200_becomes_403` — cedar-agent answers 200 for *both*
Allow and Deny, and agentgateway treats any 2xx as allow, so without this
translation every request would be permitted.
"""

import os

import httpx
import pytest

from conftest import MockBackend, load_module, serve

AGENT_HEADER = "x-agentmesh-agent"


def cedar_routes(decision="Allow", status=200, reasons=None):
    def is_authorized(body):
        return status, {
            "decision": decision,
            "diagnostics": {"reason": reasons or ["policy0"], "errors": []},
        }
    return {"/v1/is_authorized": is_authorized}


def boot(cedar_url, name, **env):
    os.environ.update({
        "CEDAR_AGENT_URL": cedar_url,
        "TEMPORAL_HOST": "",          # approval lookup disabled → always False
        "AGENT_ID_HEADER": AGENT_HEADER,
        **env,
    })
    return load_module("cedar-shim/main.py", f"cedar_shim_{name}")


def call(base, path="/v1/chat/completions", body=None, headers=None):
    return httpx.post(
        f"{base}{path}",
        json=body if body is not None else {"model": "llama3"},
        headers={AGENT_HEADER: "research-assistant", **(headers or {})},
        timeout=10,
    )


# --- the reason this component exists -----------------------------------------

def test_deny_with_http_200_becomes_403():
    """cedar-agent returns 200 carrying {"decision":"Deny"}. agentgateway would
    read that 2xx as ALLOW. The shim must convert it to 403."""
    with MockBackend(cedar_routes("Deny")) as cedar:
        module = boot(cedar.url, "deny")
        base, stop = serve(module)
        try:
            r = call(base)
        finally:
            stop()
    assert r.status_code == 403, "a Cedar Deny was allowed through"


def test_allow_becomes_200():
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "allow")
        base, stop = serve(module)
        try:
            r = call(base)
        finally:
            stop()
    assert r.status_code == 200


# --- identity -----------------------------------------------------------------

def test_missing_agent_identity_is_denied():
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "noident")
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/v1/chat/completions", json={"model": "llama3"}, timeout=10)
        finally:
            stop()
    assert r.status_code == 403
    assert not cedar.calls, "cedar-agent was queried without an identity"


def test_principal_sent_to_cedar_comes_from_the_gateway_header():
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "principal")
        base, stop = serve(module)
        try:
            call(base, headers={AGENT_HEADER: "pii-handler"})
        finally:
            stop()
    assert cedar.calls[0]["body"]["principal"] == 'AgentMesh::Agent::"pii-handler"'


# --- request classification ---------------------------------------------------

def test_model_call_is_classified_from_the_body():
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "classify_model")
        base, stop = serve(module)
        try:
            call(base, body={"model": "claude-opus-5", "messages": []})
        finally:
            stop()
    sent = cedar.calls[0]["body"]
    assert sent["action"] == 'AgentMesh::Action::"call_model"'
    assert sent["resource"] == 'AgentMesh::Model::"claude-opus-5"'


def test_tool_call_is_classified_from_jsonrpc_params():
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "classify_tool")
        base, stop = serve(module)
        try:
            call(base, path="/mcp", body={"params": {"name": "drop_table"}})
        finally:
            stop()
    sent = cedar.calls[0]["body"]
    assert sent["action"] == 'AgentMesh::Action::"call_tool"'
    assert sent["resource"] == 'AgentMesh::Tool::"drop_table"'


@pytest.mark.parametrize("path,body", [
    ("/v1/chat/completions", {}),            # no model named
    ("/mcp", {}),                            # no tool named
    ("/something/unknown", {"model": "x"}),  # unrecognised route
])
def test_unclassifiable_requests_are_denied(path, body):
    """If the request cannot be mapped to a Cedar resource, it cannot be
    authorized -- and an unauthorized request must not proceed."""
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "unclassifiable")
        base, stop = serve(module)
        try:
            r = call(base, path=path, body=body)
        finally:
            stop()
    assert r.status_code == 403
    assert not cedar.calls


# --- no self-approval ---------------------------------------------------------

def test_agent_cannot_self_approve_via_headers():
    """An agent controls its own headers. Approval must come from Temporal, so
    anything it sets about approval has to be inert."""
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "selfapprove")
        base, stop = serve(module)
        try:
            call(base, headers={
                "x-agentmesh-hitl-approved": "true",
                "hitl_approved": "true",
                "x-agentmesh-hitl-workflow": "a-workflow-it-never-got-approved",
            })
        finally:
            stop()
    # TEMPORAL_HOST is unset, so the lookup yields False regardless of headers.
    assert cedar.calls[0]["body"]["context"]["hitl_approved"] is False


def test_approval_flag_is_not_taken_from_the_request_body():
    with MockBackend(cedar_routes("Allow")) as cedar:
        module = boot(cedar.url, "bodyapprove")
        base, stop = serve(module)
        try:
            call(base, body={"model": "llama3", "hitl_approved": True,
                             "context": {"hitl_approved": True}})
        finally:
            stop()
    assert cedar.calls[0]["body"]["context"]["hitl_approved"] is False


# --- fail closed --------------------------------------------------------------

def test_cedar_agent_unreachable_is_denied():
    module = boot("http://127.0.0.1:1", "unreachable")
    base, stop = serve(module)
    try:
        r = call(base)
    finally:
        stop()
    assert r.status_code == 403
    assert "unreachable" in r.json()["reason"]


def test_cedar_agent_error_status_is_denied():
    with MockBackend(cedar_routes("Allow", status=500)) as cedar:
        module = boot(cedar.url, "cedar500")
        base, stop = serve(module)
        try:
            r = call(base)
        finally:
            stop()
    assert r.status_code == 403


def test_unrecognised_decision_value_is_denied():
    with MockBackend(cedar_routes("Maybe")) as cedar:
        module = boot(cedar.url, "weird")
        base, stop = serve(module)
        try:
            r = call(base)
        finally:
            stop()
    assert r.status_code == 403
