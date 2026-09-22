// ─────────────────────────────────────────────────────────────────────────
// 2. Tenant isolation, applied to every action.
// ─────────────────────────────────────────────────────────────────────────
forbid (principal, action, resource)
unless { principal.tenant == resource.tenant };
