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
