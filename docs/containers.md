# The containers

Twenty-one containers run once everything is up: fifteen defined in this repo's
`docker-compose.yml` (fourteen by default, plus `ollama` behind a profile) and
six from Langfuse. This is what each one is for, and — measured, not guessed —
what stops working when it does.

The **If it stops** column was produced by stopping each container, issuing a
request through the gateway, and restarting it. Where the answer is `200`, the
container is genuinely not in the request path; that is a design property worth
knowing, not an oversight.

> Ports below are the defaults. Every published port is overridable in `.env`
> (`GATEWAY_PORT`, `OTEL_GRPC_PORT`, `LANGFUSE_PORT` …); this machine uses 4100
> for the gateway because DevLake owns 4000.

---

## Data path

Everything an agent does passes through here.

### `agentgateway` — the choke point
`cr.agentgateway.dev/agentgateway:v1.5.0` · **4000** (API), **15000** (admin UI)

Authenticates the caller, delegates authorization to Cedar, runs the PII
guardrails, routes to whichever backend serves the requested model, and emits
telemetry. Configured entirely by `agentgateway/config.yaml`; the JWKS at
`auth/jwks.json` is mounted read-only.

Runs distroless as uid 65532 — there is no shell, so `docker exec … sh` fails,
and it cannot write to a root-owned volume. That is why the request log is
Postgres rather than SQLite.

**If it stops:** nothing works. This is the single point every call goes
through, which is the point.

### `ollama` — optional local inference
`ollama/ollama:latest` · **11434** · profile `local-llm`

Not started by default, because Ollama usually already runs somewhere else —
point `OLLAMA_BASE_URL` at it instead. Start it with
`docker compose --profile local-llm up -d` if you want inference inside the
stack. Models persist in the `ollama-models` volume.

**If it stops:** `503 upstream call failed`. Authorization and guardrails have
already passed at that point, so a 503 means the platform worked and the model
did not.

---

## Identity and policy

### `cedar-agent` — the policy decision point
`permitio/cedar-agent:latest` · **8180**

Holds the Cedar schema, policies and entity data, and answers
`POST /v1/is_authorized`. Real Cedar in a real PDP, rather than the gateway's
native CEL.

**If it stops:** `403 external authorization failed` on every request. Fail
closed — the shim cannot obtain a decision, so it does not manufacture one.

> ### ⚠️ Restarting it silently empties the policy store
> `cedar-agent` keeps policies and entities **in memory only**. After a restart
> the PDP reports `policies: 0`, `data: 0`, and because `cedar-loader` is a
> one-shot that already exited, nothing reloads them. Cedar is deny-by-default,
> so every request then returns `403 cedar:deny []` — which reads as a policy
> bug rather than lost state.
>
> ```
> docker compose restart cedar-loader     # reload; verify with the log
> curl localhost:8180/v1/policies         # expect 6, not 0
> ```
>
> This bit during the measurements for this very table, producing a run of false
> readings before it was spotted. It will do the same to you.

### `cedar-shim` — decision to HTTP status
`build: cedar-shim/` · **no published port**

agentgateway's HTTP extAuthz treats **any 2xx as allow**, and cedar-agent
returns HTTP 200 for both Allow and Deny with the verdict in the body. Wired
directly, everything would be permitted. The shim classifies the request into a
Cedar `(action, resource)`, asks the PDP, and returns 200 or 403.

Deliberately unpublished: only the gateway calls it, and it is the component
that decides authorization.

**If it stops:** `403 external authorization failed`. Fail closed.

### `cedar-loader` — one-shot policy loader
`python:3.12-slim` · runs `scripts/load_cedar.py`, then exits 0

Pushes `cedar/policies/*.cedar` and `cedar/entities.json` into cedar-agent, then
reads back what was stored and exits non-zero if the counts disagree. That
read-back exists because a failed load leaves an **empty** policy set, and
deny-by-default turns that into universal 403s three containers from the cause.

**`Exited (0)` is success, not failure.** `docker compose up -d` will not re-run
it when nothing changed; `docker compose restart cedar-loader` will.

**If it stops:** nothing — its work is already done. What matters is re-running
it after editing `cedar/`, or after cedar-agent restarts.

---

## PII

### `presidio-analyzer` — NER detection
`mcr.microsoft.com/presidio-analyzer:latest` · **5001**

Finds entities regex cannot: IBANs, medical licence numbers, names. Its
recognizer registry is `presidio/conf/recognizers.yaml`, which **replaces** the
vendor defaults rather than extending them — it is a copy of upstream's file
with our org-specific recognizer appended.

Takes roughly a minute to load its spaCy models. During that window the adapter
fails closed, so requests 503 until it is ready.

**If it stops:** `503 failed to process LLM request: prompt guard failed`.

### `presidio-anonymizer` — masking
`mcr.microsoft.com/presidio-anonymizer:latest` · **5002**

Rewrites detected spans into placeholders. Only used when the adapter runs in
`mask` mode.

**If it stops:** `200`. It is not in the default request path — masking of
well-formed identifiers is done by agentgateway's own regex layer, not here.

### `presidio-adapter` — the bridge
`build: presidio-adapter/` · **no published port**

agentgateway posts its guardrail envelope; Presidio speaks `/analyze` and
`/anonymize`. Nothing joins the two. The adapter also decides which entities
count as a violation (`PRESIDIO_ENTITIES`) and fails closed on any error —
an unavailable scanner must not become an open door.

**If it stops:** `503 … prompt guard failed`, because the webhook is configured
`failureMode: failClosed`.

---

## Telemetry

Two independent systems. See [Observability](observability.md).

### `otel-collector` — scrub and forward
`otel/opentelemetry-collector-contrib:0.119.0` · **4317** (gRPC), **4318** (HTTP)

Receives spans from the gateway and the Temporal workers, applies the
allow-list that drops every attribute not explicitly permitted, and forwards to
Langfuse. The allow-list is what keeps prompt content and client IPs out of
exported telemetry.

**If it stops:** `200` — traffic is completely unaffected and telemetry is
**silently lost**. The one place in this stack that fails open, deliberately:
losing observability must not take down the platform.

### `agentgateway-postgres` — the request log
`postgres:16-alpine` · not published · volume `gateway-pgdata`

Backs the admin UI's Analytics tab. One row per request: model, provider,
token counts, cost, duration, and `agentgateway_user` — the Cedar principal — so
usage attributes to an agent. The gateway creates its own schema on first
connect. It does **not** store prompt bodies.

Separate from `temporal-postgres` on purpose: an unbounded request log must not
fill the volume holding workflow history.

**If it stops:** `200` while the gateway is already running — it logs
`sqlx_core::pool` connection errors and keeps serving, losing request-log rows.
On a **cold start** it blocks, because the gateway declares
`depends_on: condition: service_healthy`.

### Langfuse — six containers
Started separately: `python3 scripts/vendor_stacks.py up langfuse`. Defined by
`vendor/langfuse.yml` (upstream, pinned) plus `vendor/langfuse.override.yml`
(ours).

| Container | Image | What it is |
|---|---|---|
| `langfuse-web` | `langfuse/langfuse:4` | UI and ingestion API, **3300** (`LANGFUSE_PORT`) |
| `langfuse-worker` | `langfuse/langfuse-worker:4` | processes ingested events into ClickHouse |
| `clickhouse` | `clickhouse-server:25.12` | where traces actually land (`events_full`) |
| `postgres` | `postgres:17` | Langfuse's own metadata, projects, users |
| `redis` | `redis:7` | queues between web and worker |
| `minio` | `quay.io/minio/minio` | S3-compatible blob store for event payloads |

`minio` is substituted from upstream's Chainguard image, whose blobs 403 behind
a TLS-inspecting proxy. Langfuse's Postgres, ClickHouse and Redis have their
published ports cleared — nothing outside its network needs them.

**If any stops:** `200` from the gateway. The collector logs an export failure
per batch and drops the data; the platform is unaffected.

---

## Orchestration

### `temporal` — the workflow engine
`temporalio/auto-setup:1.24.2` · **7233**

Durable execution for multi-step work: saga compensation, human approval gates,
retries that survive restarts.

**If it stops:** `200` — LLM calls are untouched. Workflows stall and the
workers log connection errors until it returns.

### `temporal-postgres` — workflow history
`postgres:16-alpine` · not published · volume `temporal-pgdata`

**If it stops:** `200` for LLM calls; Temporal itself starts erroring
(39 error lines within 40 seconds when measured) and needs a restart once the
database is back.

### `temporal-ui` — workflow browser
`temporalio/ui:latest` · **8233**

Read-only window onto executions and event histories.

**If it stops:** `200`. Only the UI is gone.

### `orchestrator` — control-plane worker
`build: orchestrator/` · no ports

Runs workflow definitions plus the orchestration-plane activities, on
`orchestrator-task-queue`. Compensations must be registered here or a saga
unwind stalls on "activity not registered".

**If it stops:** `200`. Workflows stop progressing; nothing else changes.

### `remote-runner` — agent-plane worker
`build: remote_runner/` · no ports

The agent plane: **outbound connections only, no inbound ports**. It dials
Temporal and polls `remote-agent-fleet-prod`, which is what lets it sit outside
the control plane's trust boundary. `MultiAgentDAGWorkflow` routes its
gateway-facing step here explicitly.

**If it stops:** `200`. Agent-plane activities are not executed.

---

## Configured but not running

| Product | Why |
|---|---|
| **agentregistry** (`:12121`) | The catalogue and approval workflow. Vendored and pinned, but its compose requires a `VERSION` release tag that nothing here supplies yet. Until it runs, `registry_status` in `cedar/entities.json` is set by hand |
| **Agent Control** (`:8000`, UI `:4001`) | Step-level controls inside an agent's own execution — what the gateway cannot see. Vendored and pinned; not started |

---

## Volumes, and what `down -v` destroys

| Volume | Holds |
|---|---|
| `temporal-pgdata` | workflow history — every execution ever run |
| `gateway-pgdata` | the request log behind the Analytics tab |
| `ollama-models` | pulled models, if you run Ollama in-compose |
| `langfuse_*` (separate project) | traces, projects, users, API keys |

`docker compose down` keeps all of it. `docker compose down -v` does not.

---

## Quick reference

```bash
docker compose ps                       # what is running here
python3 scripts/vendor_stacks.py status # what is running from vendor/
docker compose logs cedar-shim          # every authorization decision
docker compose restart cedar-loader     # after editing cedar/, or a cedar-agent restart
docker compose restart agentgateway     # after editing agentgateway/config.yaml
```
