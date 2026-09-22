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

---

# Worked example: onboarding a CrewAI project

`~/Developer/samples/crewai_sample_01` — a stock CrewAI crew, two agents, two
sequential tasks. It runs on AgentMesh with **no change to `src/`**: CrewAI
routes LLM calls through an OpenAI-compatible client, and agentgateway is an
OpenAI-compatible endpoint.

### 1. Register the principal

`cedar/entities.json`:

```json
{ "uid":   { "type": "AgentMesh::Agent", "id": "crewai-sample-01" },
  "attrs": { "registry_status": "approved", "tenant": "acme", "clearance": 2,
             "roles": ["model-user"], "allowed_model_tiers": ["local"] },
  "parents": [] }
```

`roles` omits `tool-user` because the crew attaches no tools. Grant it when one
is wired, not before.

Register the **model name the framework actually sends**, tag and all —
`gemma4:latest`, not `gemma4`. They are different Cedar resources.

```bash
docker compose restart cedar-loader && docker compose logs --tail=5 cedar-loader
```

### 2. Issue a credential

```bash
python3 scripts/agent_token.py issue crewai-sample-01 --ttl 604800
```

### 3. Point the crew at the gateway

Its `.env` — the whole integration:

```bash
OPENAI_BASE_URL=http://localhost:4100/v1   # GATEWAY_PORT
OPENAI_API_KEY=<the JWT>                   # not a provider key
MODEL=gemma4:latest
OPENAI_MODEL_NAME=gemma4:latest
```

```bash
crewai run     # or: uv run run_crew
```

## Three things that will bite you

**No provider prefix on the model name.** CrewAI 1.9 made LiteLLM optional and
resolves providers natively. `openai/gemma4:latest` is looked up in its
hardcoded OpenAI model constants, misses, and falls through to LiteLLM:

```
ImportError: Fallback to LiteLLM is not available
```

Bare `gemma4:latest` takes a different path — `_infer_provider_from_model()`
finds no match, defaults to `openai`, and uses the native client with
`OPENAI_BASE_URL`. That is the one that works.

**`uv` does not use the OS trust store.** Behind a TLS-inspecting proxy,
`uv sync` dies on `invalid peer certificate: UnknownIssuer`. Use
`UV_NATIVE_TLS=1 uv sync`.

**Name-level PII screening and multi-turn agents are incompatible.** A
sequential crew feeds each task's output into the next task's prompt, so a
single false positive is fatal — spaCy reading "MoE" as a person aborted a run
on its second task. `PERSON` is therefore not in `PRESIDIO_ENTITIES`; the
control that keeps this crew's context on-premises is
`allowed_model_tiers: ["local"]`. See
[Security & governance](security-and-governance.md).

## Confirming it was actually governed

```bash
docker compose logs cedar-shim | grep crewai-sample-01 | tail -5
# ALLOW agent=crewai-sample-01 action=call_model resource=gemma4:latest
#       (cedar:allow ['03-model-access-by-tier'])
```

One line per LLM call, and no other principal. A run of the sample crew produces
around 30.
