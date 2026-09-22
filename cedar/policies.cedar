// AgentMesh IAM — evaluated by cedar-agent, enforced by agentgateway.
//
// Cedar is deny-by-default: an agent gets nothing unless a `permit` matches,
// and any `forbid` overrides every `permit`. That property is what makes an
// unregistered agent inert rather than merely unlisted.
//
// Principals are agents as published and approved in agentregistry; resources
// are the models and MCP tools configured in agentgateway.

// ─────────────────────────────────────────────────────────────────────────
// 1. Registry admission. Nothing acts unless the registry approved it.
//    This is the policy that turns the catalogue into enforcement.
// ─────────────────────────────────────────────────────────────────────────
forbid (principal, action, resource)
unless { principal.registry_status == "approved" };

// ─────────────────────────────────────────────────────────────────────────
// 2. Tenant isolation, applied to every action.
// ─────────────────────────────────────────────────────────────────────────
forbid (principal, action, resource)
unless { principal.tenant == resource.tenant };

// ─────────────────────────────────────────────────────────────────────────
// 3. Model access by tier. An agent granted only "local" cannot reach a
//    hosted provider no matter which model name it asks for — this is how
//    "that agent may not send anything off-premises" is expressed.
// ─────────────────────────────────────────────────────────────────────────
permit (
    principal,
    action == AgentMesh::Action::"call_model",
    resource
)
when {
    principal.roles.contains("model-user") &&
    principal.allowed_model_tiers.contains(resource.tier)
};

// ─────────────────────────────────────────────────────────────────────────
// 4. Tool access by clearance against the tool's registered risk score.
// ─────────────────────────────────────────────────────────────────────────
permit (
    principal,
    action == AgentMesh::Action::"call_tool",
    resource
)
when {
    principal.roles.contains("tool-user") &&
    principal.clearance >= resource.risk_score
};

// ─────────────────────────────────────────────────────────────────────────
// 5. Destructive tools always require a verified human approval, whatever
//    the agent's clearance. `forbid` beats any permit above.
//    The approval flag is supplied by the Temporal HITL workflow via
//    cedar-shim — never by the calling agent.
// ─────────────────────────────────────────────────────────────────────────
forbid (
    principal,
    action == AgentMesh::Action::"call_tool",
    resource
)
when {
    resource.risk == "destructive" &&
    context.hitl_approved != true
};
