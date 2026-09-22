import asyncio

from temporalio.worker import Worker

from orchestrator.activities.compensations import rollback_action
from orchestrator.activities.gateway_client import invoke_agent_step, request_human_approval
from orchestrator.temporal_config import ORCHESTRATOR_TASK_QUEUE, connect
from orchestrator.workflows.hitl_approval import HITLApprovalWorkflow
from orchestrator.workflows.multi_agent_dag import MultiAgentDAGWorkflow
from observability import telemetry


async def main():
    telemetry.init("agentmesh-orchestrator")
    client = await connect()
    # Control plane: workflows plus the orchestration-plane activities.
    # Compensations inherit the workflow's task queue, so rollback_action MUST
    # be registered here or a saga unwind stalls on "activity not registered".
    worker = Worker(
        client,
        task_queue=ORCHESTRATOR_TASK_QUEUE,
        workflows=[HITLApprovalWorkflow, MultiAgentDAGWorkflow],
        activities=[invoke_agent_step, request_human_approval, rollback_action],
    )
    print(f"Orchestrator worker started on queue: {ORCHESTRATOR_TASK_QUEUE}")
    try:
        await worker.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print("\nOrchestrator worker shut down cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
