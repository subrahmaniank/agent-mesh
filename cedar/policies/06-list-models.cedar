// ─────────────────────────────────────────────────────────────────────────
// 6. Model discovery. OpenAI-compatible clients call GET /v1/models before
//    making a request; without this they get a 403 on a standard endpoint.
//
//    Granted to `model-user` rather than to everyone: an agent that may not
//    call a model has no reason to read the catalogue of models. Policies 1
//    and 2 still apply on top — an unapproved agent, or one from another
//    tenant, is denied regardless.
// ─────────────────────────────────────────────────────────────────────────
permit (
    principal,
    action == AgentMesh::Action::"list_models",
    resource
)
when {
    principal.roles.contains("model-user")
};
