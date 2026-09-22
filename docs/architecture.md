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
 arctl publish  ──▶  agentregistry
                     · version, publisher verification
                     · enrichment score
                     · APPROVAL WORKFLOW ──▶ approved artifact
                                                   │
                     grant roles                   ▼
                     PUT /v1/data          cedar-agent
                     PUT /v1/policies      · principal = agent identity
                                           · action    = call_model | call_tool
                                           · resource  = model | tool
                                                   │
                     issue credential ──▶ agentgateway
                     add MCP servers  ──▶ agentgateway mcp.targets
```

The registry is the source of truth for what is approved; Cedar entity data is
derived from it. Nothing else creates a usable identity.

## Runtime — one call, end to end

```
agent
  │  POST :4000/v1/chat/completions   {model, messages}
  │  Authorization: Bearer <credential>
  ▼
agentgateway
  1. AUTHN       JWT / API key                        → 401 if absent or invalid
  2. AUTHZ       extAuthz(HTTP) → cedar-shim
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
OTel Collector ──(ZDR transform drops prompt bodies)──▶ Langfuse
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

| Dependency down | Result |
|---|---|
| cedar-agent unreachable | shim returns 403 — denied |
| Presidio unreachable | adapter returns Reject — denied |
| Agent Control unreachable | engine default is `deny` |
| Cedar policy set empty (failed load) | Cedar is deny-by-default — everything denied |
| Langfuse unreachable | traces lost; traffic unaffected (observability is not in the path) |

The only component whose failure does not block traffic is the one that must
not: observability.
