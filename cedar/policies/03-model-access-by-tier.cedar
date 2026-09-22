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
