from temporalio import activity

# Compensations are retried by the workflow and may also be re-delivered by
# Temporal, so each must be safe to run more than once. This in-memory set
# stands in for the idempotency key a real implementation would persist
# alongside the resource it tears down.
_COMPLETED: set = set()


@activity.defn
async def rollback_action(params: dict) -> bool:
    """Undo a previously completed pipeline step. Must be idempotent."""
    action = params["action"]
    target_id = params["target_id"]
    key = f"{action}:{target_id}"

    if key in _COMPLETED:
        activity.logger.info(f"Rollback {key} already applied; skipping.")
        return True

    activity.logger.warning(f"Executing rollback: {action} on {target_id}")
    _COMPLETED.add(key)
    return True
