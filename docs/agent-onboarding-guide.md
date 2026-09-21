# Agent Onboarding Guide

This guide provides an end-to-end walkthrough for onboarding, securing, orchestrating, and instrumenting a new autonomous AI agent (e.g., built with LangGraph, CrewAI, AutoGen, or custom REST services) into **AgentMesh**.

---

## Onboarding Lifecycle Overview

```
 ┌────────────────┐     ┌────────────────┐     ┌────────────────┐     ┌────────────────┐
 │ 1. Register    │ ──▶ │ 2. Configure   │ ──▶ │ 3. Configure   │ ──▶ │ 4. Wire Runtime │
 │    Agent ID    │     │    Cedar IAM   │     │    OTel & DLP  │     │    & Temporal  │
 └────────────────┘     └────────────────┘     └────────────────┘     └────────────────┘
```

---

## Step 1: Declare & Register the Agent Entity

Every agent on the platform requires an **Agent Identity** (`agent_id`, `owner`, `tier`, `model`).

1. Open [`gateway/policies/schema.cedarschema`](../gateway/policies/schema.cedarschema).
2. Ensure your agent entity type and tier attributes are recognized. The standard schema supports:

```cedar
entity Agent {
  owner: String,
  tier: String,    // e.g. "tier-1-prod", "tier-2-internal", "sandbox"
  model: String    // e.g. "gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"
};
```

3. Register your agent card or metadata in your agent deployment configuration (or `.env`):
```env
AGENT_ID="data-analytics-agent"
AGENT_TIER="tier-1-prod"
AGENT_OWNER="data-engineering"
AGENT_MODEL="gpt-4o"
TENANT_ID="tenant-enterprise"
```

---

## Step 2: Configure Cedar IAM & Authorization Policies

The platform uses **AWS Cedar** for pre-flight authorization. By default, access is denied unless explicitly permitted.

Open [`gateway/policies/base_guardrails.cedar`](../gateway/policies/base_guardrails.cedar) and define your agent's access rules:

### 2.1 Allow Specific Operators to Invoke Your Agent
```cedar
// Allow members of DataScienceTeam to invoke data-analytics-agent
permit (
    principal in AgentPlatform::UserGroup::"DataScienceTeam",
    action == AgentPlatform::Action::"invoke",
    resource == AgentPlatform::Agent::"data-analytics-agent"
);
```

### 2.2 Define Tool Clearances (ABAC)
Ensure tools called by the agent enforce the caller's verified clearance level:
```cedar
// Allow data-analytics-agent to call tools when user clearance >= tool risk score
permit (
    principal == AgentPlatform::Agent::"data-analytics-agent",
    action == AgentPlatform::Action::"call_tool",
    resource
)
when {
    context.user_clearance >= resource.risk_level_score
};
```

### 2.3 Enforce Hard Guardrails & Invariants
Block high-risk actions without confirmed Human-in-the-Loop (HITL) approval:
```cedar
forbid (
    principal == AgentPlatform::Agent::"data-analytics-agent",
    action == AgentPlatform::Action::"call_tool",
    resource
)
when {
    (resource.risk_level == "destructive" ||
     context.payload.contains("DROP") ||
     context.payload.contains("TRUNCATE") ||
     context.payload.contains("DELETE")) &&
    context.hitl_approved != true
};
```

---

## Step 3: Configure In-Flight DLP (Microsoft Presidio)

Configure entity detection for sensitive data sent to or from the agent.

1. In [`gateway/config.yaml`](../gateway/config.yaml), verify active PII recognizers:
```yaml
guardrails:
  presidio:
    analyzer_endpoint: "http://presidio-analyzer:3000/analyze"
    anonymizer_endpoint: "http://presidio-anonymizer:3000/anonymize"
    entities:
      - EMAIL_ADDRESS
      - PHONE_NUMBER
      - US_SSN
      - CREDIT_CARD
      - IP_ADDRESS
    mask_mode: "replace"
    mask_token: "<{entity_type}_{index}>"
```
2. For domain-specific PII (e.g., custom Employee IDs or Patient IDs), add regex rules to [`presidio/conf/recognizers.yaml`](../presidio/conf/recognizers.yaml):
```yaml
custom_recognizers:
  - name: "INTERNAL_EMPLOYEE_ID"
    supported_language: "en"
    patterns:
      - name: "emp_id_pattern"
        regex: "EMP-[0-9]{6}"
        score: 0.85
    context:
      - "employee"
      - "badge"
```

---

## Step 4: Configure OpenTelemetry (OTel) & Zero Data Retention

All agent invocations and tool calls emit OpenTelemetry spans with W3C trace context.

1. **Endpoint Configuration:** Point the agent's OTel SDK to the platform collector:
```env
OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
OTEL_SERVICE_NAME="agent-data-analytics"
```

2. **Zero Data Retention (ZDR):**
The collector automatically deletes raw prompts and responses while preserving token metrics and execution hashes in [`otel/otel-collector-config.yaml`](../otel/otel-collector-config.yaml).

3. **Backend Switching:**
Choose or change where telemetry flows by setting the exporter in `otel/otel-collector-config.yaml`:
- **AgentOps.ai:** Set `AGENTOPS_API_KEY` in `.env`.
- **Langfuse:** Set `LANGFUSE_AUTH_TOKEN` in `.env`.

---

## Step 5: Implement the Runtime Adapter (Universal Shim)

Agents execute tools through the `UniversalAgentShim` so that Cedar policies and Presidio DLP are evaluated before any external action is executed.

### 5.1 Python Agent Implementation

```python
import os
import asyncio
from remote_runner.runner_shim import UniversalAgentShim

async def run_agent_step():
    # Initialize the shim with the onboarded agent credentials
    shim = UniversalAgentShim(
        agent_id="data-analytics-agent",
        tenant_id="tenant-enterprise"
    )

    # Execute a governed tool call
    result = await shim.execute_step(
        tool_name="postgres_query",
        payload={"query": "SELECT count(*) FROM sales WHERE year = 2026;"},
        user_context={
            "user_id": "operator-subra",
            "user_clearance": 4,          # Verified user clearance
            "hitl_approved": False         # Set to True only via verified approval
        }
    )

    print("Governed Tool Response:", result)

if __name__ == "__main__":
    asyncio.run(run_agent_step())
```

---

## Step 6: Register with Temporal Orchestration (Optional)

If your agent participates in multi-step sagas or requires asynchronous Human-in-the-Loop approval:

1. **Add Agent Activity** to [`orchestrator/activities/gateway_client.py`](../orchestrator/activities/gateway_client.py):
```python
@activity.defn
async def execute_data_analytics_step(params: dict) -> dict:
    # Routes through gateway shim
    shim = UniversalAgentShim(agent_id=params["agent_id"], tenant_id=params["tenant_id"])
    return await shim.execute_step(params["tool"], params["payload"], params["context"])
```

2. **Register Activity in Worker** in [`remote_runner/worker_poll.py`](../remote_runner/worker_poll.py):
```python
worker = Worker(
    client,
    task_queue="remote-agent-fleet-prod",
    workflows=[HITLApprovalWorkflow, MultiAgentDAGWorkflow],
    activities=[execute_data_analytics_step, invoke_agent_step, request_human_approval]
)
```

---

## Step 7: Verification & Evals Checklist

Before deploying the new agent to production, run the evaluation pipeline:

| Check | How to Test | Expected Outcome |
| :--- | :--- | :--- |
| **1. Cedar RBAC/ABAC** | Run `pytest tests/unit/test_cedar_policies.py` | Unauthorized clearances receive `DENY`. |
| **2. Invariant Block** | Send a `DROP TABLE` or `DELETE` payload | Blocked with `DENY`/`FORBID` unless `hitl_approved=True`. |
| **3. Presidio DLP** | Run `pytest tests/evals/test_pii_leakage.py` | Raw PII is masked before model processing. |
| **4. Grounding & Faithfulness** | Run `pytest tests/evals/test_hallucination_grounding.py` | Grounding overlap score $\ge 0.8$. |
| **5. End-to-End Suite** | `uv run pytest tests/ -v` | All 8 unit and eval tests PASS. |
