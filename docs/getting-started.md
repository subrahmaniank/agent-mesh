# Getting started

The stack runs with **no cloud credentials** — the default LLM backend is your
own Ollama, wherever it runs. Cloud providers are additive.

---

## 1. Prerequisites

- Docker and Docker Compose
- Python 3.12 (the Cedar loader and token issuer are stdlib-only scripts)
- `openssl` (signs development JWTs)
- [uv](https://docs.astral.sh/uv/), for the test suite only

**Behind a TLS-inspecting proxy** (Zscaler, Netskope, Palo Alto): copy your
corporate root CA into `certs/` as a `.crt` before building, or every image
that installs a package fails with `CERTIFICATE_VERIFY_FAILED`. See
[`certs/README.md`](../certs/README.md).

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

### Started by `docker compose up -d`

| Service | URL | What it is |
|---|---|---|
| agentgateway API | `$GATEWAY` (4000 default, 4100 here) | OpenAI-compatible endpoint |
| agentgateway admin | http://localhost:15000 | Gateway config and status (redirects to `/ui`) |
| Temporal | http://localhost:8233 | Workflows and sagas |
| cedar-agent | http://localhost:8180 | Cedar PDP (`/rapidoc` for its API explorer) |

The admin UI's **Analytics** tab is backed by `agentgateway-postgres`, which
starts with the rest of the stack — see
[Observability](observability.md#the-request-log-and-the-analytics-tab).

### Started by `./scripts/up-vendor-stacks.sh`

These three publish their own compose files and are deliberately not copied into
`docker-compose.yml`. **They are not running unless you run that script**, so a
blank page at these URLs is expected rather than a fault.

| Service | URL | What it is |
|---|---|---|
| agentregistry | http://localhost:12121 | Catalogue and approvals |
| Agent Control | http://localhost:4001 | Controls dashboard (API `:8000`) |
| Langfuse | http://localhost:3000 | Traces, tokens, cost |

> **The fetch can be blocked.** The script downloads each project's compose file
> from GitHub. Behind TLS inspection that returns a proxy block page instead —
> `curl -f` rejects it, the script reports `could not fetch` and skips the
> stack, so nothing starts and nothing is corrupted. Workaround: download the
> files by hand and drop them at `.vendor/{agentregistry,agentcontrol,langfuse}.yml`,
> or point the script elsewhere with `AGENTREGISTRY_COMPOSE_URL`,
> `AGENTCONTROL_COMPOSE_URL`, `LANGFUSE_COMPOSE_URL`.
>
> **Langfuse wants port 3000**, which Grafana, a dev server or another stack
> very often already owns. Check with `ss -lntp | grep :3000` before starting
> it, and remap in the vendor compose file if it is taken.

> **Ports.** Every published port is overridable in `.env` — `GATEWAY_PORT`,
> `OTEL_GRPC_PORT` and friends. 4000, 4317/4318 and 3000 are claimed by a lot of
> other local stacks; when one clashes you get
> `Bind for 0.0.0.0:4317 failed: port is already allocated`. Find the holder
> with `ss -lntp | grep :4317`.
>
> The commands below use `$GATEWAY` so they work whatever you set:
>
> ```bash
> export GATEWAY=http://localhost:${GATEWAY_PORT:-4000}
> ```

## 3. Issue a credential

An agent's identity is the `sub` claim of a signed JWT. Nothing else establishes
it — see [Why forgery fails](#why-forgery-fails).

```bash
python3 scripts/agent_token.py init                      # once per machine
docker compose restart agentgateway                      # pick up the JWKS
TOKEN=$(python3 scripts/agent_token.py issue research-assistant)
```

`init` writes `auth/agentmesh-dev.ed25519.pem` (private, gitignored) and
`auth/jwks.json` (public, mounted read-only into the gateway). In production
this is your IdP's JWKS instead; only `jwtAuth.jwks.file` changes.

## 4. First call

```bash
curl -X POST $GATEWAY/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"model":"llama3","messages":[{"role":"user","content":"Say hello."}]}'
```

That single request exercises authn → Cedar → guardrails → routing → telemetry.

### If it returns 403

That is the platform working, not failing. Cedar is deny-by-default and two
things must be registered:

```bash
docker compose logs cedar-shim | tail -5
# DENY agent=research-assistant action=call_model resource=gemma4:latest
#      (cedar:deny [])
```

An empty `cedar:deny []` means no `permit` matched — usually the **model name
is not a registered resource**. Model ids must match what clients send, tag and
all (`gemma4:latest`, not `gemma4`). Add it to `cedar/entities.json`:

```json
{ "uid":   { "type": "AgentMesh::Model", "id": "gemma4:latest" },
  "attrs": { "tier": "local", "tenant": "acme", "provider": "ollama" },
  "parents": [] }
```

then reload without restarting anything else:

```bash
docker compose up -d --force-recreate cedar-loader && docker compose logs cedar-loader
```

A *named* policy in the denial — `cedar:deny ['01-registry-admission']` — means
a specific `forbid` fired, and the filename tells you which.

### Why forgery fails

agentgateway forwards only `authorization` and `host` to an HTTP extAuthz
endpoint; a client's own headers never arrive. The identity is injected by the
gateway from the validated token via a CEL expression
(`addRequestHeaders: {x-agentmesh-agent: jwt.sub}`). Confirm it yourself:

```bash
PH=$(python3 scripts/agent_token.py issue pii-handler)
curl -X POST $GATEWAY/v1/chat/completions \
  -H "Authorization: Bearer $PH" \
  -H 'x-agentmesh-agent: research-assistant' \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4.1","messages":[{"role":"user","content":"hi"}]}'
# 403 — the shim logs agent=pii-handler, not research-assistant
```

## 5. Add a cloud provider

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

## 6. Connect Langfuse

Create a project in the Langfuse UI, then:

```bash
echo "LANGFUSE_BASIC_AUTH=$(printf 'pk-lf-...:sk-lf-...' | base64 -w0)" >> .env
docker compose restart otel-collector
```

Langfuse accepts OTLP over **HTTP only** — a gRPC exporter silently drops every
trace. `otel/otel-collector-config.yaml` is already configured correctly.

## 7. Run the tests

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r tests/requirements-test.txt -r orchestrator/requirements.txt
uv run pytest tests/ -q          # 52 passing, no Docker needed
```

---

## Troubleshooting, from things that actually went wrong

| Symptom | Cause | Fix |
|---|---|---|
| `CERTIFICATE_VERIFY_FAILED` during `docker compose build` | TLS-inspecting proxy; the container trusts public roots only | put the corporate CA in `certs/` — [`certs/README.md`](../certs/README.md) |
| `Bind for 0.0.0.0:4317 failed: port is already allocated` | another local stack owns it | override the port in `.env` |
| `upstream call failed: Connect: invalid peer certificate: UnknownIssuer` | same proxy, now on the gateway's egress | add `backendTLS: {root: /certs/...}` to that model entry — not `insecure: true` |
| `upstream call failed: Connect: No route to host` | `OLLAMA_BASE_URL` points somewhere unreachable | Ollama binds `127.0.0.1` by default; set `OLLAMA_HOST=0.0.0.0` **on that machine** and open the port |
| Gateway exits with `data did not match any variant of untagged enum ...` | a config shape is wrong | `--validate-only` names the field and lists the accepted values |
| `cedar-loader` exits non-zero with HTTP 400 | a `.cedar` file holds more than one statement | one statement per file in `cedar/policies/` |
| Every request 403s with `cedar:deny []` | the model or agent is not a registered Cedar entity | add it to `cedar/entities.json`, re-run the loader |
| `403` on a model that exists | the model name is not a registered Cedar resource — `gemma4` and `gemma4:latest` differ | read `resource=` in `docker compose logs cedar-shim` and register that exact string |
| `503 upstream call failed` | authorization passed; the backend is unreachable | this is the platform working — fix the model backend |
| Ordinary prompts rejected as PII | `PRESIDIO_ENTITIES` empty means *every* entity; spaCy tags "France" as `LOCATION` | keep the curated default list |
| `presidio-analyzer` exits code 3 | its registry YAML replaces the defaults and needs a top-level `recognizers:` key | see the header of `presidio/conf/recognizers.yaml` |
| `http://localhost:15000` hangs or returns nothing | the admin UI binds `127.0.0.1` inside the container by default, so the published port maps to nothing | `config.adminAddr: "0.0.0.0:15000"` in `agentgateway/config.yaml` — already set |
| `Analytics API error: request log database is not configured` | `config.database` unset, or `agentgateway-postgres` not healthy | `docker compose ps agentgateway-postgres`; the gateway creates its own schema on connect |
| Traces configured but nothing reaches the collector | `config.tracing.randomSampling` defaults to `null`, which samples nothing | set it to `true` |
| agentregistry / Agent Control / Langfuse blank | they are not started by `docker compose up -d` | `./scripts/up-vendor-stacks.sh`, and see the note about blocked downloads above |
| otel-collector won't start on an unset variable | older collectors can't expand `${env:VAR:-default}` | already fixed by pinning 0.119.0 |

## Verifying the security model

```bash
RA=$(python3 scripts/agent_token.py issue research-assistant)
PH=$(python3 scripts/agent_token.py issue pii-handler)
UN=$(python3 scripts/agent_token.py issue unapproved-agent)

ask() { curl -s -o /dev/null -w "%{http_code}\n" -X POST $GATEWAY/v1/chat/completions \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $1" \
  -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"; }

ask "$RA" llama3:latest   # 200 — permitted (503 also passes: see below)
ask "$PH" gpt-4.1         # 403 — pinned to the local tier
ask "$UN" llama3:latest   # 403 — 01-registry-admission
curl -s -o /dev/null -w "%{http_code}\n" -X POST $GATEWAY/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{"model":"llama3","messages":[]}'   # 401
```

A **503** on the first line is also a pass. It means every control allowed the
call and the model backend was unreachable — check for
`cedar:allow` in `docker compose logs cedar-shim`.

Then the guardrails:

```bash
# masked inline by the regex layer — the call succeeds and the model never sees it
curl -s -X POST $GATEWAY/v1/chat/completions -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $RA" \
  -d '{"model":"llama3:latest","messages":[{"role":"user",
       "content":"Repeat exactly: my SSN is 123-45-6789"}]}'
# -> the completion echoes "<SSN>", not the digits

# rejected by Presidio before egress
curl -s -X POST $GATEWAY/v1/chat/completions -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $RA" \
  -d '{"model":"llama3:latest","messages":[{"role":"user",
       "content":"Wire it to GB82WEST12345698765432 today"}]}'
# -> 403 Request blocked: content contains IBAN_CODE.

docker compose logs presidio-adapter | tail -3
```

Note what is **not** rejected: a person's name. `PERSON` is deliberately absent
from `PRESIDIO_ENTITIES` — see
[Security & governance](security-and-governance.md#2-pii--two-layers-because-neither-alone-is-enough).
Keeping an agent's data on-premises is Cedar's job, via `allowed_model_tiers`.

## Bringing your own agent on

Everything above is operator setup. To onboard an agent — register it, grant it
policy, issue its credential and verify — follow
[Onboarding an agent](agent-onboarding-guide.md). It is framework-neutral and
self-contained.

## Still to exercise

The three vendor stacks have not been started here:
`./scripts/up-vendor-stacks.sh` brings up agentregistry, Agent Control and
Langfuse. Until then the registry → Cedar identity hand-off and the per-session
token/cost view are configured but unproven. `mcp.targets` is also empty, so
the tool path has been verified through Cedar but not against a real MCP server.

## Teardown

```bash
docker compose down
./scripts/up-vendor-stacks.sh down
```
