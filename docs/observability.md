# Observability — traces, tokens and cost

Token usage and cost come from agentgateway itself, so they cover **every** model
call through the platform regardless of which agent made it or which provider
served it. No instrumentation in agent code.

There are **two independent systems**, and confusing them wastes time:

| | Where it is configured | What it gives you | Needs |
|---|---|---|---|
| **Request log** | `config.database` in `agentgateway/config.yaml` | the admin UI's Analytics tab — requests, tokens, cost, per agent | a Postgres instance |
| **Traces** | `config.tracing` in `agentgateway/config.yaml` | OTel spans out to a backend (Langfuse, AgentOps, …) | the OTel collector |

They do not substitute for each other. The Analytics tab reads the database and
knows nothing about OTel; Langfuse reads traces and knows nothing about the
database.

---

## Where OTel is configured

Four places, in the order data moves:

**1. The gateway emits** — `agentgateway/config.yaml`:

```yaml
config:
  tracing:
    otlpEndpoint: "${OTEL_EXPORTER_OTLP_ENDPOINT}"   # http://otel-collector:4317
    otlpProtocol: grpc                               # grpc | http
    randomSampling: true
```

> **`randomSampling` is the one that catches people.** It is a CEL expression
> and defaults to `null`, which samples **nothing**. The endpoint is configured,
> the collector is reachable, and not a single span is emitted. `true` samples
> every request; replace it with an expression when volume matters.

**2. The workers emit** — `docker-compose.yml` sets
`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317` on `orchestrator` and
`remote-runner`. The SDK wiring is `observability/telemetry.py`, which degrades
to a no-op when the variable is unset, so a worker never fails to start because
telemetry is unavailable.

**3. The collector receives, scrubs and forwards** —
`otel/otel-collector-config.yaml`: an OTLP receiver on 4317 (gRPC) and 4318
(HTTP), a `transform` processor that drops prompt bodies, and exporters.

**4. The collector's ports are published** — `OTEL_GRPC_PORT` / `OTEL_HTTP_PORT`
in `.env`, defaulting to 4317/4318. This machine remaps them to 14317/14318
because Grafana Alloy owns the defaults. That remap is for host access only;
inside the compose network the collector is always `otel-collector:4317`.

## Why the scrub had to be rebuilt

The original transform deleted six attributes — `gen_ai.prompt`,
`gen_ai.completion`, `llm.prompts`, `llm.completions`, `input.value`,
`output.value`. **agentgateway emits none of them.** Its attributes are
`gen_ai.usage.*`, `http.*`, `agentgateway.user`, `src.addr`. The scrub deleted
nothing, and the documentation said it protected you.

Nothing was leaking — the gateway does not put prompt content on spans by
default — but a control aimed at names that do not exist is not a control. It is
now an **allow-list**: `keep_keys` drops every attribute not explicitly named, so
a prompt body under any key, an attribute a future version adds, or a
provider-specific field nobody reviewed is dropped by default rather than
exported by default.

Resource attributes are scrubbed too. The old config had no `context: resource`
block at all, and SDKs routinely put host names, container ids and process
command lines there.

Two layers, because one is not enough:

| Layer | Where | What it does |
|---|---|---|
| Source | `config.tracing.fields.remove` in `agentgateway/config.yaml` | strips `src.addr` before the span leaves the gateway process |
| Egress | `keep_keys` in `otel/otel-collector-config.yaml` | drops everything not on the allow-list |

The source layer matters because a second exporter added later would bypass the
collector, not the gateway.

### What is exported, measured

Verified by setting the collector's `debug` exporter to `verbosity: detailed`,
sending a request containing a marker string, and reading the exported span:

```
gen_ai.usage.input_tokens · gen_ai.usage.output_tokens
gen_ai.usage.cache_read.input_tokens · gen_ai.request.model
gen_ai.response.model · gen_ai.provider.name · gen_ai.operation.name
agentgateway.user · http.method · http.status · duration · privacy.sanitized
resource: service.name · service.version
```

Gone: `src.addr`, `http.path`, `http.host`, `user_agent.name`, `jwt.sub`,
`route`, `endpoint`.

And in Langfuse's own store, across all 58 string columns of `events_full`:

```sql
-- rows containing the marker string: 0
-- events with non-empty input:       0
-- events with non-empty output:      0   (of 20 events)
```

Repeat that check on your own deployment — it is the assertion the whole design
rests on, and it takes a minute.

### Agent attribution needs one extra line

`agentgateway.user` reaches the request-log database but **not** the OTLP export,
so a tracing backend has nothing to attribute usage to. `config.tracing.fields.add`
puts it on the span:

```yaml
fields:
  add:
    agentgateway.user: jwt.sub    # a CEL expression
```

`jwt.sub` is the validated token's subject — the Cedar principal. An agent
identifier, not a person.

## The path out

```
agentgateway ──OTLP/gRPC──▶ otel-collector ──OTLP/HTTP──▶ Langfuse :3300
orchestrator ──OTLP/gRPC──▶      │
remote-runner ─OTLP/gRPC──▶      └─ allow-list: everything not explicitly
                                    named is dropped; token, cost and model
                                    attributes are what survive
```

Langfuse runs as its own compose project, so the collector reaches it over the
host — `http://host.docker.internal:3300/api/public/otel` — not by service name.

> ⚠️ **Langfuse accepts OTLP over HTTP only.** It has no gRPC endpoint. The
> exporter must be `otlphttp/langfuse`; an `otlp/` exporter connects and then
> silently drops every trace.

---

## The request log and the Analytics tab

The admin UI at `:15000` has an Analytics tab. Without a database it reports:

```
Analytics API error
request log database is not configured
```

That is not an OTel problem — it is `config.database`:

```yaml
config:
  database:
    url: "${AGENTGATEWAY_DATABASE_URL}"
```

backed by the `agentgateway-postgres` service in `docker-compose.yml`. The
gateway creates its own schema on first connect:

```
info telemetry::log_store::postgres  initializing request log database schema
info telemetry::log_store::postgres  request log database schema is ready
```

### Why Postgres and not SQLite

The gateway image runs as uid 65532. A named volume is created root-owned, so
the process cannot create a SQLite file in it, and the image is distroless so
there is no shell to `chown` with. Postgres sidesteps the problem. It is a
**separate instance** from `temporal-postgres` on purpose: a request log that
grows without bound should not be able to fill the volume holding workflow
history.

### What it records

One row per request in `request_logs`:

| Column | |
|---|---|
| `gen_ai_request_model`, `gen_ai_provider_name` | what was asked for, and who served it |
| `input_tokens`, `output_tokens`, `total_tokens` | usage |
| `cost` | realized cost — `0` for local models, populated for priced providers |
| `agentgateway_user` | **the Cedar principal**, so usage attributes to an agent |
| `http_status`, `error`, `duration_ms` | outcome |
| `trace_id`, `span_id` | the join back to the traces above |

### It does not store prompts by default

There is a `request_log_payloads` table and a `has_payload` boolean, and on this
configuration nothing is written to it — `promptPreview` and `turn.input` /
`turn.output` come back `null`. Verify on your own deployment before assuming:

```bash
docker exec agentmesh_gateway_postgres \
  psql -U agentgateway -d agentgateway -c 'select count(*) from request_log_payloads;'
```

This matters. The collector's ZDR transform protects *traces*; it has no effect
on the request log, which is a separate path to a separate store. If payload
capture is ever switched on, that database holds prompt content and must be
treated accordingly.

### Querying it directly

The UI calls `POST /api/logs/analytics/summary` and `POST /api/logs/search`; both
are reachable without the UI:

```bash
curl -s -X POST localhost:15000/api/logs/analytics/summary \
  -H 'Content-Type: application/json' -d '{}'
```

```json
{"buckets":[{"requests":3,"totalTokens":66,"cost":0.0}],
 "filterOptions":{"agentgateway.user":["research-assistant"],
                  "provider":["ollama"],"requestModel":["gemma4:latest"]}}
```

Or straight from SQL:

```sql
select agentgateway_user, gen_ai_request_model,
       count(*), sum(total_tokens), sum(cost)
from request_logs group by 1, 2;
```

---

## Running Langfuse

```bash
python3 scripts/vendor_stacks.py up          # not part of `docker compose up -d`
docker compose restart otel-collector  # pick up LANGFUSE_BASIC_AUTH
```

Then open http://localhost:3300 — the login is `LANGFUSE_INIT_USER_EMAIL` /
`LANGFUSE_INIT_USER_PASSWORD` from `.env`.

Six containers: `langfuse-web`, `langfuse-worker`, `clickhouse`, `redis`,
`minio`, `postgres`. Four things about running it here were not obvious:

| | |
|---|---|
| **Fetching the compose file** | `raw.githubusercontent.com` is blocked by TLS-inspecting proxies. The script uses the GitHub **contents API** instead — `api.github.com` is reachable where the raw host is not |
| **Port 3000** | claimed by Grafana and much else. Remapped to `LANGFUSE_PORT` (3300) through `.vendor/langfuse.override.yml`, which the script regenerates — so re-fetching the vendor file cannot silently revert it |
| **The minio image** | upstream uses `cgr.dev/chainguard/minio`, whose blobs come from `r2.cloudflarestorage.com` and 403 behind the proxy *after* the manifest has already downloaded, so it looks transient. Substituted with `quay.io/minio/minio` |
| **Credentials** | `DATABASE_URL` is read directly and defaults to the stock `postgres:postgres`. Setting `POSTGRES_PASSWORD` alone gives Prisma `P1001: Can't reach database server`, which reads as a network fault and is actually auth |

Secrets are generated into `.env`. Langfuse's compose ships insecure defaults for
every one of them, which is how a self-hosted deployment ends up open.

### No click-through needed

`LANGFUSE_INIT_*` provisions the organisation, project, user and API keys on
first boot, so `LANGFUSE_BASIC_AUTH` is valid immediately. Without those
variables you must sign in, create a project by hand and paste its keys back.

### Langfuse v4 stores events, not legacy traces

`GET /api/public/traces` returns `0` even when ingestion is working. v4 writes to
`events_core` / `events_full` in ClickHouse; the legacy tables stay empty. Check
ingestion there, not through that endpoint:

```bash
docker exec langfuse-clickhouse-1 clickhouse-client \
  --password "$CLICKHOUSE_PASSWORD" -q "select count() from default.events_full"
```

Before Langfuse is up the collector logs one export failure per batch. That is
expected — traces are still received and scrubbed, they just have nowhere to
land.

## Using AgentOps instead

The collector is the seam, so swapping the backend is a config edit with no agent
changes: add an `otlp/agentops` exporter and put it in the `traces` pipeline's
`exporters` list. Everything upstream — including the ZDR transform — is
unchanged.

## What is not wired

- **Metrics.** The collector has only a `traces` pipeline. agentgateway exposes
  Prometheus metrics on its stats listener (`:15020`, unpublished) and nothing
  scrapes them. This cannot be fixed by pointing them at Langfuse — Langfuse
  ingests traces, not Prometheus metrics. It needs a Prometheus service and a
  `prometheus` receiver in the collector, which is a separate decision.
- **The workers only emit while working.** `orchestrator` and `remote-runner`
  produce spans during workflow execution, so an idle stack shows gateway traces
  only. Not a fault.
- **No Prometheus/Grafana.** agentgateway exposes a token-usage metric and a
  stats listener, but nothing scrapes it.
- **Agent Control has its own audit log** of policy triggers. No single pane
  joins it to the above.
- **Sessions.** The request log attributes usage to an *agent*
  (`agentgateway_user`), not to a session. Grouping by conversation needs a
  session identifier propagated from the client, which nothing does yet.
