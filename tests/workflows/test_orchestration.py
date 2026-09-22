"""Durable orchestration tests.

Saga and approval-gate behaviour, run against Temporal's time-skipping test
server so the 24h approval SLA resolves instantly. No Docker required.
"""

import uuid

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.activities.gateway_client import CEDAR_POLICY_VIOLATION
from orchestrator.temporal_config import AGENT_FLEET_TASK_QUEUE, ORCHESTRATOR_TASK_QUEUE
from orchestrator.workflows.hitl_approval import ApprovalDecision, HITLApprovalWorkflow
from orchestrator.workflows.multi_agent_dag import MultiAgentDAGWorkflow

# Recorded side effects, reset per test.
ROLLBACKS: list = []
TOOL_CALLS: list = []
FAILING_ROLLBACKS: set = set()


@pytest.fixture(autouse=True)
def _reset():
    ROLLBACKS.clear()
    TOOL_CALLS.clear()
    FAILING_ROLLBACKS.clear()


# --- stand-in activities, registered under the production names --------------

@activity.defn(name="invoke_agent_step")
async def stub_invoke_agent_step(params: dict) -> dict:
    action = params.get("action")
    if action == "create_staging_workspace":
        return {"workspace_id": "ws-test-1"}
    if action == "provision_test_db":
        return {"db_id": "db-test-1"}
    return {"status": "ok"}


@activity.defn(name="rollback_action")
async def stub_rollback_action(params: dict) -> bool:
    action = params["action"]
    if action in FAILING_ROLLBACKS:
        raise RuntimeError(f"rollback {action} is broken")
    ROLLBACKS.append(action)
    return True


@activity.defn(name="request_human_approval")
async def stub_request_human_approval(payload: dict) -> bool:
    return True


def make_tool_activity(behaviour: str):
    @activity.defn(name="call_gateway_tool")
    async def stub_call_gateway_tool(params: dict) -> dict:
        TOOL_CALLS.append(params.get("tool"))
        if behaviour == "deny":
            raise ApplicationError(
                "Gateway denied tool: Cedar Policy Violation",
                type=CEDAR_POLICY_VIOLATION,
                non_retryable=True,
            )
        if behaviour == "flaky":
            raise RuntimeError("transient gateway error")
        return {"status": "success", "decision": "ALLOW"}

    return stub_call_gateway_tool


async def run_pipeline(env: WorkflowEnvironment, behaviour: str = "allow"):
    """Start both workers (control plane + agent fleet) and run the DAG."""
    async with Worker(
        env.client,
        task_queue=ORCHESTRATOR_TASK_QUEUE,
        workflows=[MultiAgentDAGWorkflow, HITLApprovalWorkflow],
        activities=[stub_invoke_agent_step, stub_rollback_action, stub_request_human_approval],
    ), Worker(
        env.client,
        task_queue=AGENT_FLEET_TASK_QUEUE,
        activities=[make_tool_activity(behaviour), stub_request_human_approval],
    ):
        return await env.client.execute_workflow(
            MultiAgentDAGWorkflow.run,
            {"tool": "database_read", "arguments": {"query": "SELECT 1"}},
            id=f"dag-{uuid.uuid4()}",
            task_queue=ORCHESTRATOR_TASK_QUEUE,
        )


# --- saga --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_completes_and_runs_no_compensations():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await run_pipeline(env, "allow")
    assert result["status"] == "Pipeline_Completed"
    assert ROLLBACKS == []


@pytest.mark.asyncio
async def test_gateway_denial_unwinds_both_steps_in_lifo_order():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with pytest.raises(WorkflowFailureError):
            await run_pipeline(env, "deny")
    # LIFO: the database provisioned last is torn down first.
    assert ROLLBACKS == ["teardown_test_db", "delete_staging_workspace"]


@pytest.mark.asyncio
async def test_policy_denial_is_not_retried():
    """The retry policy named an error type that did not exist, so a Cedar
    denial was retried 3x. It must fail on the first attempt."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with pytest.raises(WorkflowFailureError):
            await run_pipeline(env, "deny")
    assert len(TOOL_CALLS) == 1, f"policy denial retried {len(TOOL_CALLS)} times"


@pytest.mark.asyncio
async def test_transient_error_is_still_retried():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with pytest.raises(WorkflowFailureError):
            await run_pipeline(env, "flaky")
    assert len(TOOL_CALLS) == 3, "non-policy failures should exhaust maximum_attempts"


@pytest.mark.asyncio
async def test_one_failing_compensation_does_not_skip_the_others():
    """A single broken rollback used to abort the whole unwind, leaking the rest."""
    FAILING_ROLLBACKS.add("teardown_test_db")
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with pytest.raises(WorkflowFailureError):
            await run_pipeline(env, "deny")
    assert ROLLBACKS == ["delete_staging_workspace"]


# --- HITL --------------------------------------------------------------------

async def start_hitl(env: WorkflowEnvironment):
    return await env.client.start_workflow(
        HITLApprovalWorkflow.run,
        {"action": "drop_customers_table"},
        id=f"hitl-{uuid.uuid4()}",
        task_queue=ORCHESTRATOR_TASK_QUEUE,
    )


@pytest.mark.asyncio
async def test_workflow_pauses_then_signal_unblocks_it():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=ORCHESTRATOR_TASK_QUEUE,
            workflows=[HITLApprovalWorkflow],
            activities=[stub_request_human_approval],
        ):
            handle = await start_hitl(env)
            assert await handle.query(HITLApprovalWorkflow.get_approval_state) == "Awaiting_Review"

            await handle.signal(
                HITLApprovalWorkflow.submit_approval,
                ApprovalDecision(approved=True, reviewer_id="alice", comments="ok"),
            )
            result = await handle.result()

    assert result["status"] == "Evaluated"
    assert result["approved"] is True
    assert result["reviewer"] == "alice"


@pytest.mark.asyncio
async def test_rejection_is_recorded():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=ORCHESTRATOR_TASK_QUEUE,
            workflows=[HITLApprovalWorkflow],
            activities=[stub_request_human_approval],
        ):
            handle = await start_hitl(env)
            await handle.signal(
                HITLApprovalWorkflow.submit_approval,
                ApprovalDecision(approved=False, reviewer_id="bob"),
            )
            result = await handle.result()

    assert result["approved"] is False


@pytest.mark.asyncio
async def test_unanswered_approval_times_out_after_the_sla():
    """Time-skipping advances past the 24h SLA without waiting."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=ORCHESTRATOR_TASK_QUEUE,
            workflows=[HITLApprovalWorkflow],
            activities=[stub_request_human_approval],
        ):
            handle = await start_hitl(env)
            result = await handle.result()

    assert result == {"status": "Timed_Out", "approved": False}
