# Security & governance

Three independent controls, each owned by a different product: **who may act**
(Cedar), **what may leave** (PII guardrails), and **what a step may do**
(Agent Control).

---

## 1. Cedar IAM

Policies live in `cedar/policies.cedar` and are evaluated by cedar-agent, a
standalone Cedar PDP. agentgateway delegates to it via `extAuthz` on every call.

Cedar is **deny-by-default**: an agent gets nothing unless a `permit` matches,
and any `forbid` overrides every `permit`. That property is what turns the
registry from a list into enforcement.

### The six policies

| # | Rule | Effect |
|---|---|---|
| 1 | `forbid unless principal.registry_status == "approved"` | Unapproved agents can do nothing, whatever their clearance |
| 2 | `forbid unless principal.tenant == resource.tenant` | Tenant isolation on every action |
| 3 | `permit` when role `model-user` **and** the model's tier is in `allowed_model_tiers` | Model access by tier |
| 4 | `permit` when role `tool-user` **and** `clearance >= resource.risk_score` | Tool access by clearance |
| 5 | `forbid` destructive tools unless `context.hitl_approved` | Human approval gate |
| 6 | `permit` `list_models` when role `model-user` | Model discovery — `GET /v1/models`, which OpenAI-compatible clients probe. Authorized rather than waved through: policies 1 and 2 still apply, so an unapproved or wrong-tenant agent cannot even enumerate |

### Keeping an agent on-premises

`allowed_model_tiers` is the lever. The shipped `pii-handler` agent is granted
only `local`, so it can use the on-premises Ollama backend and is denied every
hosted provider — regardless of which model name it asks for. Changing that is a
policy edit, not a code change.

### Revocation: authority, not credentials

There is **no token blacklist**. A JWT is self-contained; once issued it keeps
authenticating until `exp` passes, and neither deleting a file nor restarting the
gateway invalidates it.

What stops an agent is removing its *authority*. Set `registry_status` to
anything but `"approved"` — or delete the entity outright — and reload:

```bash
docker compose restart cedar-loader
```

Policy 1 then denies every action for that principal. The token still
authenticates and authorizes nothing, which is the correct outcome: the audit
trail still attributes the attempts.

Rotating the signing key (`scripts/agent_token.py init --force`) invalidates
**every** token for **every** agent at once. That is the response to a
compromised signing key, not to a compromised agent.

Keep TTLs short enough that a forgotten revocation expires on its own.

### The approval flag cannot be forged

`context.hitl_approved` never comes from the request. An agent controls its own
headers and body, so anything it sets there is a claim, not a fact. cedar-shim
ignores both and reads the approval state from the Temporal workflow instead: a
request may *reference* a workflow id, but the verdict comes from Temporal. An
agent naming a workflow it never got approved gains nothing.

`tests/adapters/test_cedar_shim.py` asserts this directly — a request carrying
`hitl_approved: true` in headers *and* body still reaches Cedar with
`hitl_approved: false`.

### Editing policies

```bash
vim cedar/policies.cedar cedar/entities.json
uv run pytest tests/cedar/ -q      # verify before loading
docker compose restart cedar-loader     # reload into cedar-agent
```

Run the tests first. Cedar **skips a policy that raises at evaluation time**
rather than failing the request, so a malformed rule silently disappears and the
request is decided by whatever `permit` remains. `tests/cedar/` asserts the
engine reports zero errors across the whole agent × resource matrix, which is
what catches that class of mistake.

---

## 2. PII — two layers, because neither alone is enough

Requirement: PII must never reach a provider. No single control in this stack
delivers that.

| Layer | Mechanism | Can it redact? |
|---|---|---|
| agentgateway **regex** prompt guards | built-in patterns, inline | **Yes** — `mask`, the only native masking anywhere in the stack |
| agentgateway **webhook** → presidio-adapter | Presidio NER | **No** — `reject` or `audit` only |
| Agent Control controls | `deny` / `steer` / `observe` | **No** |

So: regex masks well-formed patterns (SSN, card, email) inline, and Presidio
**rejects** the identifiers its NER catches that regex missed — an IBAN, a
medical licence number, an IP address, a custom entity. Rejection, not
redaction, is what "never sent" actually requires.

It does **not** reject a person's name; `PERSON` is excluded from
`PRESIDIO_ENTITIES` for the reason given below.

Three things worth knowing:

- **The webhook cannot mask — settled.** agentgateway's guardrails overview
  lists webhook actions as *reject, audit*; its Guardrail Webhook API page
  describes *Pass, Mask, Reject*. The binary decides it:

      unknown variant `mask`, expected `reject` or `audit`

  So Presidio can block but never redact, and `GUARDRAIL_ACTION=mask` in the
  adapter has no effect on this build. Redaction is the regex layer's job.

- **Streaming is guarded only because it is switched on.** `guardrails.streaming`
  accepts `Enabled` | `Disabled`; the config sets `Enabled`. Without it,
  `stream: true` is an unguarded path straight out of the platform.

- **Fail-closed is explicit, not default.** The webhook's `failureMode` accepts
  `failClosed` | `failOpen`. Both request and response guards set `failClosed`:
  if Presidio is down, the call does not proceed unscreened.

- **The entity list is a deliberate trade, and `PERSON` is the expensive part.**
  `PRESIDIO_ENTITIES` is curated rather than empty, because empty means every
  entity Presidio knows — spaCy tags "France" in *"What is the capital of
  France?"* as `LOCATION`. Excluded: `LOCATION`, `DATE_TIME`, `NRP`, `URL`, and
  `PERSON`.

  `PERSON` was excluded after it made a real agent unusable. spaCy read "MoE"
  (mixture of experts) as a person's name, and because a sequential crew feeds
  each task's output into the next task's prompt, one false positive aborted the
  run on its second task. Name-level NER has too many false positives to gate
  traffic on, and in a multi-turn agent every false positive is fatal.

  What still holds the line is **Cedar, not Presidio**: an agent with
  `allowed_model_tiers: ["local"]` cannot reach a hosted provider whatever its
  prompt contains. Containment by destination, not detection by content.

  The cost is real and worth stating: a person's name typed into a prompt bound
  for a cloud provider is no longer blocked. Restore `PERSON` to
  `PRESIDIO_ENTITIES` if you accept the false positives.

### Why screening cannot follow the destination

The natural design is to screen harshly for calls leaving the premises and
lightly for on-prem ones. agentgateway v1.5.0 does not permit it. Verified:

| Attempt | Outcome |
|---|---|
| Read `model` from the guardrail envelope | envelope is exactly `{"body": {"messages": [...]}}` — no model, no route |
| Per-model `guardrails:` blocks | parse and apply, **but not to every message role**: with top-level `reject` and per-model `audit`, PII in a `user` turn passed and the same text in a `system` or `assistant` turn was rejected |
| `headers: {x-agentmesh-model: llm.model}` on the webhook | validates, never arrives |

Any agent framework accumulates `assistant` turns, so the per-model route cannot
govern a conversation. Making this work needs a second adapter instance wired to
the hosted model entries, or a gateway that passes route context to the webhook.

### Fail closed

If Presidio is unreachable, the adapter returns a **reject**, not a pass. An
unavailable scanner must not become an open door — inference leaves the
building, and an unscreened prompt reaching a provider is unrecoverable.
`test_presidio_unreachable_rejects_rather_than_passing` guards this.

The adapter also rejects on a malformed guardrail envelope and on a Presidio
error response, for the same reason.

### Presidio has an outbound network dependency

Presidio's `EmailRecognizer` calls `tldextract`, which downloads the IANA public
suffix list from `publicsuffix.org` on first use. On a host without egress — or
behind a TLS-intercepting proxy — that call **hangs** rather than failing, so
analysis never returns and, with fail-closed behaviour, every request is
rejected.

Before deploying the analyzer into an air-gapped network, pre-seed the
`tldextract` cache in the container image or pin the extractor to its bundled
snapshot. This is easy to miss because it only manifests once egress is
actually restricted.

---

## 3. Agent Control — policy inside the agent

Controls in `agentcontrol/controls/` are JSON: `scope` (which step types and
stages) + `condition` (selector + evaluator) + `action` (`deny` / `steer` /
`observe`). They are managed in the Agent Control UI and take effect immediately
without redeploying agents.

Built-in evaluators: `regex`, `list`, `json`, `sql`, and `galileo.luna2` (which
carries `pii_detection` and `prompt_injection` metrics). A custom evaluator is a
Python class registered through a `pyproject.toml` entry point; it runs
server-side and may make HTTP calls, so Presidio can also be reached from here
if you want NER detection at step granularity as well as on the wire.

Agent Control enforces **only** around `@control()`-decorated functions. It has
no proxy mode, so it governs the agent's internals while the gateway governs the
wire. Neither substitutes for the other.

---

## Credentials

| Secret | Held by | Never seen by |
|---|---|---|
| LLM provider API keys | agentgateway (from `.env`) | agents |
| `GATEWAY_TOKEN` | the agent | — it is the agent's own identity |
| Agent identity header | set by agentgateway after authn | must not be settable by a client |

⚠️ **The most important thing to verify on first run:** cedar-shim reads the
agent identity from a header agentgateway is expected to populate *after*
authenticating the caller. If a client can inject that header itself, it can
impersonate any agent and the entire authorization model collapses. Confirm
agentgateway strips or overwrites it on ingress before trusting this stack with
anything real.
