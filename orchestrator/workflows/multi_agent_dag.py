from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities.gateway_client import invoke_agent_step
    from orchestrator.activities.compensations import rollback_action

@workflow.defn
class MultiAgentDAGWorkflow:
    @workflow.run
    async def run(self, pipeline_spec: dict) -> dict:
        compensations = []
        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_attempts=3,
            non_retryable_error_types=["CedarPolicyViolationError"]
        )

        try:
            res1 = await workflow.execute_activity(
                invoke_agent_step,
                {"action": "create_staging_workspace", "spec": pipeline_spec},
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=retry_policy
            )
            compensations.append(("delete_staging_workspace", res1["workspace_id"]))

            res2 = await workflow.execute_activity(
                invoke_agent_step,
                {"action": "provision_test_db", "workspace_id": res1["workspace_id"]},
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy
            )
            compensations.append(("teardown_test_db", res2["db_id"]))

            return {"status": "Pipeline_Completed", "workspace": res1, "db": res2}

        except Exception as failure:
            for comp_action, target_id in reversed(compensations):
                await workflow.execute_activity(
                    rollback_action,
                    {"action": comp_action, "target_id": target_id},
                    start_to_close_timeout=timedelta(minutes=2)
                )
            raise failure
