# Architecture

AgentMesh is five planes, each a separate open-source product, joined by
configuration.

| Plane | Product | Owns |
|---|---|---|
| **Catalogue** | agentregistry | What agents, MCP servers, skills and prompts exist; versions; publishers; **approval** |
| **Identity & policy** | cedar-agent | Cedar schema, policies, entity data — which agent may do what |
| **Data path** | agentgateway | Every LLM, MCP and tool call: authn, authz, guardrails, routing, telemetry |
| **Agent-step policy** | Agent Control | Controls inside the agent's own execution, pre and post each step |
| **Orchestration** | Temporal | Durable multi-step work, saga compensation, human approval gates |

Supported by **Presidio** (PII detection), **Langfuse** (traces, tokens, cost)
and whichever **LLM providers** are configured.

---

## Design time — onboarding an agent

```
 developer                 operator                       platform
 ─────────                 ────────                       ────────
 arctl publish  ┄┄▶  agentregistry                    (optional — not running
                     · version, publisher verification     on this deployment)
                     · APPROVAL WORKFLOW ┄┄▶ approved artifact
                                                   ┆
                     register principal             ▼
                     + model resources      cedar-agent
                     cedar/entities.json    · principal = agent identity
                     cedar/policies/*.cedar · action    = call_model
                       ──▶ cedar-loader                  | call_tool
                                           ·             | list_models
                                           · resource  = model | tool
                                                   │        | platform
                     issue credential ──▶ agentgateway
                       agent_token.py       (JWT `sub` = the principal id)
                     add MCP servers  ──▶ agentgateway mcp.targets
```

Solid arrows are what runs today; the dashed registry path is the intended
source of truth for `registry_status` once agentregistry is up. Until then the
attribute is set by hand, and **Cedar is what creates a usable identity** —
without a principal and a matching credential, an agent gets nothing.

The step-by-step procedure is
[Onboarding an agent](agent-onboarding-guide.md).

## Runtime — one call, end to end

```
agent
  │  POST :4000/v1/chat/completions   {model, messages}
  │  Authorization: Bearer <credential>
  ▼
agentgateway
  1. AUTHN       jwtAuth, mode: strict                → 401 if absent or invalid
                 the token's `sub` IS the agent identity
  2. AUTHZ       extAuthz(HTTP) → cedar-shim
                    gateway forwards ONLY `authorization` + `host`, and
                    injects  x-agentmesh-agent: jwt.sub  (a CEL expression)
                    — so a client-set identity header never arrives
                    → cedar-agent POST /v1/is_authorized
                    → {"decision":"Allow"|"Deny"}     → shim maps to 200 | 403
  3. GUARDRAILS  a. regex prompt guards — mask SSN / card / email inline
                 b. webhook → presidio-adapter → Presidio /analyze
                    NER hit that regex missed         → REJECT 403
  4. ROUTE       llm.models[] — model name selects the backend
                 (openAI | anthropic | azure | bedrock | vertex | gemini |
                  ollama | vLLM | …), with aliasing, failover, load balancing
  ▼
 the configured LLM backend ──▶ completion
  ▼
  5. GUARDRAILS  the same two checks over the response
  6. TELEMETRY   OTel span: tokens, model, session; log: realized cost
  ▼
OTel Collector ──(allow-list: only named attributes survive)──▶ Langfuse
  ▼
Langfuse — trace grouped by session, with tokens and cost
```

**A tool or MCP call is the same path.** Steps 1, 2, 3, 5 and 6 are identical;
only step 4 routes to an `mcp.targets` entry instead of an LLM backend. That
uniformity is the entire reason for a single gateway: one authentication, one
authorization, one guardrail pipeline, one telemetry stream — regardless of what
the agent is reaching for.

## Inside the agent

```
@control()-decorated step
   │ pre   ──▶ Agent Control ──▶ evaluators (regex | list | json | sql | luna2)
   │                              → deny | steer | observe
   ▼ post  ──▶ the same evaluation over the output
```

Agent Control sees what the gateway cannot: *which step* of the agent's own
logic is running, and what it is passing between steps. Controls are edited in
its UI and take effect immediately, with no redeploy.

It has no proxy mode — enforcement is via the SDK decorator only. That is why it
complements the gateway rather than replacing it.

## Admission control: why an unregistered agent is inert

This deserves stating plainly, because it is the part a registry alone cannot
deliver: **a registry is a catalogue with an approval workflow. It records
intent. It cannot stop a process from starting on a host.**

Enforcement comes from the choke point. Because every call goes through the
gateway:

| Attempt | What stops it | Where |
|---|---|---|
| No credential | 401 | agentgateway authn |
| Stolen credential, no Cedar principal | Deny → 403 | cedar-agent, policy 1 |
| Registered but not yet approved | Deny → 403 | cedar-agent, policy 1 |
| No role for that model tier | Deny → 403 | cedar-agent, policy 3 |
| Clearance below the tool's risk | Deny → 403 | cedar-agent, policy 4 |
| Destructive tool without approval | Deny → 403 | cedar-agent, policy 5 |
| MCP server not in `mcp.targets` | no route | agentgateway |
| Prompt contains PII | masked or rejected | guardrails |

An unapproved agent can run — and gets no model, no tools, no data. Combined
with the registry's inventory and Langfuse's per-session traces, both halves are
answerable: *what is approved*, and *what is actually happening*.

## Cedar, and why it sits behind external authorization

agentgateway's native policy engine is **CEL**, not Cedar. Rather than rewrite
the authorization model in CEL, Cedar is preserved by delegating: agentgateway's
`extAuthz` policy calls out, and cedar-agent — a real Cedar PDP — decides.

That delegation needs one translation. agentgateway's HTTP extAuthz mode treats
**any 2xx response as allow**, while cedar-agent returns HTTP 200 for *both*
Allow and Deny with the verdict in the body. `cedar-shim` reads the verdict and
maps it onto the status code agentgateway expects. Without it, every request
would be permitted — which is what `tests/adapters/test_cedar_shim.py` guards.

## Fail-closed behaviour

Measured by stopping each container and issuing a request, not inferred:

| Dependency down | Observed |
|---|---|
| `cedar-agent` | `403 external authorization failed` |
| `cedar-shim` | `403 external authorization failed` |
| `presidio-analyzer` or `-adapter` | `503 … prompt guard failed` |
| Cedar policy set empty (failed load, or a cedar-agent restart) | `403 cedar:deny []` — deny-by-default |
| Agent Control unreachable | engine default is `deny` (not measured; not running here) |
| `otel-collector` | **`200`** — traffic unaffected, telemetry silently lost |
| Langfuse | **`200`** — collector logs an export failure per batch |
| `temporal`, `orchestrator`, `remote-runner` | **`200`** — workflows stall, LLM calls unaffected |

The only components whose failure does not block traffic are the ones that must
not: observability and orchestration. Everything on the authorization and PII
path fails closed.

Per-container detail is in [The containers](containers.md).
