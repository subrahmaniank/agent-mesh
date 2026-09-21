// Permit authorized operators to invoke agents
permit (
    principal in AgentPlatform::UserGroup::"AuthorizedOperators",
    action == AgentPlatform::Action::"invoke",
    resource in AgentPlatform::Agent::"default-agent"
);

// Allow agents to execute MCP tools only when user clearance meets resource risk
permit (
    principal,
    action == AgentPlatform::Action::"call_tool",
    resource
)
when {
    context.user_clearance >= resource.risk_level_score
};

// Absolute Invariant: Forbid destructive operations unless HITL is confirmed
forbid (
    principal,
    action == AgentPlatform::Action::"call_tool",
    resource
)
when {
    (resource.risk_level == "destructive" ||
     context.payload.contains("DROP TABLE") ||
     context.payload.contains("DELETE FROM") ||
     context.payload.contains("rm -rf")) &&
    context.hitl_approved != true
};
