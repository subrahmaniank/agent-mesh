"""Cedar IAM policy tests.

These run the real Cedar engine over the shipped `cedar/policies/*.cedar` and
`cedar/entities.json` — the same files cedar-agent loads at runtime. They need
no Docker, so the authorization logic stays verifiable even when the stack is
not running.

The policies live one-per-file because cedar-agent accepts only one statement
per policy entry; concatenating them here is exactly what the engine evaluates
as a set, so the tests still see the real interaction between permits and
forbids.

Cedar SKIPS a policy that errors at evaluation time rather than failing the
request, so a malformed rule silently disappears and the request is allowed by
whatever `permit` remains. Every case therefore asserts `errors == []`.
"""

import json
from pathlib import Path

import cedarpy
import pytest

ROOT = Path(__file__).resolve().parents[2]
POLICY_FILES = sorted((ROOT / "cedar" / "policies").glob("*.cedar"))
assert POLICY_FILES, "no policy files found in cedar/policies/"
POLICIES = "\n".join(f.read_text() for f in POLICY_FILES)

_raw = json.loads((ROOT / "cedar" / "entities.json").read_text())
# Strip documentation-only keys before handing entities to the engine.
ENTITIES = [
    {**e, "attrs": {k: v for k, v in e["attrs"].items() if not k.startswith("_")}}
    for e in _raw
]

RESOURCE_TYPE = {"call_model": "Model", "call_tool": "Tool", "list_models": "Platform"}


def decide(agent: str, action: str, resource: str, hitl_approved: bool = False):
    request = {
        "principal": f'AgentMesh::Agent::"{agent}"',
        "action": f'AgentMesh::Action::"{action}"',
        "resource": f'AgentMesh::{RESOURCE_TYPE[action]}::"{resource}"',
        "context": {"hitl_approved": hitl_approved},
    }
    result = cedarpy.is_authorized(request, POLICIES, ENTITIES)
    return (
        str(result.decision).rsplit(".", 1)[-1],
        list(result.diagnostics.errors or []),
    )


def allow(*args, **kwargs):
    verdict, errors = decide(*args, **kwargs)
    assert errors == [], f"Cedar evaluation errors: {errors}"
    return verdict


# --- registry admission -------------------------------------------------------

def test_unapproved_agent_cannot_call_a_model():
    """The policy that turns the registry from a catalogue into enforcement.
    This agent has the highest clearance in the fixture and still gets nothing."""
    assert allow("unapproved-agent", "call_model", "llama3") == "Deny"


def test_unapproved_agent_cannot_call_a_tool():
    assert allow("unapproved-agent", "call_tool", "search_docs") == "Deny"


def test_approved_agent_can_call_a_model():
    assert allow("research-assistant", "call_model", "llama3") == "Allow"


# --- model tiers: keeping sensitive agents on-premises -------------------------

def test_agent_granted_hosted_tier_reaches_a_hosted_model():
    assert allow("research-assistant", "call_model", "gpt-4.1") == "Allow"


def test_local_only_agent_is_confined_to_local_models():
    """pii-handler is pinned to on-premises inference: it may use llama3 but
    not a hosted provider, whatever model name it asks for."""
    assert allow("pii-handler", "call_model", "llama3") == "Allow"
    assert allow("pii-handler", "call_model", "claude-opus-5") == "Deny"
    assert allow("pii-handler", "call_model", "gpt-4.1") == "Deny"


# --- tool clearance -----------------------------------------------------------

@pytest.mark.parametrize(
    "agent,tool,expected",
    [
        ("research-assistant", "search_docs", "Allow"),     # clearance 3 ≥ risk 1
        ("research-assistant", "query_database", "Allow"),  # clearance 3 ≥ risk 3
        ("pii-handler", "query_database", "Deny"),          # clearance 2 < risk 3
    ],
)
def test_tool_access_follows_clearance(agent, tool, expected):
    assert allow(agent, "call_tool", tool) == expected


# --- the HITL gate ------------------------------------------------------------
# Exercised with ops-agent (clearance 5) specifically so the *only* thing
# standing between it and the destructive tool is the approval flag. Testing
# this with a lower-clearance agent would pass for the wrong reason.

def test_destructive_tool_denied_without_approval():
    assert allow("ops-agent", "call_tool", "drop_table", hitl_approved=False) == "Deny"


def test_destructive_tool_allowed_with_verified_approval():
    assert allow("ops-agent", "call_tool", "drop_table", hitl_approved=True) == "Allow"


def test_approval_does_not_override_insufficient_clearance():
    """forbid-beats-permit must not be confused with permit-by-approval: an
    approval flag cannot manufacture clearance the agent does not have."""
    assert allow("research-assistant", "call_tool", "drop_table", hitl_approved=True) == "Deny"


# --- tenant isolation ---------------------------------------------------------

def test_cross_tenant_access_is_denied():
    entities = ENTITIES + [
        {
            "uid": {"type": "AgentMesh::Model", "id": "other-tenant-model"},
            "attrs": {"provider": "ollama", "tier": "local", "tenant": "globex"},
            "parents": [],
        }
    ]
    request = {
        "principal": 'AgentMesh::Agent::"research-assistant"',
        "action": 'AgentMesh::Action::"call_model"',
        "resource": 'AgentMesh::Model::"other-tenant-model"',
        "context": {"hitl_approved": False},
    }
    result = cedarpy.is_authorized(request, POLICIES, entities)
    assert list(result.diagnostics.errors or []) == []
    assert str(result.decision).rsplit(".", 1)[-1] == "Deny"


# --- the guard that catches a silently-skipped rule ----------------------------

def test_no_policy_evaluation_errors_anywhere():
    """A rule that raises is skipped by Cedar, not surfaced. Sweep the matrix."""
    for agent in ("research-assistant", "pii-handler", "ops-agent", "unapproved-agent"):
        for model in ("llama3", "gpt-4.1", "claude-opus-5"):
            assert decide(agent, "call_model", model)[1] == []
        for tool in ("search_docs", "query_database", "drop_table"):
            for hitl in (True, False):
                assert decide(agent, "call_tool", tool, hitl)[1] == []


# --- onboarded frameworks -----------------------------------------------------

def test_crewai_sample_reaches_the_local_model_it_actually_asks_for():
    """CrewAI sends the model name verbatim, tag included. `gemma4` and
    `gemma4:latest` are different Cedar resources, and only the registered
    spelling is permitted -- which is the mechanism that stops an agent using a
    model nobody approved."""
    assert allow("crewai-sample-01", "call_model", "gemma4:latest") == "Allow"


def test_crewai_sample_cannot_reach_a_hosted_model():
    """The containment the PII story leans on. Name-level NER is deliberately
    not in PRESIDIO_ENTITIES because it cannot survive a multi-turn agent, so
    what actually keeps this crew's accumulated research context on-premises is
    this denial -- see presidio-adapter/main.py."""
    assert allow("crewai-sample-01", "call_model", "gpt-4.1") == "Deny"


def test_crewai_sample_has_no_tool_role():
    """The crew attaches no tools; custom_tool.py is an unused scaffold. Grant
    `tool-user` when one is actually wired, not before."""
    assert allow("crewai-sample-01", "call_tool", "search_docs") == "Deny"


# --- model discovery ----------------------------------------------------------

def test_approved_agent_may_list_models():
    """GET /v1/models is a standard OpenAI endpoint that clients probe before
    calling. It is authorized rather than waved through, but it must succeed for
    an agent that is allowed to call models at all."""
    assert allow("research-assistant", "list_models", "gateway") == "Allow"


def test_unapproved_agent_may_not_list_models():
    """Discovery is still discovery: an agent the registry has not approved does
    not get to read the catalogue of what it cannot call."""
    assert allow("unapproved-agent", "list_models", "gateway") == "Deny"


def test_listing_models_requires_the_model_user_role():
    """`orchestrator` holds model-user so it may list; the grant is by role, not
    by being approved. A tool-only agent has no reason to enumerate models."""
    assert allow("orchestrator", "list_models", "gateway") == "Allow"
