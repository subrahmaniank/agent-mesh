# Framework integrations

Any framework works, because the integration point is an ordinary
OpenAI-compatible HTTP endpoint. There is no platform SDK to adopt and no
library to import.

This page covers the wiring. For the platform-side work — registering the
agent, granting it policy, issuing its credential — see
[Onboarding an agent](agent-onboarding-guide.md). Do that first; the settings
below are useless without a token.

---

## The general shape

Three settings, whatever your framework calls them:

```
base_url = http://localhost:${GATEWAY_PORT:-4000}/v1   # /v1 is required
api_key  = <the agent's JWT>                           # not a provider key
model    = <a name registered in cedar/entities.json>
```

That is the whole integration. Authentication, Cedar authorization, PII
guardrails, provider routing and token/cost telemetry all happen behind it.

> On this machine `GATEWAY_PORT=4100`. Check `grep GATEWAY_PORT .env`.

## Find out what your framework actually sends

The model name is authorized as an exact string, and **SDKs rewrite it**. Some
add a provider prefix, some strip one, some substitute a default you never
configured. Do not assume; ask the platform.

Make one call with anything configured, then:

```bash
docker compose logs --tail=20 cedar-shim | grep <your-agent-id>
```

```
cedar-shim DENY agent=my-agent action=call_model resource=gemma4:latest
           hitl=False (cedar:deny [])
```

`resource=` is the exact string that arrived on the wire. cedar-shim logs it
**even when it denies**, so the first failed call tells you precisely what to
register. This works the same for every framework and is faster than reading
any SDK's source.

`GET /v1/models` is *not* the way to do this: it returns the gateway's routing
patterns (`gpt-*`, `anthropic/*`, `*`), not concrete model names.

---

## Quirks worth knowing

| Framework | Watch out for |
|---|---|
| **CrewAI 1.9+** | **No provider prefix.** `openai/gemma4:latest` fails — see below |
| **LangChain / LangGraph** | `ChatOpenAI` sends `model` verbatim; some flows probe `GET /v1/models`, which is allowed for `model-user` agents |
| **AutoGen** | `base_url` must include `/v1`; omitting it 404s |
| **openai SDK (raw)** | `OpenAI(base_url=..., api_key=...)` — the `/v1` belongs in `base_url` |
| **Anything with a "summariser" or embedding step** | Register that model too, or one internal step 403s and the failure looks intermittent |

### CrewAI: no provider prefix

CrewAI 1.9 made LiteLLM optional and resolves providers natively. The two paths
behave differently:

```python
model="openai/gemma4:latest"   # looked up in CrewAI's hardcoded OpenAI model
                               # constants, misses, falls through to LiteLLM:
                               #   ImportError: Fallback to LiteLLM is not available

model="gemma4:latest"          # _infer_provider_from_model() finds no match and
                               # defaults to "openai", so the native client is
                               # used with OPENAI_BASE_URL.  This one works.
```

Older CrewAI releases, which always routed through LiteLLM, needed the prefix —
which is why the prefixed form appears in a lot of documentation, including an
earlier version of this page.

---

## Examples

All of these assume `GATEWAY_URL` and `AGENT_TOKEN` are in the environment.

### LangChain / LangGraph

```python
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url=os.environ["GATEWAY_URL"],      # http://localhost:4100/v1
    api_key=os.environ["AGENT_TOKEN"],
    model="gemma4:latest",                   # registered in cedar/entities.json
)
```

Every node in a LangGraph graph that holds this `llm` is governed. Nodes that
construct their own client are not — that is the thing to check when a graph
partly works.

### CrewAI

```python
import os
from crewai import Agent, LLM

llm = LLM(
    model="gemma4:latest",                   # no provider prefix — see above
    base_url=os.environ["GATEWAY_URL"],
    api_key=os.environ["AGENT_TOKEN"],
)
researcher = Agent(role="Researcher", goal="...", llm=llm)
```

Or entirely through the environment, with no code change at all:

```bash
OPENAI_BASE_URL=http://localhost:4100/v1
OPENAI_API_KEY=<the JWT>
MODEL=gemma4:latest
OPENAI_MODEL_NAME=gemma4:latest
```

### AutoGen

```python
config_list = [{
    "model": "gemma4:latest",
    "base_url": os.environ["GATEWAY_URL"],
    "api_key": os.environ["AGENT_TOKEN"],
}]
```

### Raw openai SDK

```python
from openai import OpenAI

client = OpenAI(base_url=os.environ["GATEWAY_URL"],
                api_key=os.environ["AGENT_TOKEN"])
client.chat.completions.create(model="gemma4:latest", messages=[...])
```

### Plain HTTP

```python
import httpx

r = httpx.post(
    f"{GATEWAY_URL}/chat/completions",
    headers={"Authorization": f"Bearer {AGENT_TOKEN}"},
    json={"model": "gemma4:latest",
          "messages": [{"role": "user", "content": "..."}]},
)
if r.status_code in (401, 403):
    ...   # authn, Cedar or a guardrail refused — the body says which
```

### From a Temporal workflow

Use the `call_model` / `call_gateway_tool` activities in
`orchestrator/activities/gateway_client.py`. They map a policy denial to a
**non-retryable** error, because retrying cannot change a policy decision and
would otherwise burn the whole retry budget.

---

## What not to send

- **An identity header.** `x-agentmesh-agent` is set by the gateway from the
  validated token; a client-set one never reaches the authorization service.
- **`hitl_approved`** or any other authorization claim. Approval is read from
  the Temporal workflow, never from the request body.
- **A provider API key.** Provider credentials live in the gateway. If your
  agent still holds one, it has a second egress path that nothing governs.

---

## Streaming

`stream: true` works and is guarded — `guardrails.streaming: Enabled` in
`agentgateway/config.yaml` applies the checks to streamed responses. Without
that setting streaming would be an unscreened path out of the platform, so do
not remove it.

Note that the regex `mask` action does not apply to streamed responses; masking
of well-formed identifiers happens on the request side.

---

## Switching providers

`model` names an entry in `agentgateway/config.yaml`, not a provider. Moving an
agent from a local model to a hosted one — or between clouds — is a change to
that file, or to which model name the agent asks for. Framework code does not
change, and the governance travels with it: guardrails, Cedar authorization and
telemetry apply to every backend.

Whether an agent *may* use a given model is a Cedar decision, not a
configuration detail. An agent granted only the `local` tier is denied hosted
providers no matter what it passes as `model` — which is what makes
"this agent's data stays on-premises" an enforceable statement rather than a
convention.

---

## Step-level governance

Framework integration covers the wire — every call in and out. To govern the
agent's *internal* steps, add Agent Control's decorator to the functions you
want checked; see
[Onboarding an agent](agent-onboarding-guide.md). Agent Control ships
integrations for LangChain, LangGraph, CrewAI, Google ADK and AWS Strands.

> Agent Control is configured but not running on this deployment.

## MCP tools

Tools reach agents through agentgateway's MCP gateway rather than being wired
into each framework separately, so the same authorization and guardrails apply.
Register the server in `mcp.targets`, give it a Cedar `Tool` entity, and grant
the agent `tool-user` — see
[Onboarding an agent](agent-onboarding-guide.md).
