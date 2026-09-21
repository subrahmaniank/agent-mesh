# Observability & Analytics Spine

The telemetry architecture uses **OpenTelemetry (OTel)** with in-flight transformation to support vendor-neutral, zero-data-retention tracing and metrics collection.

---

## 1. OTel Collector Configuration (`otel/otel-collector-config.yaml`)

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 128

  transform:
    error_mode: ignore
    trace_statements:
      - context: span
        statements:
          # Zero Data Retention: Redact raw message bodies
          - delete_key(attributes, "gen_ai.prompt")
          - delete_key(attributes, "gen_ai.completion")
          - delete_key(attributes, "llm.prompts")
          - delete_key(attributes, "llm.completions")
          - delete_key(attributes, "input.value")
          - delete_key(attributes, "output.value")
          # Retain execution metadata
          - set(attributes["privacy.sanitized"], "true")
          - set(attributes["platform.version"], "v1.0.0")

exporters:
  otlp/agentops:
    endpoint: "https://api.agentops.ai:443"
    headers:
      X-Agentops-Api-Key: "${env:AGENTOPS_API_KEY}"

  otlp/langfuse:
    endpoint: "https://langfuse.internal.net:443"
    headers:
      Authorization: "Basic ${env:LANGFUSE_AUTH_TOKEN}"

  logging:
    verbosity: detailed

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch, transform]
      exporters: [otlp/agentops, logging]
```

---

## 2. Interchangeable Observability Backends

Observability sinks can be swapped seamlessly by modifying the `exporters` array in `service.pipelines.traces`:

- **AgentOps.ai**: Switch to `exporters: [otlp/agentops, logging]`.
- **Langfuse OSS / Cloud**: Switch to `exporters: [otlp/langfuse, logging]`.
- **Custom Internal Sinks**: Add any OTLP/HTTP or OTLP/gRPC exporter.

No application code changes or agent rebuilds are needed.
