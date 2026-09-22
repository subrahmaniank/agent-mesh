# Agent registry — what is approved to run here

agentregistry is the catalogue and approval workflow: agents, MCP servers,
skills and prompts, with versioning, publisher verification and enrichment
scores. It is the source of truth for *what has been approved*.

> **A registry cannot stop a process from starting.** It records intent.
> Enforcement happens at the gateway: an agent whose identity is not approved
> and granted a Cedar role gets a 401 or 403 on every model, tool and MCP call.
> It can run, and it can do nothing. See `docs/architecture.md`.

No manifests are checked in here yet, deliberately: artifact schemas belong to
`arctl`, and inventing one would put a file in this repo that the tool rejects.
Generate them with the CLI instead.

## Publishing an agent

```bash
# 1. Scaffold — arctl writes the manifest in the schema its version expects
arctl init agent research-assistant

# 2. Publish into the catalogue
arctl publish --registry http://localhost:12121

# 3. Approve it (registry UI at :12121, or via the REST API).
#    Until approved, policy 1 in cedar/policies.cedar denies everything:
#      forbid (principal, action, resource)
#      unless { principal.registry_status == "approved" };
```

## Connecting registry identity to authorization

Approval in the registry does not by itself grant anything. The agent must also
exist as a Cedar principal with the roles it needs. Add it to
`cedar/entities.json`:

```json
{
  "uid": { "type": "AgentMesh::Agent", "id": "research-assistant" },
  "attrs": {
    "registry_status": "approved",
    "tenant": "acme",
    "clearance": 3,
    "roles": ["model-user", "tool-user"],
    "allowed_model_tiers": ["local", "hosted"]
  },
  "parents": []
}
```

then reload:

```bash
docker compose up cedar-loader
```

`allowed_model_tiers` is the lever for keeping an agent on-premises: an agent
granted only `local` cannot reach a hosted provider whatever model name it
asks for. `cedar/entities.json` ships a `pii-handler` agent configured that
way, and `tests/cedar/test_policies.py` proves it.

Keeping `registry_status` in step with the registry is a manual copy today.
Deriving the Cedar entity set from the registry API is the obvious next step
and would remove the drift.

## MCP servers

MCP servers are registry artifacts too. Once approved, add the server to the
`mcp.targets` list in `agentgateway/config.yaml` and a matching
`AgentMesh::Tool` entity in `cedar/entities.json`. A server that is not in
`mcp.targets` has no route, and a tool with no Cedar entity has no permit —
both are denials, from different directions.
