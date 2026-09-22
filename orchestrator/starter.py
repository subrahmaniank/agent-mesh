"""Control-plane CLI: start workflows, send HITL approvals, query state.

This is the entrypoint the platform was missing -- nothing in the repository
previously called start_workflow() or sent the submit_approval signal, so no
workflow had ever executed.

Usage:
    python -m orchestrator.starter run-dag [--spec '{"job":"nightly-etl"}']
    python -m orchestrator.starter start-hitl --workflow-id hitl-1
    python -m orchestrator.starter state       --workflow-id hitl-1
    python -m orchestrator.starter approve     --workflow-id hitl-1 --reviewer alice
    python -m orchestrator.starter approve     --workflow-id hitl-1 --reviewer alice --reject
"""

import argparse
import asyncio
import json
import sys

from orchestrator.temporal_config import ORCHESTRATOR_TASK_QUEUE, connect
from orchestrator.workflows.hitl_approval import ApprovalDecision, HITLApprovalWorkflow
from orchestrator.workflows.multi_agent_dag import MultiAgentDAGWorkflow


async def run_dag(args) -> int:
    client = await connect()
    spec = json.loads(args.spec) if args.spec else {"job": "demo-pipeline"}
    handle = await client.start_workflow(
        MultiAgentDAGWorkflow.run,
        spec,
        id=args.workflow_id,
        task_queue=ORCHESTRATOR_TASK_QUEUE,
    )
    print(f"Started MultiAgentDAGWorkflow id={handle.id} run={handle.result_run_id}")
    try:
        result = await handle.result()
    except Exception as exc:
        # An expected outcome when exercising the saga: the pipeline failed and
        # compensations ran. The event history on :8233 shows the rollbacks.
        print(f"Workflow failed (compensations should have run): {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


async def start_hitl(args) -> int:
    client = await connect()
    task_input = json.loads(args.task) if args.task else {"action": "drop_customers_table"}
    handle = await client.start_workflow(
        HITLApprovalWorkflow.run,
        task_input,
        id=args.workflow_id,
        task_queue=ORCHESTRATOR_TASK_QUEUE,
    )
    print(f"Started HITLApprovalWorkflow id={handle.id} -- awaiting approval signal.")
    print(f"Approve with: python -m orchestrator.starter approve --workflow-id {handle.id} --reviewer <you>")
    return 0


async def approve(args) -> int:
    client = await connect()
    handle = client.get_workflow_handle(args.workflow_id)
    decision = ApprovalDecision(
        approved=not args.reject,
        reviewer_id=args.reviewer,
        comments=args.comments,
    )
    await handle.signal(HITLApprovalWorkflow.submit_approval, decision)
    print(f"Signalled {args.workflow_id}: approved={decision.approved} reviewer={decision.reviewer_id}")
    result = await handle.result()
    print(json.dumps(result, indent=2))
    return 0


async def state(args) -> int:
    client = await connect()
    handle = client.get_workflow_handle(args.workflow_id)
    print(await handle.query(HITLApprovalWorkflow.get_approval_state))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator.starter")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dag = sub.add_parser("run-dag", help="Start the multi-agent DAG workflow")
    p_dag.add_argument("--workflow-id", default="dag-demo")
    p_dag.add_argument("--spec", help="JSON pipeline spec")
    p_dag.set_defaults(func=run_dag)

    p_hitl = sub.add_parser("start-hitl", help="Start an HITL approval workflow")
    p_hitl.add_argument("--workflow-id", required=True)
    p_hitl.add_argument("--task", help="JSON task input")
    p_hitl.set_defaults(func=start_hitl)

    p_app = sub.add_parser("approve", help="Send the submit_approval signal")
    p_app.add_argument("--workflow-id", required=True)
    p_app.add_argument("--reviewer", required=True)
    p_app.add_argument("--comments")
    p_app.add_argument("--reject", action="store_true", help="Reject instead of approve")
    p_app.set_defaults(func=approve)

    p_state = sub.add_parser("state", help="Query current approval state")
    p_state.add_argument("--workflow-id", required=True)
    p_state.set_defaults(func=state)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
