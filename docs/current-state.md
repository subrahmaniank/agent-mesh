# Current state

What this repo is today, what it replaced, and what is not yet wired.

The stack now runs: `docker compose up -d` brings up 14 services — 13 long-lived
plus the one-shot `cedar-loader` — and a request has been served through
authn → Cedar → guardrails → Ollama → completion. Langfuse adds six more via
`python3 scripts/vendor_stacks.py up`. What each container does, and what its
failure breaks, is in [The containers](containers.md).

The configuration shapes that were previously inferred were validated against
the agentgateway binary, and several were wrong — see
[What is verified](../README.md#what-is-verified).

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
- **`otel/`** — the telemetry allow-list, exporting to Langfuse over OTLP **HTTP**
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

## What works now

Each of these was broken or absent at some point in this repo's history and is
now verified end to end:

- **A real framework agent runs governed.** A CrewAI project at
  `~/Developer/samples/crewai_sample_01` completes both tasks through the gateway
  as the principal `crewai-sample-01` — roughly 30 `ALLOW` decisions per run and
  no other identity. Its Python was not modified; three environment settings
  were.
- **Langfuse** runs on http://localhost:3300, provisioned headlessly, with traces
  arriving and **verified to carry no prompt content** — 0 hits for a marker
  string across all 58 string columns of its `events_full` table.
- **Telemetry leaves the gateway at all.** `config.tracing` was never set, so
  despite the architecture diagram the collector only received spans from the
  Temporal workers. `randomSampling` also had to be set explicitly: it defaults
  to sampling nothing, silently.
- **Token and cost are queryable per agent.** `config.database` plus
  `agentgateway-postgres` back the admin UI's Analytics tab, which used to error
  with "request log database is not configured". Usage attributes to the Cedar
  principal, and prompt bodies are not stored.
- **`GET /v1/models` is authorized rather than denied.** It used to 403 because
  cedar-shim could not map the path to a resource, breaking conformant OpenAI
  clients. Policy `06-list-models` grants it to `model-user`; unapproved agents
  are still denied. It returns routing *patterns* (`gpt-*`, `*`), not concrete
  model names.
- **The stack is reproducible from git.** Vendor compose files are committed
  under `vendor/` and pinned to upstream commits, so bringing Langfuse up needs
  no network.
- **The tooling is OS-independent.** No bash, no GNU coreutils, no `openssl`
  binary — Python plus Docker.

## What is not yet wired

Being explicit, because the gap between "configured" and "working" is where
platforms mislead:

- **agentregistry and Agent Control are not started.** Both are vendored and
  pinned, but agentregistry's compose requires a `VERSION` release tag that
  nothing here supplies. Until it runs, the registry → Cedar identity hand-off
  is unproven and `registry_status` is set by hand in `cedar/entities.json`.
- **`mcp.targets` is empty.** The tool path is exercised through Cedar but not
  against a real MCP server.
- **Metrics are not collected.** The collector has only a `traces` pipeline;
  agentgateway's Prometheus metrics on `:15020` are unscraped. Langfuse ingests
  traces, not metrics, so closing this needs Prometheus — a separate decision.
- **Windows is untested.** The tooling is OS-independent by construction, but
  development happened on Linux.
- **`cedar-agent` loses its policy store on restart.** It holds policies in
  memory and `cedar-loader` is a one-shot, so a restart leaves the PDP empty and
  every request 403s until the loader is re-run. Documented in
  [The containers](containers.md); making the loader self-heal is a pending
  decision.
- **The JWT issuer is a development one.** `scripts/agent_token.py` mints
  Ed25519 tokens from a local key. In production the JWKS comes from your IdP
  and tokens from the registry's approval step; only `jwtAuth.jwks.file`
  changes.
- **Cloud-provider egress is untested behind the corporate proxy.** It fails
  with `invalid peer certificate: UnknownIssuer` until a model entry carries
  `backendTLS.root`.

## What is verified

`uv run pytest tests/ -q` → 52 passing without Docker. The Cedar policies are
exercised against the real engine, and both adapters against mocked backends,
including their fail-closed paths. See the README table for the breakdown.

Also verified during assembly: the Cedar policy set evaluates with zero engine
errors across the full agent × resource matrix, and `scripts/load-cedar.sh`'s
awk-based JSON escaping round-trips the policy file byte-identically.
