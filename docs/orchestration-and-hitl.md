# Durable Orchestration & Asynchronous HITL

The platform uses **Temporal** to orchestrate multi-agent workflows, long-running Human-in-the-Loop (HITL) approval gates, and automatic compensation sagas.

---

## 1. Asynchronous Human-in-the-Loop (HITL)

Traditional synchronous agents block servers or lose state when waiting for operator intervention. In contrast, Temporal workflows can hibernate for hours or days with zero compute cost.

### 1.1 Approval Workflow (`orchestrator/workflows/hitl_approval.py`)

- **State Evaluation**: The workflow waits on a signal (`submit_approval`).
- **Timeout Protection**: If an operator does not respond within the SLA (e.g., 24 hours), the workflow deterministically branches to a timed-out state.
- **Dynamic Queries**: Operators or UI frontends can inspect current workflow status via `@workflow.query get_approval_state`.

```python
@workflow.defn
class HITLApprovalWorkflow:
    def __init__(self):
        self.decision: Optional[ApprovalDecision] = None

    @workflow.signal
    def submit_approval(self, decision: ApprovalDecision) -> None:
        self.decision = decision

    @workflow.run
    async def run(self, task_input: dict) -> dict:
        try:
            await workflow.wait_condition(
                lambda: self.decision is not None,
                timeout=timedelta(hours=24)
            )
        except TimeoutError:
            return {"status": "Timed_Out", "approved": False}

        return {
            "status": "Evaluated",
            "approved": self.decision.approved,
            "reviewer": self.decision.reviewer_id,
            "comments": self.decision.comments
        }
```

---

## 2. Multi-Agent DAGs & Saga Compensations

When an orchestration workflow fails mid-pipeline (e.g., database provisioning error or Cedar policy violation), previously completed stateful actions must be undone.

### 2.1 Compensation Engine (`orchestrator/workflows/multi_agent_dag.py`)

1. **Step Execution**: Each successful activity pushes a rollback handler to a compensations stack.
2. **Deterministic Rollback**: If a subsequent step raises an exception, the workflow iterates backwards through the stack, executing each rollback activity in reverse order (LIFO).

```python
compensations = []
try:
    res1 = await workflow.execute_activity(invoke_agent_step, {"action": "create_staging_workspace", ...})
    compensations.append(("delete_staging_workspace", res1["workspace_id"]))

    res2 = await workflow.execute_activity(invoke_agent_step, {"action": "provision_test_db", ...})
    compensations.append(("teardown_test_db", res2["db_id"]))

    return {"status": "Pipeline_Completed", ...}
except Exception as failure:
    for comp_action, target_id in reversed(compensations):
        await workflow.execute_activity(rollback_action, {"action": comp_action, "target_id": target_id})
    raise failure
```
