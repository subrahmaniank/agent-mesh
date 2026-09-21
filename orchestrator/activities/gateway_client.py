from temporalio import activity

@activity.defn
async def invoke_agent_step(params: dict) -> dict:
    action = params.get("action")
    if action == "create_staging_workspace":
        return {"workspace_id": "ws-98721"}
    elif action == "provision_test_db":
        return {"db_id": "db-sandbox-401"}
    return {"status": "ok"}

@activity.defn
async def request_human_approval(payload: dict) -> bool:
    activity.logger.info(f"Notification: Action {payload} requires approval.")
    return True
