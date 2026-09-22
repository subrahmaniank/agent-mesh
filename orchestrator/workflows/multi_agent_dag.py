import asyncio
from datetime import timedelta
from typing import List, Tuple

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities.compensations import rollback_action
    from orchestrator.activities.gateway_client import (
        CEDAR_POLICY_VIOLATION,
        call_gateway_tool,
        invoke_agent_step,
    )
    from orchestrator.temporal_config import AGENT_FLEET_TASK_QUEUE

# Forward progress. A Cedar denial is terminal -- retrying cannot change a
# policy decision -- so it is marked non-retryable by type name.
STEP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_attempts=3,
    non_retryable_error_types=[CEDAR_POLICY_VIOLATION],
)

# Compensations must terminate. Without an explicit policy Temporal retries
# forever, so a permanently-failing rollback hangs the workflow instead of
# surfacing the failure.
COMPENSATION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=3,
)


@workflow.defn
class MultiAgentDAGWorkflow:
    @workflow.run
    async def run(self, pipeline_spec: dict) -> dict:
        compensations: List[Tuple[str, str]] = []
        tool = pipeline_spec.get("tool", "database_read")
        arguments = pipeline_spec.get("arguments", {"query": "SELECT count(*) FROM transactions"})

        try:
            res1 = await workflow.execute_activity(
                invoke_agent_step,
                {"action": "create_staging_workspace", "spec": pipeline_spec},
                start_to_close_timeout=timedelta(minutes=3),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=STEP_RETRY,
            )
            compensations.append(("delete_staging_workspace", res1["workspace_id"]))

            res2 = await workflow.execute_activity(
                invoke_agent_step,
                {"action": "provision_test_db", "workspace_id": res1["workspace_id"]},
                start_to_close_timeout=timedelta(minutes=5),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=STEP_RETRY,
            )
            compensations.append(("teardown_test_db", res2["db_id"]))

            # Agent-plane step. Routed explicitly to the remote fleet queue so
            # the gateway call originates from the sandbox, not the control
            # plane. A Cedar denial here fails fast and unwinds both
            # provisioning steps above.
            res3 = await workflow.execute_activity(
                call_gateway_tool,
                {
                    "tool": tool,
                    "arguments": arguments,
                    "agent_id": pipeline_spec.get("agent_id", "default-agent"),
                    "tenant_id": pipeline_spec.get("tenant_id", "tenant-enterprise"),
                    "hitl_workflow_id": pipeline_spec.get("hitl_workflow_id"),
                },
                task_queue=AGENT_FLEET_TASK_QUEUE,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=STEP_RETRY,
            )

            return {"status": "Pipeline_Completed", "workspace": res1, "db": res2, "tool_call": res3}

        except asyncio.CancelledError:
            # CancelledError derives from BaseException, so the original
            # `except Exception` never caught it and a cancelled pipeline
            # leaked its workspace and database. Shield the unwind so the
            # compensation activities are not cancelled along with us.
            await asyncio.shield(self._compensate(compensations))
            raise

        except Exception:
            await self._compensate(compensations)
            raise

    async def _compensate(self, compensations: List[Tuple[str, str]]) -> None:
        """Unwind completed steps in LIFO order.

        Each compensation is guarded independently: one failing rollback must
        not skip the remaining ones, nor replace the original failure that
        triggered the unwind.
        """
        failures: List[str] = []
        for action, target_id in reversed(compensations):
            try:
                await workflow.execute_activity(
                    rollback_action,
                    {"action": action, "target_id": target_id},
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=COMPENSATION_RETRY,
                )
            except Exception as exc:
                failures.append(f"{action}({target_id}): {exc}")
                workflow.logger.error(f"Compensation failed for {action} on {target_id}: {exc}")
                continue

        if failures:
            workflow.logger.error(f"{len(failures)} compensation(s) failed: {failures}")
