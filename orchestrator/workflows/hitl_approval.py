from datetime import timedelta
from typing import Optional
from dataclasses import dataclass
from temporalio import workflow

@dataclass
class ApprovalDecision:
    approved: bool
    reviewer_id: str
    comments: Optional[str] = None

@workflow.defn
class HITLApprovalWorkflow:
    def __init__(self):
        self.decision: Optional[ApprovalDecision] = None

    @workflow.signal
    def submit_approval(self, decision: ApprovalDecision) -> None:
        self.decision = decision

    @workflow.query
    def get_approval_state(self) -> str:
        if self.decision is None:
            return "Awaiting_Review"
        return "Approved" if self.decision.approved else "Rejected"

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
