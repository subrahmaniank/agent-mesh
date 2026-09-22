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

## The path out

```
agentgateway ──OTLP/gRPC──▶ otel-collector ──OTLP/HTTP──▶ Langfuse :3000
orchestrator ──OTLP/gRPC──▶      │
remote-runner ─OTLP/gRPC──▶      └─ ZDR transform: prompt and completion bodies
                                    dropped, token/cost attributes preserved
```

`otel/otel-collector-config.yaml` deletes `gen_ai.prompt`, `gen_ai.completion`,
`llm.prompts`, `llm.completions`, `input.value` and `output.value` before
anything leaves the collector, and does **not** touch token or cost attributes —
so you keep per-session accounting without retaining prompt content.

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

## Setup

The request log needs nothing — `docker compose up -d` starts
`agentgateway-postgres` and the gateway creates its schema.

For Langfuse:

```bash
# 1. Start it — it is not part of `docker compose up -d`
./scripts/up-vendor-stacks.sh

# 2. Create a project in the Langfuse UI, then:
echo "LANGFUSE_BASIC_AUTH=$(printf 'pk-lf-...:sk-lf-...' | base64 -w0)" >> .env
docker compose restart otel-collector
```

Until Langfuse is up, the collector logs an export failure per batch:

```
error  Exporting failed. Dropping data.  {"name": "otlphttp/langfuse", ...}
```

That is expected and harmless — traces are still received and still visible via
the `debug` exporter. It is noise, not damage.

## Using AgentOps instead

The collector is the seam, so swapping the backend is a config edit with no agent
changes: add an `otlp/agentops` exporter and put it in the `traces` pipeline's
`exporters` list. Everything upstream — including the ZDR transform — is
unchanged.

## What is not wired

- **Langfuse is not running** on this deployment, so per-session grouping in a
  UI is unproven. The gateway→collector hop **is** verified: `trace_id` appears
  in the request log and the collector receives the batch.
- **No Prometheus/Grafana.** agentgateway exposes a token-usage metric and a
  stats listener, but nothing scrapes it.
- **Agent Control has its own audit log** of policy triggers. No single pane
  joins it to the above.
- **Sessions.** The request log attributes usage to an *agent*
  (`agentgateway_user`), not to a session. Grouping by conversation needs a
  session identifier propagated from the client, which nothing does yet.
