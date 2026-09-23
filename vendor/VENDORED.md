# Vendored compose files

These are upstream files, committed here and pinned to a specific commit, so
that `vendor_stacks.py up` needs no network and a fresh clone reproduces exactly
what was running. Do not edit them by hand — local changes belong in the
matching `*.override.yml`, which is ours.

| stack | repo | path | commit | upstream date |
|---|---|---|---|---|
| `agentcontrol` | `agentcontrol/agent-control` | `docker-compose.yml` | `c464e500d45a9dc7df072697d41126e73757001a` | 2026-04-19 |
| `agentregistry` | `agentregistry-dev/agentregistry` | `docker/docker-compose.yml` | `98ce4b083419e002ead95e81804bda3a044eac77` | 2026-08-05 |
| `langfuse` | `langfuse/langfuse` | `docker-compose.yml` | `0dd0a7fbe2feb300b8776f02b3684eeee3fbceab` | 2026-09-21 |

Re-pin to the latest upstream commit, review the diff, then commit it:

```
python3 scripts/vendor_stacks.py refresh <stack>
```
