"""Agent-plane worker.

Runs where the agent's work actually happens — a sandbox, a remote host, a
different network — and holds **no inbound ports**: it dials Temporal outbound
and polls. That is what lets the agent plane sit outside the control plane's
trust boundary.

It registers activities only. Workflows are scheduled on the orchestrator's
queue; MultiAgentDAGWorkflow routes its gateway-facing step here explicitly
with `task_queue=AGENT_FLEET_TASK_QUEUE`, so `call_gateway_tool` must be
registered on this worker or that step stalls with "activity not registered".
"""

import asyncio

from temporalio.worker import Worker

from observability import telemetry
from orchestrator.activities.gateway_client import (
    call_gateway_tool,
    call_model,
    invoke_agent_step,
)
from orchestrator.temporal_config import AGENT_FLEET_TASK_QUEUE, TEMPORAL_HOST, connect


async def main():
    telemetry.init("agentmesh-remote-runner")
    print(f"Connecting outbound to Temporal at {TEMPORAL_HOST} (no inbound ports)...")
    client = await connect()

    worker = Worker(
        client,
        task_queue=AGENT_FLEET_TASK_QUEUE,
        activities=[call_gateway_tool, call_model, invoke_agent_step],
    )
    print(f"Agent-plane worker listening on task queue: {AGENT_FLEET_TASK_QUEUE}")
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
