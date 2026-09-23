# AgentMesh

An agent platform **assembled from open-source products**, not written from
scratch. Agents are catalogued and approved in a registry; every call they make
— LLM, MCP or tool — goes through one gateway that authenticates them,
authorizes with Cedar, screens for PII, and records tokens and cost.

> ### Status: running, end to end
> The stack starts with `docker compose up -d` and a real request has been
> served through the whole path — JWT authn → Cedar → PII guardrails → Ollama →
> completion with token counts. The authorization matrix, including a failed
> identity-forgery attempt, is reproduced in
> [What is verified](#what-is-verified).
>
> A previous iteration hand-wrote a Python substitute for agentgateway and
> labelled it "AgentGateway.dev". That implementation is preserved on the
> `custom-python-gateway` branch — see [`docs/current-state.md`](docs/current-state.md).

---

## Architecture

```
  agentregistry :12121          catalogue · versions · publishers · APPROVAL
        │ approved artifact → Cedar principal
        ▼
   agent ──▶ agentgateway :4000  (admin UI :15000)
               │
               ├─ 1. authn        JWT / API key
               ├─ 2. authz        extAuthz ─▶ cedar-shim ─▶ cedar-agent :8180
               ├─ 3. guardrails   regex mask  +  webhook ─▶ presidio-adapter ─▶ Presidio
               ├─ 4. route        OpenAI · Anthropic · Azure · Bedrock · Vertex
               │                  Gemini · Groq · Mistral · Ollama · vLLM · …
               ├─ 5. guardrails   same checks over the response
               └─ 6. telemetry    OTel ─▶ collector (allow-list) ─▶ Langfuse :3300
                                  request log ─▶ postgres ─▶ Analytics tab
                                          tokens + cost per agent

  Agent Control :8000 (UI :4001)  step-level controls inside the agent
  Temporal :7233 (UI :8233)       durable orchestration, sagas, HITL gates
```

A tool or MCP call follows the **identical** path; only step 4 differs. One
authn, one authz, one guardrail pipeline, one telemetry stream — whatever the
call type.

## What each product does

| Plane | Product | Owns |
|---|---|---|
| Catalogue | [agentregistry](https://aregistry.ai) | What exists, versions, publishers, approval |
| Identity & policy | [cedar-agent](https://github.com/permitio/cedar-agent) | Cedar schema, policies, entity data |
| Data path | [agentgateway](https://agentgateway.dev) | Every LLM/MCP/tool call |
| Agent-step policy | [Agent Control](https://agentcontrol.dev) | Controls inside the agent's execution |
| PII | [Microsoft Presidio](https://microsoft.github.io/presidio/) | NER-grade detection |
| Observability | [Langfuse](https://langfuse.com) | Traces, tokens, cost per session |
| Orchestration | [Temporal](https://temporal.io) | Durable workflows, sagas, approval gates |

## The containers

21 containers once everything is up. Full detail, including what each one's
failure actually does, is in [`docs/containers.md`](docs/containers.md).

| Container | Does | If it stops |
|---|---|---|
| `agentgateway` | every LLM/MCP/tool call: authn, authz, guardrails, routing, telemetry | everything |
| `ollama` *(profile)* | optional in-stack inference | `503` — authz already passed |
| `cedar-agent` | the Cedar PDP | `403` — **and a restart empties its policy store** |
| `cedar-shim` | turns a Cedar decision into 200/403 for extAuthz | `403`, fail closed |
| `cedar-loader` | one-shot: loads policies and entities, then exits 0 | nothing — but re-run it after editing `cedar/` |
| `presidio-analyzer` | NER detection | `503`, fail closed |
| `presidio-anonymizer` | masking | `200` — not in the default path |
| `presidio-adapter` | bridges the guardrail webhook to Presidio | `503`, fail closed |
| `otel-collector` | scrubs telemetry, forwards to Langfuse | `200` — **traffic fine, telemetry silently lost** |
| `agentgateway-postgres` | request log behind the Analytics tab | `200` running; blocks a cold start |
| `temporal` + `-postgres` + `-ui` | durable workflows, their store and browser | `200` — workflows stall |
| `orchestrator` | control-plane Temporal worker | `200` |
| `remote-runner` | agent-plane worker; outbound only, no inbound ports | `200` |
| Langfuse ×6 | traces, tokens and cost — started separately | `200`, export failures logged |

Every `200` is a design property, not an oversight: those containers are not in
the request path. Every value in that column was measured by stopping the
container and issuing a request, not inferred.

## Any LLM backend, by configuration

Clients always speak one OpenAI-compatible API to `:4000`. Which backend serves
a request is decided in `agentgateway/config.yaml` by model name — agents never
change:

```yaml
llm:
  models:
    - { name: "llama3*",  provider: ollama, params: { baseUrl: "$OLLAMA_BASE_URL" } }
    - { name: "gpt-*",    provider: openAI, params: { apiKey: "$OPENAI_API_KEY" } }
    - { name: "claude-*", provider: azure,  params: { azureResourceType: foundry } }
```

agentgateway supports 20+ providers natively — OpenAI, Anthropic, Azure (OpenAI
*and* AI Foundry), Bedrock, Gemini, Vertex, xAI, Cohere, Mistral, DeepSeek,
Groq, Together, Fireworks, OpenRouter, HuggingFace and more — plus self-hosted
Ollama, vLLM and LM Studio, and any OpenAI-compatible endpoint. Guardrails,
authorization and telemetry apply to **every** entry, so adding a provider never
widens the trust boundary.

**The default backend is your own Ollama — set `OLLAMA_BASE_URL` in `.env` to
wherever it runs — so the platform works with no cloud credentials at all.**

## Why an unregistered agent is inert

A registry records intent; it cannot stop a process from starting. Enforcement
is the choke point. Every row below was **observed against the running stack**:

| Attempt | Result |
|---|---|
| No credential | `401 authentication failure: no bearer token found` |
| Valid token, unapproved in registry | `403 cedar:deny ['01-registry-admission']` |
| `pii-handler` asking for a hosted model | `403` — pinned to local tier |
| Agent asking for an unregistered model name | `403` — Cedar has no such resource |
| **Forged `x-agentmesh-agent` header** | **ignored** — see below |
| Prompt with an SSN | masked inline — the model receives `<SSN>` |
| Prompt with an IBAN | `403 content contains IBAN_CODE` |

The forgery case is the one that matters. agentgateway forwards **only**
`authorization` and `host` to an HTTP extAuthz endpoint — a client's own headers
never reach the shim. Identity is injected by the gateway from the validated
token:

```yaml
extAuthz:
  protocol:
    http:
      addRequestHeaders:
        x-agentmesh-agent: jwt.sub     # CEL over the verified JWT
```

So an agent holding `pii-handler`'s token and sending
`x-agentmesh-agent: research-assistant` is still authorized as `pii-handler`.
An agent never asserts who it is.

## Quickstart

```bash
cp .env.example .env
# Point OLLAMA_BASE_URL at your Ollama server — full URL, including /v1:
#   OLLAMA_BASE_URL=http://192.168.1.20:11434/v1

python3 scripts/agent_token.py init   # one-time: dev signing key + JWKS

docker compose up -d          # gateway, cedar, presidio, temporal, otel
python3 scripts/vendor_stacks.py up   # Langfuse, Agent Control, agentregistry

# No Ollama of your own? Run one in-compose instead:
#   docker compose --profile local-llm up -d
#   OLLAMA_BASE_URL=http://ollama:11434/v1

TOKEN=$(python3 scripts/agent_token.py issue research-assistant)
curl -X POST localhost:${GATEWAY_PORT:-4000}/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"model":"llama3","messages":[{"role":"user","content":"hello"}]}'
```

Two things must be registered before a call succeeds, and that is the design:
the **agent** in `cedar/entities.json` (so it has a principal) and the **model
name you actually ask for**, tag included (so it has a resource). An
unregistered model is denied exactly like an unapproved agent.

Behind a TLS-inspecting proxy, drop your corporate root CA into `certs/` before
building — see [`certs/README.md`](certs/README.md).

| UI | URL | Status |
|---|---|---|
| agentgateway admin | http://localhost:15000 | **up** with `docker compose up -d` |
| Temporal | http://localhost:8233 | **up** with `docker compose up -d` |
| agentregistry | http://localhost:12121 | needs `python3 scripts/vendor_stacks.py up` |
| Agent Control | http://localhost:4001 | needs `python3 scripts/vendor_stacks.py up` |
| Langfuse | http://localhost:3300 | **up** via `python3 scripts/vendor_stacks.py up` (3000 is usually taken, hence 3300) |

The last three publish their own compose files and are **not** started by
`docker compose up -d`. Those files are committed under `vendor/`, pinned to
specific upstream commits, so bringing them up needs no network — see
[`vendor/VENDORED.md`](vendor/VENDORED.md).

Full walkthrough: [`docs/getting-started.md`](docs/getting-started.md).

## Onboarding an agent

Bringing your own agent onto the platform — any framework, or none — is four
things on the platform side and three settings on yours:

```bash
# platform: register the principal in cedar/entities.json, register the model
#           names it sends, reload, issue a credential
docker compose restart cedar-loader
TOKEN=$(python3 scripts/agent_token.py issue my-first-agent --ttl 604800)

# your agent: point it at the gateway
#   base_url = http://localhost:${GATEWAY_PORT:-4000}/v1
#   api_key  = $TOKEN          (not a provider key)
#   model    = a registered model name
```

Your agent's code does not change — it already speaks OpenAI-compatible HTTP.
The step-by-step procedure, with verification and a failure catalogue, is
[`docs/agent-onboarding-guide.md`](docs/agent-onboarding-guide.md); per-framework
wiring is [`docs/framework-integrations.md`](docs/framework-integrations.md).

## Repository layout

```
agentgateway/config.yaml     the substance: authn, backends, guardrails, extAuthz
cedar/policies/*.cedar       one file per policy — the filename is the policy id
cedar/entities.json          agents, models and tools Cedar knows about
cedar-shim/                  extAuthz → Cedar decision            [code]
presidio-adapter/            guardrail webhook → Presidio         [code]
orchestrator/                Temporal workflows and activities    [code]
agentcontrol/controls/       step-level controls (JSON)
registry/                    how agents get published and approved
otel/                        telemetry allow-list + Langfuse exporter
vendor/                      upstream compose files, pinned — see VENDORED.md
certs/                       corporate root CAs for TLS-inspecting proxies
auth/                        dev JWT signing key + JWKS (gitignored)
scripts/                     cedar loader, token issuer, vendor stacks (Python)
tests/                       cedar policies, adapters, Temporal workflows
```

Three things are code — `cedar-shim/`, `presidio-adapter/`, `orchestrator/` —
and everything else is configuration. The scripts are Python so they run on any
OS; `vendor/` is committed so the stack is reproducible without network access.

## What is verified

### Against the running stack

```
agent → JWT authn → Cedar → regex mask → Presidio NER → Ollama → completion
```

A prompt containing an SSN was masked in flight and still returned a completion
with `usage.total_tokens`; a prompt containing an IBAN was rejected before egress.
The full authorization matrix is the table above — every row observed, not
inferred.

Shapes confirmed by running `--validate-only` against agentgateway v1.5.0
rather than by reading docs:

| Thing | Finding |
|---|---|
| Guardrail regex builtins | exactly `ssn`, `creditCard`, `email`, `phoneNumber` |
| Regex actions | `mask` \| `reject` \| `audit`; rules take `{builtin}` or `{pattern}` — **no `name` field** |
| **Webhook actions** | `reject` \| `audit` — **no `mask`.** The vendor's Webhook API page lists Pass/Mask/Reject; this build does not implement Mask, so a webhook cannot redact |
| Webhook `failureMode` | `failClosed` \| `failOpen` — set `failClosed` or Presidio being down means PII flows |
| `guardrails.streaming` | `Enabled` \| `Disabled`; without `Enabled`, `stream: true` is an unguarded path |
| extAuthz forwarding | only `authorization` + `host`; body and identity must be requested explicitly |
| JWKS key types | RSA, EC, OKP[Ed25519] — symmetric keys rejected outright |
| Model name wildcards | only at the start or the end; `claude-*-direct` is a parse error |
| `ui:` block | has **no** `enabled` field; `ui: {enabled: true}` fails to parse |

Re-check any of it after an edit:

```bash
docker run --rm -v ./agentgateway:/c:ro cr.agentgateway.dev/agentgateway:v1.5.0 \
  -f /c/config.yaml --validate-only
```

### Without Docker

`uv run pytest tests/ -q` → **52 passing**:

| Suite | Proves |
|---|---|
| `tests/cedar/` (19) | The shipped Cedar policies under the real engine: unapproved agents denied, `pii-handler` confined to local models, clearance enforced, the HITL gate flipping deny→allow, tenant isolation, and **zero policy evaluation errors** — Cedar silently skips a rule that raises, so a malformed policy would otherwise vanish unnoticed |
| `tests/adapters/test_cedar_shim.py` (14) | A Cedar `Deny` returned with HTTP 200 becomes a **403** — without this translation every request would be allowed; plus fail-closed on an unreachable PDP, and that an agent cannot self-approve via headers or body |
| `tests/adapters/test_presidio_adapter.py` (11) | The agentgateway webhook contract, NER-only entities caught where regex would miss, and **fail-closed when Presidio is down** |
| `tests/workflows/` (8) | Temporal saga: LIFO compensation, policy denials not retried, partial-compensation recovery, approval signal, SLA timeout |

Still unverified: **agentregistry** and **Agent Control** are vendored and
pinned but not started — agentregistry needs a `VERSION` release tag nothing
here supplies — so the registry → Cedar identity hand-off is unexercised and
`registry_status` is set by hand. MCP targets are empty, so the tool path has
been proven through Cedar but not against a real MCP server. Windows is
untested: the tooling is OS-independent by construction (Python plus Docker),
but this was developed on Linux.

## Documentation

| Guide | |
|---|---|
| [The containers](docs/containers.md) | What each of the 21 containers does, and what its failure breaks |
| [Current state](docs/current-state.md) | What changed from the custom build, and what is not yet wired |
| [Architecture](docs/architecture.md) | The five planes, request walkthrough, admission control |
| [Security & governance](docs/security-and-governance.md) | Cedar IAM, the two PII layers and their limits, fail-closed behaviour |
| [Getting started](docs/getting-started.md) | Setup, first run, verification |
| [Agent onboarding](docs/agent-onboarding-guide.md) | **Start here to bring an agent on.** Register, grant, credential, verify, operate |
| [Observability](docs/observability.md) | Langfuse wiring, tokens and cost |
| [Orchestration & HITL](docs/orchestration-and-hitl.md) | Temporal workflows and approval gates |
