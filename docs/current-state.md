# Current state

What this repo is today, what it replaced, and what is not yet wired.

---

## What it replaced

The previous implementation named real products in its diagrams and service
banner but shipped bespoke Python for nearly all of them. Its "gateway" was a
~700-line `http.server` that printed `AgentGateway.dev (PEP + Cedar PDP)
Running` at startup and returned `"service": "AgentGateway.dev"` from
`/healthz`. There was no agentgateway in the compose file — or anywhere else. A
reader, or an auditor, would reasonably have concluded the third-party product
was deployed.

The root cause was `implementation_plan.md`, which specified *building* a
substitute for a named product rather than running it.

| Capability | Was | Is now |
|---|---|---|
| Agent registry | none — `tools:`/`models:` blocks in a YAML file | agentregistry, with approval workflow |
| Gateway | custom Python `http.server` | agentgateway (Rust), 20+ LLM providers |
| Cedar IAM | `cedarpy` embedded in the gateway process | cedar-agent, a standalone PDP |
| Guardrails | a `.cedar` file needing a restart | Agent Control, editable at runtime |
| PII | custom DLP module calling Presidio | Presidio behind agentgateway's guardrail webhook |
| Observability | collector exporting to `debug` — no backend | Langfuse, tokens and cost per session |
| Token/cost tracking | not implemented | agentgateway metrics → Langfuse |
| MCP / tools | `_dispatch()` stub, no transport | agentgateway MCP gateway |
| LLM backends | one hand-written Azure Foundry client | any provider, by configuration |

The old implementation is preserved on the **`custom-python-gateway`** branch.
It was not bad code — it had real Cedar evaluation, real Presidio calls, real
Foundry egress and 109 passing tests — it was simply the wrong thing to build.

## What carried over

- **`orchestrator/`** — Temporal workflows are irreducibly code; Temporal has no
  no-code mode. The saga fixes from the audit came with them: LIFO unwind,
  per-item compensation guards, bounded rollback retries, `CancelledError`
  handling (it derives from `BaseException`, so a bare `except Exception` let a
  cancelled pipeline leak its resources), and policy denials marked
  non-retryable.
- **`otel/`** — the ZDR transform, now exporting to Langfuse over OTLP **HTTP**
  (Langfuse has no gRPC endpoint; the previous gRPC exporter would have silently
  dropped every trace).
- **Cedar** — the *policies*, rewritten for a real PDP. The engine moved from
  `cedarpy`-in-process to cedar-agent because agentgateway's native policy
  language is CEL, so Cedar reaches it through external authorization instead.

## The three pieces of code

Everything else is YAML, JSON and vendor UIs.

| Component | Why it can't be configuration |
|---|---|
| `cedar-shim/` | agentgateway's HTTP extAuthz treats **any 2xx as allow**. cedar-agent returns HTTP 200 for *both* Allow and Deny, with the verdict in the body. Wired directly, everything would be permitted. |
| `presidio-adapter/` | agentgateway posts `{"body": {"messages": [...]}}` to `/request`; Presidio speaks `/analyze` and `/anonymize`. Nothing bridges the two — the vendor's own third-party example uses an adapter for the same reason. |
| `orchestrator/` | Temporal workflow definitions are code in a language SDK. |

## What is not yet wired

Being explicit, because the gap between "configured" and "working" is where
platforms mislead:

- **Nothing has been started.** No Docker daemon was available. Every container,
  port and config shape below is unexercised.
- **`agentgateway/config.yaml` is partly inferred.** Entries marked `[check]` —
  the regex-guard field names, the Ollama param name, `mcp.targets` shape, the
  `ui` block, and where `extAuthz` attaches relative to `llm` — need validating
  against the schema at `https://agentgateway.dev/schema/config`.
- **The webhook mask question is unresolved.** agentgateway's guardrails
  overview lists webhook actions as *reject, audit*; its Guardrail Webhook API
  describes *Pass, Mask, Reject*. The adapter therefore defaults to `reject`,
  which satisfies "PII must never be sent" under either reading. Set
  `GUARDRAIL_ACTION=mask` only after confirming.
- **cedar-agent's policy wire format is unconfirmed.** `scripts/load-cedar.sh`
  submits the policy file as one named document and prints every response, so a
  mismatch is loud. A failed load leaves an empty policy set — which, with
  Cedar's deny-by-default, blocks everything rather than allowing it.
- **Registry → Cedar is a manual copy.** `registry_status` in
  `cedar/entities.json` is maintained by hand. Deriving entities from the
  registry API would remove the drift.
- **Agent Control needs SDK integration.** Its controls only fire around
  `@control()`-decorated functions inside agent code; it has no proxy mode.
- **`GATEWAY_TOKEN` → agent identity is unconfigured.** cedar-shim reads the
  identity from a header agentgateway is expected to populate after authn. If a
  client can set that header itself, the authorization model collapses — this is
  the single most important thing to verify on first run.

## What is verified

`uv run pytest tests/ -q` → 46 passing without Docker. The Cedar policies are
exercised against the real engine, and both adapters against mocked backends,
including their fail-closed paths. See the README table for the breakdown.

Also verified during assembly: the Cedar policy set evaluates with zero engine
errors across the full agent × resource matrix, and `scripts/load-cedar.sh`'s
awk-based JSON escaping round-trips the policy file byte-identically.
