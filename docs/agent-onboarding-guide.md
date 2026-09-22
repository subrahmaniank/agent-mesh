# Onboarding an agent

Four steps: publish, approve, grant, credential. An agent that skips any of them
can still run — and can do nothing.

---

## 1. Publish to the registry

```bash
arctl init agent research-assistant
arctl publish --registry http://localhost:12121
```

agentregistry records the version, verifies the publisher and scores the
artifact. See [`registry/README.md`](../registry/README.md).

## 2. Approve it

In the agentregistry UI at `:12121`, or via its REST API. Until approved, the
first Cedar policy denies everything:

```cedar
forbid (principal, action, resource)
unless { principal.registry_status == "approved" };
```

## 3. Grant roles in Cedar

Approval in the registry does not by itself grant anything — the agent must also
exist as a Cedar principal. Add it to `cedar/entities.json`:

```json
{
  "uid": { "type": "AgentMesh::Agent", "id": "research-assistant" },
  "attrs": {
    "registry_status": "approved",
    "tenant": "acme",
    "clearance": 3,
    "roles": ["model-user", "tool-user"],
    "allowed_model_tiers": ["local", "hosted"]
  },
  "parents": []
}
```

| Attribute | Effect |
|---|---|
| `registry_status` | Anything but `approved` denies everything |
| `tenant` | Must match the model's or tool's tenant |
| `clearance` | Compared against a tool's `risk_score` |
| `roles` | `model-user` and/or `tool-user` |
| `allowed_model_tiers` | `["local"]` pins the agent to on-premises inference |

Verify, then load:

```bash
uv run pytest tests/cedar/ -q
docker compose up cedar-loader
```

Run the tests first: Cedar silently **skips** a policy that errors rather than
failing the request, so a malformed rule vanishes instead of announcing itself.

## 4. Issue a gateway credential

The credential is a signed JWT whose `sub` claim is the agent id from step 1 —
the same string Cedar knows as the principal:

```bash
python3 scripts/agent_token.py init          # once per machine
docker compose restart agentgateway          # pick up the JWKS

AGENT_TOKEN=$(python3 scripts/agent_token.py issue research-assistant)
```

The agent never asserts its own identity, and cannot. agentgateway validates the
signature, then injects `x-agentmesh-agent: jwt.sub` on the authorization call;
a client's own headers are not forwarded to extAuthz at all. Handing an agent a
token for a different `sub` is the only way to change who it is — which is the
point, because minting tokens is an operator action.

In production, replace the dev issuer with your IdP: point `jwtAuth.jwks.file`
at its key set. Nothing else in the config changes.

Also register the **model names** the agent will ask for, exactly as sent —
`gemma4:latest`, not `gemma4`. An unregistered model is denied the same way an
unapproved agent is.

---

## Calling through the gateway

One OpenAI-compatible endpoint, whatever the backend:

```python
import httpx

r = httpx.post(
    "http://localhost:4000/v1/chat/completions",
    headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
    json={"model": "llama3", "messages": [{"role": "user", "content": "..."}]},
)
if r.status_code in (401, 403):
    ...  # authn, Cedar, or a guardrail refused — the body says which
```

Do **not** send `hitl_approved`, an identity header, or any other authorization
claim. They are ignored, and sending them signals a misunderstanding of where
authorization happens.

From a Temporal workflow, use the `call_model` / `call_gateway_tool` activities
in `orchestrator/activities/gateway_client.py`, which handle the denial-to-
non-retryable-error mapping.

## Adding step-level controls

For policy inside the agent's own execution, decorate the functions you want
governed and manage the rules in the Agent Control UI:

```python
from agent_control import control, init

init(agent_name="research-assistant", description="...")

@control()
def fetch_customer_record(customer_id: str) -> dict:
    ...
```

Controls in `agentcontrol/controls/` are seeds; edit them in the dashboard at
`:4001` and they take effect immediately, with no redeploy.

## Registering a tool or MCP server

1. Publish and approve it in agentregistry.
2. Add it to `mcp.targets` in `agentgateway/config.yaml` — without a route it is
   unreachable.
3. Add a matching `AgentMesh::Tool` entity in `cedar/entities.json` with a
   `risk` and `risk_score` — without one, no `permit` matches and every call is
   denied.

Tools with `risk: "destructive"` additionally require a verified Temporal
approval, whatever the agent's clearance.
