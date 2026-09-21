from temporalio import activity

@activity.defn
async def rollback_action(params: dict) -> bool:
    activity.logger.warn(f"Executing rollback: {params['action']} on {params['target_id']}")
    return True
