# AgentMesh

An agent platform **assembled from open-source products**, not written from
scratch. Agents are catalogued and approved in a registry; every call they make
— LLM, MCP or tool — goes through one gateway that authenticates them,
authorizes with Cedar, screens for PII, and records tokens and cost.

> ### ⚠️ Status: configured, not yet run
> The stack below has **not been started** — no Docker daemon was available in
> the environment where it was assembled. Configuration shapes came from vendor
> documentation and are marked `[check]` where inferred. What *has* been
> verified, with tests you can run right now, is in
> [What is actually verified](#what-is-actually-verified).
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
               └─ 6. telemetry    OTel ─▶ collector (ZDR) ─▶ Langfuse :3000
                                          tokens + cost per session

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

## Any LLM backend, by configuration

Clients always speak one OpenAI-compatible API to `:4000`. Which backend serves
a request is decided in `agentgateway/config.yaml` by model name — agents never
change:

```yaml
llm:
  models:
    - { name: "llama3*",  provider: ollama, params: { host: "ollama:11434" } }
    - { name: "gpt-*",    provider: openAI, params: { apiKey: "$OPENAI_API_KEY" } }
    - { name: "claude-*", provider: azure,  params: { azureResourceType: foundry } }
```

agentgateway supports 20+ providers natively — OpenAI, Anthropic, Azure (OpenAI
*and* AI Foundry), Bedrock, Gemini, Vertex, xAI, Cohere, Mistral, DeepSeek,
Groq, Together, Fireworks, OpenRouter, HuggingFace and more — plus self-hosted
Ollama, vLLM and LM Studio, and any OpenAI-compatible endpoint. Guardrails,
authorization and telemetry apply to **every** entry, so adding a provider never
widens the trust boundary.

**The default backend is a local Ollama, so the platform runs with no cloud
credentials at all.**

## Why an unregistered agent is inert

A registry records intent; it cannot stop a process from starting. Enforcement
is the choke point:

| Attempt | Stopped by |
|---|---|
| No credential | agentgateway → 401 |
| Stolen credential, no Cedar principal | cedar-agent → Deny → 403 |
| Registered but no role for that model | Cedar → Deny → 403 |
| MCP server not in `mcp.targets` | no route |
| Prompt contains PII | masked, or rejected |

An unapproved agent can run — and gets no model, no tools, no data.

## Quickstart

```bash
cp .env.example .env          # every value optional; Ollama needs none

docker compose up -d          # gateway, cedar, presidio, ollama, temporal, otel
./scripts/up-vendor-stacks.sh # agentregistry, Agent Control, Langfuse

docker exec agentmesh_ollama ollama pull llama3

curl -X POST localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${GATEWAY_TOKEN:-dev-operator-token}" \
  -d '{"model":"llama3","messages":[{"role":"user","content":"hello"}]}'
```

| UI | URL |
|---|---|
| agentgateway admin + LLM playground | http://localhost:15000 |
| agentregistry | http://localhost:12121 |
| Agent Control | http://localhost:4001 |
| Langfuse | http://localhost:3000 |
| Temporal | http://localhost:8233 |

Full walkthrough: [`docs/getting-started.md`](docs/getting-started.md).

## Repository layout

```
agentgateway/config.yaml     the substance: backends, guardrails, extAuthz, MCP
cedar/                       policies.cedar + entities.json (real Cedar)
cedar-shim/                  extAuthz → Cedar decision            [code]
presidio-adapter/            guardrail webhook → Presidio         [code]
orchestrator/                Temporal workflows and activities    [code]
agentcontrol/controls/       step-level controls (JSON)
registry/                    how agents get published and approved
otel/                        ZDR transform + Langfuse exporter
scripts/                     cedar loader, vendor stack launcher
tests/                       cedar policies, adapters, Temporal workflows
```

Three things are code; everything else is configuration.

## What is actually verified

`uv run pytest tests/ -q` → **46 passing**, no Docker required:

| Suite | Proves |
|---|---|
| `tests/cedar/` (13) | The shipped Cedar policies under the real engine: unapproved agents denied, `pii-handler` confined to local models, clearance enforced, the HITL gate flipping deny→allow, tenant isolation, and **zero policy evaluation errors** — Cedar silently skips a rule that raises, so a malformed policy would otherwise vanish unnoticed |
| `tests/adapters/test_cedar_shim.py` (14) | A Cedar `Deny` returned with HTTP 200 becomes a **403** — without this translation every request would be allowed; plus fail-closed on an unreachable PDP, and that an agent cannot self-approve via headers or body |
| `tests/adapters/test_presidio_adapter.py` (11) | The agentgateway webhook contract, NER-only entities caught where regex would miss, and **fail-closed when Presidio is down** |
| `tests/workflows/` (8) | Temporal saga: LIFO compensation, policy denials not retried, partial-compensation recovery, approval signal, SLA timeout |

Not verified: anything needing a running container — gateway config shapes, the
cedar-agent wire format, the vendor compose files, and the end-to-end request
path. Those are the first things to exercise once Docker is available; see the
[first-run checklist](docs/getting-started.md#first-run-checklist).

## Documentation

| Guide | |
|---|---|
| [Current state](docs/current-state.md) | What changed from the custom build, and what is not yet wired |
| [Architecture](docs/architecture.md) | The five planes, request walkthrough, admission control |
| [Security & governance](docs/security-and-governance.md) | Cedar IAM, the two PII layers and their limits, fail-closed behaviour |
| [Getting started](docs/getting-started.md) | Setup, first run, verification |
| [Agent onboarding](docs/agent-onboarding-guide.md) | Publish, approve, grant roles, issue a credential |
| [Observability](docs/observability.md) | Langfuse wiring, tokens and cost |
| [Orchestration & HITL](docs/orchestration-and-hitl.md) | Temporal workflows and approval gates |
