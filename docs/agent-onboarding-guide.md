# Onboarding an agent

How to take an agent that exists — in any framework, or none — and make it a
governed participant on this platform: authenticated, authorized by policy,
screened for PII, and accounted for.

This guide is framework-neutral. Every command below is plain `curl` or a
repository script. Framework-specific wiring is one short step at the end, and
the quirks of individual frameworks live in
[Framework integrations](framework-integrations.md).

---

## Worksheet

Decide these five things first. Everything afterwards is substitution.

| | Value | Decided in |
|---|---|---|
| **Agent id** | `______________` | Step 1 |
| **Tenant** | `acme` | Step 2 |
| **Roles** | `model-user` / `tool-user` | Step 2 |
| **Model tiers** | `local` / `hosted` | Step 2 |
| **Model names** | exactly as sent on the wire | Step 4 |

Throughout, `$AGENT` is your agent id and `$GATEWAY` is the gateway's base URL:

```bash
export AGENT=my-first-agent
export GATEWAY=http://localhost:${GATEWAY_PORT:-4000}   # 4100 on this machine
```

> **Ports.** The gateway listens on 4000 by default, but every published port is
> overridable and this machine sets `GATEWAY_PORT=4100` in `.env` because DevLake
> owns 4000. Check with `grep GATEWAY_PORT .env`; if it is unset, 4000 is right.

---

## What onboarding actually is

Two planes, and they meet only at a token.

```
   AgentMesh (the platform)                 Your agent's project
   ─────────────────────────                ────────────────────
   cedar/entities.json                      base_url  → $GATEWAY/v1
     principal + what it may do             api_key   → the JWT
   cedar/policies/*.cedar                   model     → a registered name
     the rules, unchanged per agent
   scripts/agent_token.py
     issues the credential
```

**Your agent's code does not change.** It already speaks to some OpenAI-compatible
endpoint; you are changing which one, and what it presents as a key. There is no
SDK to adopt and no library to import.

**What changes is who it is.** The token's `sub` claim is the agent's identity.
agentgateway validates the signature and injects that identity into the
authorization call itself — the agent never asserts it and cannot override it.

---

## Before you start

Four preconditions, each with the command that proves it. Do not skip these:
every one of them has been the actual cause of a failure further down.

```bash
# 1. The stack is running — 12 services, none exited except cedar-loader
docker compose ps

# 2. The signing key exists. If auth/jwks.json is missing, no token verifies.
ls auth/jwks.json || python3 scripts/agent_token.py init

# 3. The gateway is answering
curl -s -o /dev/null -w "%{http_code}\n" $GATEWAY/v1/models      # 401, not 000

# 4. The gateway accepted its config (a config error is fatal and it exits)
docker compose ps agentgateway --format "{{.Status}}"            # Up ...
docker compose logs agentgateway 2>&1 | grep -E "\| Error:" || echo "clean start"
```

Step 3 returning **401** is the correct answer — you sent no token. `000` means
nothing is listening, and you have a port or startup problem, not an onboarding
problem.

Do not grep the gateway log for "error" generally: its ordinary `info` request
lines contain an `error=` field and will match, which makes a healthy gateway
look broken. A fatal config problem appears as a line beginning `Error:` and the
container will not be `Up`.

There is deliberately no precondition for "the model backend is reachable". You
find that out at call time as a **503**, and the distinction matters: a 503 means
every platform control passed.

If you ran `agent_token.py init` just now, restart the gateway so it picks up
the new JWKS: `docker compose restart agentgateway`.

---

## Step 1 — Choose the agent id

The id is used in two places and **must be byte-identical in both**:

- the Cedar principal — `AgentMesh::Agent::"<id>"`
- the JWT `sub` claim, which `scripts/agent_token.py issue <id>` sets

A mismatch is not an error anywhere. The token verifies, the request is
authenticated, and Cedar then evaluates a principal that does not exist — so
every call is denied with an empty diagnostic (`cedar:deny []`), which looks
exactly like a missing policy. Get this right and you avoid the single most
confusing failure on the platform.

Guidance:

- Lowercase, hyphenated, stable. It appears in audit logs forever.
- Name the **deployable**, not the framework's internal roles. One crew with a
  researcher and a writer is normally one agent id, because they share a process
  and a credential.
- Use a separate id per identity you want to govern *differently*. If two
  components need different model tiers, they need different ids and different
  tokens.
- Do not encode the environment (`-prod`) unless you genuinely run separate
  policy sets; the tenant attribute is the intended axis for that.

```bash
export AGENT=my-first-agent
```

---

## Step 2 — Decide the governance profile

Five attributes. Each is read by a specific policy, and the table says which —
so when a call is denied you can work backwards from the policy name in the log.

| Attribute | Type | What it controls | Read by |
|---|---|---|---|
| `registry_status` | string | Anything other than `"approved"` denies everything, whatever else is set | `01-registry-admission` |
| `tenant` | string | Must equal the resource's `tenant`, or the call is denied | `02-tenant-isolation` |
| `roles` | list | `model-user` permits LLM calls and model discovery; `tool-user` permits tool calls. Separate grants | `03`, `04`, `06` |
| `allowed_model_tiers` | list | Which model tiers this agent may reach — `["local"]` pins it to on-premises inference | `03-model-access-by-tier` |
| `clearance` | number | Must be **≥** a tool's `risk_score` | `04-tool-access-by-clearance` |

### Choosing

**`roles`** — grant only what the agent uses. An agent with no tools gets
`["model-user"]`. Adding `tool-user` "just in case" widens the blast radius of a
stolen token for no benefit; grant it when a tool is actually wired.

**`allowed_model_tiers`** — this is the most consequential choice, because it is
what actually keeps data on-premises. `["local"]` means the agent physically
cannot reach a hosted provider, whatever its prompt contains and whatever model
name it asks for.

That matters more than it may appear. PII screening does **not** block names:
`PERSON` is deliberately absent from `PRESIDIO_ENTITIES` because name-level NER
produces false positives that are fatal to multi-turn agents (spaCy reads "MoE"
as a person). So for any agent whose context accumulates — which is every
framework agent — `allowed_model_tiers: ["local"]` is the control doing the
work. See [Security & governance](security-and-governance.md#2-pii--two-layers-because-neither-alone-is-enough).

**`clearance`** — only relevant if the agent calls tools. Compare against the
`risk_score` of the tools it needs; the shipped tools use 1 (`search_docs`),
3 (`query_database`) and 5 (`drop_table`). Start low.

**`tenant`** — `acme` unless you are deliberately running multiple tenants. It
must match the tenant on every model and tool the agent uses, and on the
`AgentMesh::Platform::"gateway"` entity used for model discovery.

---

## Step 3 — Register the principal

Add the entity to `cedar/entities.json`. It is a flat JSON array; append to it.

```json
{
  "uid": { "type": "AgentMesh::Agent", "id": "my-first-agent" },
  "attrs": {
    "registry_status": "approved",
    "tenant": "acme",
    "roles": ["model-user"],
    "allowed_model_tiers": ["local"],
    "clearance": 1
  },
  "parents": []
}
```

Any key beginning with `_` — such as `_comment` — is documentation and is
stripped before evaluation, so use it to record *why* an agent has the profile it
has. Future you will want that.

> `registry_status` is set by hand here because agentregistry is not running on
> this deployment. When it is, this field should be reconciled against the
> catalogue rather than asserted — see [Appendix A](#appendix-a--catalogue-registration).

---

## Step 4 — Register every model the agent will ask for

**This is the step most often got wrong.** Cedar authorizes the model name as a
resource, matched as an exact string. `gemma4:latest` and `gemma4` are two
different resources; registering one does not register the other.

### Find out what your framework actually sends

Do not guess, and do not trust the value you configured — SDKs rewrite model
names. Make one call and let the platform tell you:

```bash
TOKEN=$(python3 scripts/agent_token.py issue $AGENT)
# ...configure your agent with this token and run it once. It will fail. Then:
docker compose logs --tail=20 cedar-shim | grep $AGENT
```

```
cedar-shim DENY agent=my-first-agent action=call_model resource=gemma4:latest
           hitl=False (cedar:deny [])
```

`resource=` is the exact string that arrived. cedar-shim logs it **even when it
denies**, which makes the first failed call a discovery tool rather than a dead
end. This works identically for every framework.

> **Do not use `GET /v1/models` for this.** It returns the gateway's *routing
> patterns* — `gpt-*`, `anthropic/*`, `*` — not concrete model names. Those
> patterns are not Cedar resources and asking for one by name will be denied.
> The endpoint exists so conformant clients do not break; it is not a catalogue
> of what you may call.

### Register it

```json
{
  "uid": { "type": "AgentMesh::Model", "id": "gemma4:latest" },
  "attrs": { "tier": "local", "tenant": "acme", "provider": "ollama" },
  "parents": []
}
```

| Attribute | Meaning |
|---|---|
| `tier` | `local` = on-premises; `hosted` = leaves the building. Must intersect the agent's `allowed_model_tiers` |
| `tenant` | Must equal the agent's tenant |
| `provider` | Documentation; routing is decided in `agentgateway/config.yaml` by name |

Register **every** name the agent may send, including fallbacks and any
summarisation or embedding model the framework uses internally. A framework that
quietly uses a different model for one internal step will be denied on that step
only, which presents as an intermittent failure.

---

## Step 5 — Register tools and MCP servers (skip if none)

Only if the agent calls tools through the gateway.

1. Add the server to `mcp.targets` in `agentgateway/config.yaml`. Without a
   route it is unreachable regardless of policy.
2. Add an `AgentMesh::Tool` entity:

```json
{
  "uid": { "type": "AgentMesh::Tool", "id": "search_docs" },
  "attrs": { "risk": "read", "risk_score": 1, "tenant": "acme" },
  "parents": []
}
```

Without a `risk_score` no `permit` matches and every call to it is denied.

Tools with `risk: "destructive"` additionally require a verified human approval
from a Temporal workflow, whatever the agent's clearance — the agent cannot
supply that flag itself. See
[Orchestration & HITL](orchestration-and-hitl.md).

---

## Step 6 — Load the policy set and prove it loaded

```bash
docker compose restart cedar-loader
docker compose logs --tail=6 cedar-loader
```

Expected:

```
cedar-loader: PUT /v1/data -> 200
cedar-loader: PUT /v1/policies -> 200
cedar-loader: loaded 6 policies: 01-registry-admission, 02-tenant-isolation,
              03-model-access-by-tier, 04-tool-access-by-clearance,
              05-destructive-tools-require-hitl, 06-list-models
cedar-loader: done
```

The loader reads back what the PDP actually stored and exits non-zero if the
count disagrees. That check exists because a failed load leaves an **empty**
policy set, and Cedar is deny-by-default — so the symptom is every request
returning 403, three containers away from the cause.

`restart` is the right verb: the loader is a one-shot that exits 0, and
`docker compose up -d` will not re-run it if nothing changed.

---

## Step 7 — Write a test for the grant

Two assertions in `tests/cedar/test_policies.py`: one thing the agent may do,
one it may not.

```python
def test_my_first_agent_can_use_the_local_model():
    assert allow("my-first-agent", "call_model", "gemma4:latest") == "Allow"


def test_my_first_agent_cannot_reach_a_hosted_model():
    assert allow("my-first-agent", "call_model", "gpt-4.1") == "Deny"
```

```bash
uv run pytest tests/cedar/ -q
```

This is not ceremony. **Cedar silently skips a policy that errors at evaluation
time** rather than failing the request — so a malformed rule does not announce
itself, it simply stops applying, and whatever `permit` remains decides the
call. The `allow()` helper asserts `errors == []` on every evaluation, which is
the only thing standing between you and a policy that vanished.

Write the *denial* test especially. A permit test passing proves little; a denial
test failing tells you a boundary you believed in does not exist.

---

## Step 8 — Issue the credential

```bash
python3 scripts/agent_token.py issue $AGENT --ttl 604800

# or write it straight into .env as GATEWAY_TOKEN, which avoids shell
# substitution entirely — the one step that differs between POSIX shells
# and PowerShell:
python3 scripts/agent_token.py issue $AGENT --ttl 604800 --write-env
```

| TTL | Use for |
|---|---|
| `3600` (default) | interactive testing |
| `604800` (7 days) | an agent reading it from a `.env` |
| shorter | anything you can re-issue automatically |

The token is a bearer credential: anyone holding it is that agent. Put it where
you would put a password — a gitignored `.env`, a secret manager — never in a
committed file.

**It expires.** An agent that starts returning 401 after working for a week has a
stale credential, not a broken policy. See [Day 2](#day-2-operations).

In production, replace this development issuer with your identity provider:
point `jwtAuth.jwks.file` in `agentgateway/config.yaml` at its key set. Nothing
else in the configuration changes.

---

## Step 9 — Point the agent at the gateway

Three settings. Whatever your framework calls them:

| Setting | Value | Notes |
|---|---|---|
| Base URL | `$GATEWAY/v1` | **Must include `/v1`.** Omitting it is a 404 that looks like the gateway is down |
| API key | the JWT from step 8 | Not a provider key. The gateway is the provider as far as your agent is concerned |
| Model | a name registered in step 4 | Exactly as registered |

Any OpenAI-compatible client works, because that is all the gateway is. See
[Framework integrations](framework-integrations.md) for the exact parameter
names per framework and the quirks worth knowing.

### What the agent must never send

- **An identity header.** `x-agentmesh-agent` is set by the gateway from the
  validated token. A client-set one is not forwarded to the authorization
  service at all, so it has no effect — but sending it signals a misreading of
  where authorization happens.
- **`hitl_approved`** or any other authorization claim in the body. Approval is
  read from the Temporal workflow, never from the request.
- **A provider API key.** If the agent still holds one, remove it. The whole
  point is that credentials for providers live in the gateway, not in agents.

---

## Step 10 — Verify

Five cases. Run them all; each one proves a different control, and passing only
the first tells you nothing about whether the agent is actually constrained.

```bash
TOKEN=$(python3 scripts/agent_token.py issue $AGENT)

ask() {   # ask <token> <model>
  curl -s -o /dev/null -w "%{http_code}\n" -X POST $GATEWAY/v1/chat/completions \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $1" \
    -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
}
```

| # | Command | Expect | Proves |
|---|---|---|---|
| 1 | `ask "$TOKEN" gemma4:latest` | `200` (or `503`, see below) | the grant works |
| 2 | `ask "$TOKEN" gpt-4.1` | `403` | tier confinement |
| 3 | `ask "$TOKEN" not-registered:1b` | `403` | unregistered models are denied |
| 4 | `ask "" gemma4:latest` | `401` | no credential, no entry |
| 5 | forged header, below | `403` / unchanged identity | identity cannot be asserted |

> **A `503` on case 1 is a pass, not a failure.** It means every control allowed
> the call and the *model backend* could not be reached — usually an Ollama
> server that is off or firewalled. Confirm with the log: a line reading
> `ALLOW agent=… (cedar:allow ['03-model-access-by-tier'])` is the platform
> working. `upstream call failed: Connect: deadline has elapsed` in the body
> says the same thing. Fix the backend, not the policy.

```bash
# 5 — claim to be someone else
OTHER=$(python3 scripts/agent_token.py issue pii-handler)
curl -s -o /dev/null -X POST $GATEWAY/v1/chat/completions \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $OTHER" \
  -H "x-agentmesh-agent: $AGENT" \
  -d '{"model":"gpt-4.1","messages":[{"role":"user","content":"hi"}]}'
docker compose logs --tail=2 cedar-shim
# the log must say agent=pii-handler — the header was ignored
```

Reading the denials: a bare `cedar:deny []` means **no permit matched** — usually
an unregistered model or a principal id typo. A *named* policy,
`cedar:deny ['01-registry-admission']`, means a specific `forbid` fired and the
filename tells you which.

### Confirm it was governed

Run the real agent, then:

```bash
docker compose logs cedar-shim | grep $AGENT | tail -5
```

```
ALLOW agent=my-first-agent action=call_model resource=gemma4:latest
      hitl=False (cedar:allow ['03-model-access-by-tier'])
```

One line per LLM call, and **no other principal**. If you see calls attributed to
something else, part of your agent is bypassing the gateway. Token counts come
back in each response's `usage` block; see
[Observability](observability.md).

---

## Day 2 operations

### Rotation

Tokens expire. Re-issue and restart the agent:

```bash
python3 scripts/agent_token.py issue $AGENT --ttl 604800
```

Nothing on the platform side changes — the Cedar entity is untouched and no
reload is needed.

### Revocation — read this before you need it

**There is no token blacklist.** A JWT is self-contained; once issued it keeps
authenticating until `exp` passes, and neither deleting a file nor restarting
the gateway invalidates it.

What actually stops an agent is **removing its authority**, not its credential:

```bash
# Suspend: the token still authenticates, and now authorizes nothing
#   set "registry_status": "suspended" in cedar/entities.json
docker compose restart cedar-loader
```

Policy 01 then denies every action for that principal. Deleting the entity
entirely has the same effect — Cedar is deny-by-default, so an unknown principal
gets nothing.

Rotating the signing key (`agent_token.py init --force`) invalidates **every**
token at once, for every agent. That is the blunt instrument; use it if the
signing key itself is compromised, and expect to re-issue everything.

### Changing a profile

Edit the entity, `docker compose restart cedar-loader`, and the next call is
decided by the new rules. No token re-issue, no agent restart, no gateway
restart. Add a test for whatever boundary you just moved.

---

## When it does not work

Symptoms in the order you will meet them.

| Symptom | Cause | Fix |
|---|---|---|
| `000` / connection refused | wrong port | `grep GATEWAY_PORT .env`; default is 4000, this machine 4100 |
| `404` on a call that should work | base URL missing `/v1` | use `$GATEWAY/v1` |
| `401 no bearer token found` | no token, or the framework did not send it | confirm the key setting reached the client |
| `401` after working for days | token expired | re-issue (step 8) |
| `401` right after `init` | gateway holds the old JWKS | `docker compose restart agentgateway` |
| `403 cedar:deny []` | no permit matched — model not registered, or agent id mismatch between entity and token `sub` | compare `resource=` in the cedar-shim log against `cedar/entities.json` |
| `403 cedar:deny ['01-registry-admission']` | `registry_status` is not `approved` | fix the entity, reload |
| `403 cedar:deny ['02-tenant-isolation']` | agent and resource tenants differ | align them |
| `403` only on *some* calls | the framework uses a second model internally for one step | find it in the cedar-shim log and register it |
| `403 Request blocked: content contains …` | PII guardrail | the response body names the entity; see [Security & governance](security-and-governance.md) |
| `503 upstream call failed` | authorization **passed**; the model backend is unreachable | check the backend. Ollama binds `127.0.0.1` by default, so a remote one needs `OLLAMA_HOST=0.0.0.0` on *that* machine |
| Every request 403s, including ones that worked | the policy load failed and left an empty set | `docker compose logs cedar-loader` |
| `403` on `GET /v1/models` | agent lacks `model-user` | grant it, or ignore — most clients do not need it |
| Cedar test passes but the call is denied | entity file edited but not loaded | `docker compose restart cedar-loader` |

Two diagnostics worth knowing:

```bash
# What identity and resource did the platform actually see?
docker compose logs --tail=20 cedar-shim

# Which text tripped the PII guardrail? (logs the detected value; off by default)
ADAPTER_DEBUG_MATCHES=1 docker compose up -d presidio-adapter
docker compose logs --tail=20 presidio-adapter
```

---

## Appendix A — Catalogue registration

> **Not available on this deployment.** agentregistry has not been started here.
> The steps above are complete and sufficient without it; this appendix is the
> intended flow for when the catalogue is running. See
> [`registry/README.md`](../registry/README.md) for agents currently pending
> publication.

The registry is the record of *what has been approved to run here*. It does not
enforce anything — a registry cannot stop a process from starting — but it is
where approval is decided, and `registry_status` in Cedar should be derived from
it rather than asserted by hand.

```bash
arctl init agent my-first-agent
arctl publish --registry http://localhost:12121
# approve in the UI at :12121, then reconcile registry_status in Cedar
```

---

## Appendix B — Reference

### Agent entity attributes

| Attribute | Required | Example |
|---|---|---|
| `registry_status` | yes | `"approved"` |
| `tenant` | yes | `"acme"` |
| `roles` | yes | `["model-user"]` |
| `allowed_model_tiers` | for model use | `["local"]` |
| `clearance` | for tool use | `2` |
| `_comment` | no | stripped before evaluation |

### The six policies

| File | Effect |
|---|---|
| `01-registry-admission` | `forbid` unless `registry_status == "approved"` |
| `02-tenant-isolation` | `forbid` unless principal and resource tenants match |
| `03-model-access-by-tier` | `permit` model calls for `model-user` within `allowed_model_tiers` |
| `04-tool-access-by-clearance` | `permit` tool calls for `tool-user` where `clearance >= risk_score` |
| `05-destructive-tools-require-hitl` | `forbid` destructive tools without a verified Temporal approval |
| `06-list-models` | `permit` model discovery for `model-user` |

A `forbid` always beats a `permit`. Cedar is deny-by-default: with no matching
`permit`, the answer is no.

### Commands

| | |
|---|---|
| `python3 scripts/agent_token.py init` | create the dev signing key and JWKS (once) |
| `python3 scripts/agent_token.py issue <id> --ttl <s>` | mint a credential |
| `docker compose restart cedar-loader` | apply entity or policy edits |
| `docker compose restart agentgateway` | apply gateway config or JWKS changes |
| `uv run pytest tests/cedar/ -q` | check the policy set |
| `docker compose logs cedar-shim` | every authorization decision |

### Related

- [Framework integrations](framework-integrations.md) — per-framework wiring and quirks
- [Security & governance](security-and-governance.md) — what the controls do and do not cover
- [Getting started](getting-started.md) — bringing the platform up
- [Architecture](architecture.md) — where each control sits in the request path
