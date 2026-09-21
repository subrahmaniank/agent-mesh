import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker
from orchestrator.workflows.hitl_approval import HITLApprovalWorkflow
from orchestrator.workflows.multi_agent_dag import MultiAgentDAGWorkflow
from orchestrator.activities.gateway_client import invoke_agent_step, request_human_approval

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TASK_QUEUE = "remote-agent-fleet-prod"

async def main():
    print(f"Connecting outbound to Temporal at {TEMPORAL_HOST} (No inbound ports)...")
    client = await Client.connect(TEMPORAL_HOST)
    
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[HITLApprovalWorkflow, MultiAgentDAGWorkflow],
        activities=[invoke_agent_step, request_human_approval]
    )
    print(f"Worker listening on task queue: {TASK_QUEUE}")
    try:
        await worker.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print("\nRemote worker shut down cleanly.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

