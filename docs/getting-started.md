# Getting started

The stack runs with **no cloud credentials** — the default LLM backend is your
own Ollama, wherever it runs. Cloud providers are additive.

---

## 1. Prerequisites

- Docker and Docker Compose
- Python 3.12 + [uv](https://docs.astral.sh/uv/) (for the test suite only)

## 2. Bring it up

```bash
cp .env.example .env
```

Set `OLLAMA_BASE_URL` to wherever your Ollama server runs. It is a **full URL,
including the scheme and the `/v1` suffix** — agentgateway's ollama provider
takes a base URL, not a `host:port` pair:

| Ollama location | `OLLAMA_BASE_URL` |
|---|---|
| Another machine | `http://192.168.1.20:11434/v1` |
| This Docker host | `http://host.docker.internal:11434/v1` |
| In-compose (below) | `http://ollama:11434/v1` |

> Ollama binds to `127.0.0.1` by default. A server on another machine needs
> `OLLAMA_HOST=0.0.0.0` set **on that machine**, or the gateway's connection is
> refused.

```bash
docker compose up -d            # components configured in this repo
./scripts/up-vendor-stacks.sh   # products with their own compose files
```

If you'd rather run Ollama here than point at an existing one:

```bash
docker compose --profile local-llm up -d
docker exec agentmesh_ollama ollama pull llama3
# then set OLLAMA_BASE_URL=http://ollama:11434/v1
```

| Service | URL | What it is |
|---|---|---|
| agentgateway admin + playground | http://localhost:15000 | Gateway config, LLM playground |
| agentgateway API | http://localhost:4000 | OpenAI-compatible endpoint |
| agentregistry | http://localhost:12121 | Catalogue and approvals |
| Agent Control | http://localhost:4001 | Controls dashboard (API `:8000`) |
| Langfuse | http://localhost:3000 | Traces, tokens, cost |
| Temporal | http://localhost:8233 | Workflows and sagas |
| cedar-agent | http://localhost:8180 | Cedar PDP (`/rapidoc` for its API explorer) |

## 3. First call

```bash
curl -X POST localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer dev-operator-token' \
  -d '{"model":"llama3","messages":[{"role":"user","content":"Say hello."}]}'
```

That single request exercises authn → Cedar → guardrails → routing → telemetry.

## 4. Add a cloud provider

Put a key in `.env`:

```bash
OPENAI_API_KEY=sk-...
```

It is already wired in `agentgateway/config.yaml`. Restart the gateway and the
same endpoint serves it — only `model` changes:

```bash
docker compose restart agentgateway
curl ... -d '{"model":"gpt-4.1","messages":[...]}'
```

No agent code changes, and the guardrails, Cedar authorization and telemetry
apply to the new backend automatically.

## 5. Connect Langfuse

Create a project in the Langfuse UI, then:

```bash
echo "LANGFUSE_BASIC_AUTH=$(printf 'pk-lf-...:sk-lf-...' | base64 -w0)" >> .env
docker compose restart otel-collector
```

Langfuse accepts OTLP over **HTTP only** — a gRPC exporter silently drops every
trace. `otel/otel-collector-config.yaml` is already configured correctly.

## 6. Run the tests

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r tests/requirements-test.txt -r orchestrator/requirements.txt
uv run pytest tests/ -q          # 46 passing, no Docker needed
```

---

## First-run checklist

Nothing in the container stack has been started yet — no Docker daemon was
available when it was assembled. Work through these in order; each is a place
where a documented shape may differ from the shipped build.

1. **Does agentgateway accept the config?**
   `docker compose logs agentgateway`. Entries marked `[check]` in
   `agentgateway/config.yaml` are inferred — the regex-guard fields,
   `mcp.targets`, the `ui` block, and where `extAuthz` attaches. Validate
   against `https://agentgateway.dev/schema/config`.

   Also confirm the gateway can reach Ollama:
   `docker compose exec agentgateway wget -qO- $OLLAMA_BASE_URL/models`.
   A refused connection usually means Ollama is bound to `127.0.0.1` on its
   host rather than `0.0.0.0`.

2. **⚠️ Can a client forge the agent identity header?**
   The single most important check. cedar-shim trusts the header agentgateway
   sets after authn. Send a request with that header set by hand and confirm the
   gateway overwrites it. If it does not, authorization is bypassable.

3. **Did the Cedar policies load?**
   `docker compose logs cedar-loader`, then
   `curl localhost:8180/v1/policies`. A failed load leaves an empty policy set —
   which denies everything rather than allowing it, so the symptom is universal
   403s, not a silent hole.

4. **Does a Deny actually deny?**
   Call a model the agent has no role for. Expect 403. If it returns 200,
   cedar-shim is not in the path.

5. **Does the guardrail fire?**
   Send a prompt containing an SSN and another containing only a person's name
   and address. The first should be masked, the second rejected —
   `docker compose logs presidio-adapter` shows which.

6. **Does a webhook `MaskAction` work?**
   Set `GUARDRAIL_ACTION=mask` and re-send. If masking is not honoured (the
   vendor docs disagree), revert to `reject` and record the finding.

7. **Do traces reach Langfuse with tokens and cost?**
   Check the Langfuse UI after a call. If empty, confirm `LANGFUSE_BASIC_AUTH`
   is set and the exporter is `otlphttp/`, not `otlp/`.

8. **Does the Temporal path work end to end?**
   `docker compose exec orchestrator python -m orchestrator.starter run-dag`,
   then watch the event history at `:8233`.

## Teardown

```bash
docker compose down
./scripts/up-vendor-stacks.sh down
```
