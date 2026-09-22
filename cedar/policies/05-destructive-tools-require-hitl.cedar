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
