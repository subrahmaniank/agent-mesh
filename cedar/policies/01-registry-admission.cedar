// ─────────────────────────────────────────────────────────────────────────
// 1. Registry admission. Nothing acts unless the registry approved it.
//    This is the policy that turns the catalogue into enforcement.
// ─────────────────────────────────────────────────────────────────────────
forbid (principal, action, resource)
unless { principal.registry_status == "approved" };
