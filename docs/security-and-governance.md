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

### The five policies

| # | Rule | Effect |
|---|---|---|
| 1 | `forbid unless principal.registry_status == "approved"` | Unapproved agents can do nothing, whatever their clearance |
| 2 | `forbid unless principal.tenant == resource.tenant` | Tenant isolation on every action |
| 3 | `permit` when role `model-user` **and** the model's tier is in `allowed_model_tiers` | Model access by tier |
| 4 | `permit` when role `tool-user` **and** `clearance >= resource.risk_score` | Tool access by clearance |
| 5 | `forbid` destructive tools unless `context.hitl_approved` | Human approval gate |

### Keeping an agent on-premises

`allowed_model_tiers` is the lever. The shipped `pii-handler` agent is granted
only `local`, so it can use the on-premises Ollama backend and is denied every
hosted provider — regardless of which model name it asks for. Changing that is a
policy edit, not a code change.

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
docker compose up cedar-loader     # reload into cedar-agent
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
| agentgateway **webhook** → presidio-adapter | Presidio NER | **Reject/audit** — mask support is contested |
| Agent Control controls | `deny` / `steer` / `observe` | **No** |

So: regex masks well-formed patterns (SSN, card, email) inline, and Presidio
**rejects** anything its NER catches that regex missed — a person's name, an
address, a custom entity. Rejection, not redaction, is what "never sent"
actually requires.

Two limits worth knowing:

- **`mask` never applies to streamed responses.** Streaming must reject.
- **The webhook mask question is unresolved.** agentgateway's guardrails
  overview lists webhook actions as *reject, audit*; its Guardrail Webhook API
  describes *Pass, Mask, Reject*. `GUARDRAIL_ACTION` therefore defaults to
  `reject`, which is correct under either reading. Confirm before setting `mask`.

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
