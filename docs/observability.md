# Observability — traces, tokens and cost

Token usage and cost come from agentgateway itself, so they cover **every**
model call through the platform regardless of which agent made it or which
provider served it. No instrumentation in agent code.

---

## What agentgateway emits

| Signal | Where |
|---|---|
| `agentgateway_gen_ai_client_token_usage` | Prometheus — input and output tokens per request |
| Token usage as span attributes | OTel traces |
| Realized cost in USD | Request logs |

Because these are produced at the gateway, per-session accounting is a property
of the platform rather than something each agent has to report honestly.

## The path to Langfuse

```
agentgateway ──OTLP──▶ otel-collector ──OTLP/HTTP──▶ Langfuse :3000
                          │
                          └─ ZDR transform: prompt and completion bodies dropped
                             token/cost attributes preserved
```

`otel/otel-collector-config.yaml` deletes `gen_ai.prompt`, `gen_ai.completion`,
`llm.prompts`, `llm.completions`, `input.value` and `output.value` before
anything leaves the collector, and does **not** touch agentgateway's token or
cost attributes — so you keep per-session accounting without retaining prompt
content.

> ⚠️ **Langfuse accepts OTLP over HTTP only.** It has no gRPC endpoint. The
> exporter must be `otlphttp/langfuse`; an `otlp/` exporter connects and then
> silently drops every trace. This is configured correctly already, but it is
> the first thing to check if the Langfuse UI stays empty.

## Setup

```bash
# 1. Create a project in the Langfuse UI (http://localhost:3000)
# 2. Take its public and secret keys:
echo "LANGFUSE_BASIC_AUTH=$(printf 'pk-lf-...:sk-lf-...' | base64 -w0)" >> .env
docker compose restart otel-collector
```

Langfuse groups traces by session id, giving tokens and cost per session and per
agent.

## Using AgentOps instead

The collector is the seam, so swapping the backend is a config edit with no
agent changes: add an `otlp/agentops` exporter alongside, and put it in the
`traces` pipeline's `exporters` list. Everything upstream — including the ZDR
transform — is unchanged.

## What is not wired

- **No Prometheus/Grafana in this stack.** agentgateway exposes the token-usage
  metric, but nothing scrapes it. Add a Prometheus service and point it at the
  gateway's metrics endpoint if you want dashboards and alerting on spend.
- **Agent Control has its own audit log** of policy triggers, separate from
  Langfuse. There is no single pane joining the two today.
- **None of this has been observed running** — the collector config and exporter
  choice are verified by reading, not by a trace arriving. See
  [`current-state.md`](current-state.md).
