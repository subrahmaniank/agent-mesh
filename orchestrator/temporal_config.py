"""Shared Temporal connection settings.

Single source of truth for host, namespace and task-queue names. Previously each
of worker.py / worker_poll.py hardcoded its own copy and TEMPORAL_NAMESPACE was
declared in .env.example but read by nothing.
"""

import os

from temporalio.client import Client

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")

# Workflows run here. Orchestration-plane activities (compensations) are
# registered on this queue.
ORCHESTRATOR_TASK_QUEUE = "orchestrator-task-queue"

# Agent-plane activities run here, on remote sandboxes that hold no inbound
# ports. Activities are routed to it explicitly via task_queue=.
AGENT_FLEET_TASK_QUEUE = "remote-agent-fleet-prod"


async def connect() -> Client:
    """Connect to Temporal using the configured host and namespace."""
    return await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
