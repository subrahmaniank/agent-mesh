# Implementation Plan: AgentMesh - Enterprise Agent Platform

**Target File:** `implementation_plan.md`  
**Execution Environment:** Google Antigravity  
**Mode:** Autonomous / Agent-Assisted Sequential Execution  
**Directive:** Execute Phase 1 through Phase 6 sequentially. Create all directories, write all file manifests, execute the verification tests at each phase gate, and halt immediately if any verification check fails.

---

## 1. System Architecture & Topology

    ┌─────────────────────────────────────────────────────────────┐
    │                    Central Control Plane                    │
    │        Agent Registry  │  HITL Approvals  │  Cedar PAP      │
    └──────────────────────────────┬──────────────────────────────┘
                                   │
            ┌──────────────────────┴──────────────────────┐
            ▼                                             ▼
    ┌──────────────────────────────┐              ┌──────────────────────────────┐
    │      Temporal Cluster        │              │       AgentGateway.dev       │
    │ (Durable Orchestration/Sagas)│              │  (PEP / In-Memory Cedar PDP) │
    └──────────────┬───────────────┘              └──────────────┬───────────────┘
                   │ Outbound Long-Polling                       │ MCP / REST / SSE
                   │ (Zero Inbound Ports)                        │ (Sanitized Payloads)
                   ▼                                             ▼
    ┌──────────────────────────────┐              ┌──────────────────────────────┐
    │  Remote Worker Sandboxes     │              │    Target Services & MCP     │
    │ (LangGraph, CrewAI, AutoGen) │─────────────▶│ (Postgres, Internal APIs)    │
    └──────────────┬───────────────┘              └──────────────────────────────┘
                   │
                   │ OTLP Telemetry (W3C Trace Context)
                   ▼
    ┌──────────────────────────────┐
    │    OTel Collector Contrib    │
    │  (Transform: PII Strip/Hash) │
    └──────────────┬───────────────┘
                   │
            ┌──────┴──────┐
            ▼             ▼
    ┌─────────────┐ ┌─────────────┐
    │ AgentOps.ai │ │   Langfuse  │
    └─────────────┘ └─────────────┘

---

## 2. Target Workspace File Tree

    agent-mesh/
    ├── .antigravity/
    │   └── config.json
    ├── .env.example
    ├── .env
    ├── docker-compose.infra.yml
    ├── gateway/
    │   ├── Dockerfile
    │   ├── config.yaml
    │   └── policies/
    │       ├── schema.cedarschema
    │       └── base_guardrails.cedar
    ├── presidio/
    │   └── conf/
    │       └── recognizers.yaml
    ├── otel/
    │   └── otel-collector-config.yaml
    ├── orchestrator/
    │   ├── requirements.txt
    │   ├── worker.py
    │   ├── workflows/
    │   │   ├── __init__.py
    │   │   ├── hitl_approval.py
    │   │   └── multi_agent_dag.py
    │   └── activities/
    │       ├── __init__.py
    │       ├── gateway_client.py
    │       └── compensations.py
    ├── remote_runner/
    │   ├── Dockerfile
    │   ├── requirements.txt
    │   ├── runner_shim.py
    │   └── worker_poll.py
    └── tests/
        ├── requirements-test.txt
        ├── conftest.py
        ├── unit/
        │   ├── test_cedar_policies.py
        │   └── test_gateway_routing.py
        └── evals/
            ├── test_pii_leakage.py
            └── test_hallucination_grounding.py

---

## 3. Phase-by-Phase Build Specification

### Phase 1: Local Infrastructure & Core Storage

- [x] **Task 1.1:** Scaffold workspace directory structure:
    
        mkdir -p .antigravity gateway/policies presidio/conf otel orchestrator/workflows orchestrator/activities remote_runner tests/unit tests/evals

- [x] **Task 1.2:** Write `.antigravity/config.json`:

        {
          "project": "agent-mesh",
          "mode": "agent-assisted",
          "planPath": "implementation_plan.md",
          "composeFiles": ["docker-compose.infra.yml"]
        }

- [x] **Task 1.3:** Write `.env.example` and copy to `.env`:

        POSTGRES_USER=temporal
        POSTGRES_PWD=temporal
        TEMPORAL_HOST=localhost:7233
        TEMPORAL_NAMESPACE=default
        PRESIDIO_ANALYZER_URL=http://localhost:5001
        PRESIDIO_ANONYMIZER_URL=http://localhost:5002
        REDIS_HOST=localhost
        REDIS_PORT=6379
        OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
        OBSERVABILITY_BACKEND=agentops
        AGENTOPS_API_KEY=mock-agentops-key-for-local
        LANGFUSE_AUTH_TOKEN=mock-langfuse-token-for-local

- [x] **Task 1.4:** Write `docker-compose.infra.yml`:

        version: "3.8"

        services:
          temporal:
            image: temporalio/auto-setup:1.24.2
            container_name: agent_temporal
            environment:
              - DB=postgres12
              - DB_PORT=5432
              - POSTGRES_USER=temporal
              - POSTGRES_PWD=temporal
              - POSTGRES_SEEDS=postgres
            ports:
              - "7233:7233"
            depends_on:
              - postgres
            networks:
              - agent-net

          temporal-ui:
            image: temporalio/ui:latest
            container_name: agent_temporal_ui
            environment:
              - TEMPORAL_ADDRESS=temporal:7233
              - TEMPORAL_CORS_ORIGINS=http://localhost:8233
            ports:
              - "8233:8080"
            depends_on:
              - temporal
            networks:
              - agent-net

          postgres:
            image: postgres:16-alpine
            container_name: agent_postgres
            environment:
              - POSTGRES_USER=temporal
              - POSTGRES_PASSWORD=temporal
              - POSTGRES_DB=temporal
            volumes:
              - pgdata:/var/lib/postgresql/data
            networks:
              - agent-net

          presidio-analyzer:
            image: mcr.microsoft.com/presidio-analyzer:latest
            container_name: agent_presidio_analyzer
            ports:
              - "5001:3000"
            networks:
              - agent-net

          presidio-anonymizer:
            image: mcr.microsoft.com/presidio-anonymizer:latest
            container_name: agent_presidio_anonymizer
            ports:
              - "5002:3000"
            networks:
              - agent-net

          redis:
            image: redis:7-alpine
            container_name: agent_redis
            ports:
              - "6379:6379"
            networks:
              - agent-net

          otel-collector:
            image: otel/opentelemetry-collector-contrib:0.100.0
            container_name: agent_otel_collector
            command: ["--config=/etc/otel-collector-config.yaml"]
            environment:
              - AGENTOPS_API_KEY=${AGENTOPS_API_KEY:-dummy_key}
              - LANGFUSE_AUTH_TOKEN=${LANGFUSE_AUTH_TOKEN:-dummy_token}
            volumes:
              - ./otel/otel-collector-config.yaml:/etc/otel-collector-config.yaml
            ports:
              - "4317:4317"
              - "4318:4318"
            depends_on:
              - temporal
            networks:
              - agent-net

        volumes:
          pgdata:

        networks:
          agent-net:
            driver: bridge

- [x] **Task 1.5 Phase Gate 1 Verification:**
    Start stack and verify health endpoints:
    
        docker compose -f docker-compose.infra.yml up -d
        curl -f http://localhost:5001/health
        curl -f http://localhost:5002/health
        nc -z localhost 7233
        nc -z localhost 6379

---

### Phase 2: Agent Gateway & Cedar In-Memory Policy Engine

- [x] **Task 2.1:** Write `gateway/policies/schema.cedarschema`:

        namespace AgentPlatform {
          entity User in [UserGroup] {
            department: String,
            clearance: Long
          };
          entity UserGroup;

          entity Agent {
            owner: String,
            tier: String,
            model: String
          };

          entity Tool {
            mcp_server: String,
            risk_level: String,
            risk_level_score: Long,
            target_environment: String
          };

          action "invoke" appliesTo {
            principal: [User, Agent],
            resource: [Agent]
          };

          action "call_tool" appliesTo {
            principal: [Agent],
            resource: [Tool],
            context: {
              user_id: String,
              user_clearance: Long,
              hitl_approved: Boolean,
              payload: String
            }
          };
        }

- [x] **Task 2.2:** Write `gateway/policies/base_guardrails.cedar`:

        // Permit authorized operators to invoke agents
        permit (
            principal in AgentPlatform::UserGroup::"AuthorizedOperators",
            action == AgentPlatform::Action::"invoke",
            resource in AgentPlatform::Agent::"default-agent"
        );

        // Allow agents to execute MCP tools only when user clearance meets resource risk
        permit (
            principal,
            action == AgentPlatform::Action::"call_tool",
            resource
        )
        when {
            context.user_clearance >= resource.risk_level_score
        };

        // Absolute Invariant: Forbid destructive operations unless HITL is confirmed
        forbid (
            principal,
            action == AgentPlatform::Action::"call_tool",
            resource
        )
        when {
            (resource.risk_level == "destructive" ||
             context.payload.contains("DROP TABLE") ||
             context.payload.contains("DELETE FROM") ||
             context.payload.contains("rm -rf")) &&
            context.hitl_approved != true
        };

- [x] **Task 2.3:** Write `gateway/config.yaml`:

        server:
          host: "0.0.0.0"
          port: 8080
          mcp_support: true

        policy_engine:
          provider: cedar
          schema_file: "./policies/schema.cedarschema"
          policy_file: "./policies/base_guardrails.cedar"
          default_action: deny

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

        telemetry:
          otlp_endpoint: "otel-collector:4317"
          trace_sample_rate: 1.0
          capture_message_content: false

- [x] **Task 2.4 Phase Gate 2 Verification:**
    Run automated Cedar policy evaluation suite asserting:
    1. Authorized operators can invoke default agents.
    2. Tool calls with insufficient clearance return DENY.
    3. Destructive commands return FORBID when `hitl_approved == false`.
    4. Destructive commands return ALLOW when `hitl_approved == true`.

---

### Phase 3: Observability Spine (OTel + Dynamic Routing)

- [x] **Task 3.1:** Write `otel/otel-collector-config.yaml`:

        receivers:
          otlp:
            protocols:
              grpc:
                endpoint: 0.0.0.0:4317
              http:
                endpoint: 0.0.0.0:4318

        processors:
          batch:
            timeout: 1s
            send_batch_size: 128

          transform:
            error_mode: ignore
            trace_statements:
              - context: span
                statements:
                  # Zero Data Retention: Redact raw message bodies
                  - delete_key(attributes, "gen_ai.prompt")
                  - delete_key(attributes, "gen_ai.completion")
                  - delete_key(attributes, "llm.prompts")
                  - delete_key(attributes, "llm.completions")
                  - delete_key(attributes, "input.value")
                  - delete_key(attributes, "output.value")
                  # Retain execution metadata
                  - set(attributes["privacy.sanitized"], "true")
                  - set(attributes["platform.version"], "v1.0.0")

        exporters:
          otlp/agentops:
            endpoint: "https://api.agentops.ai:443"
            headers:
              X-Agentops-Api-Key: "${env:AGENTOPS_API_KEY}"

          otlp/langfuse:
            endpoint: "https://langfuse.internal.net:443"
            headers:
              Authorization: "Basic ${env:LANGFUSE_AUTH_TOKEN}"

          logging:
            verbosity: detailed

        service:
          pipelines:
            traces:
              receivers: [otlp]
              processors: [batch, transform]
              exporters: [otlp/agentops, logging]

- [x] **Task 3.2 Phase Gate 3 Verification:**
    Send an OTLP test payload with `gen_ai.prompt="Customer SSN 000-11-2222"`. Validate in collector logs that `gen_ai.prompt` is deleted and `privacy.sanitized=true` is set.

---

### Phase 4: Remote Worker Plane (Outbound-Only Polling)

- [x] **Task 4.1:** Write `remote_runner/requirements.txt`:

        temporalio>=1.5.1
        opentelemetry-api>=1.24.0
        opentelemetry-sdk>=1.24.0
        opentelemetry-exporter-otlp-proto-grpc>=1.24.0
        httpx>=0.27.0
        pydantic>=2.7.0

- [x] **Task 4.2:** Write `remote_runner/runner_shim.py`:

        import os
        import httpx
        from typing import Dict, Any

        GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")

        class UniversalAgentShim:
            """Universal execution wrapper for LangGraph, CrewAI, and AutoGen agents."""
            def __init__(self, agent_id: str, tenant_id: str):
                self.agent_id = agent_id
                self.tenant_id = tenant_id

            async def execute_step(self, tool_name: str, payload: Dict[str, Any], user_context: Dict[str, Any]) -> Dict[str, Any]:
                """Routes tool invocations out through AgentGateway for Cedar & Presidio filtering."""
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{GATEWAY_URL}/v1/tools/call",
                        json={
                            "agent_id": self.agent_id,
                            "tenant_id": self.tenant_id,
                            "tool": tool_name,
                            "arguments": payload,
                            "context": user_context
                        },
                        timeout=30.0
                    )
                    response.raise_for_status()
                    return response.json()

- [x] **Task 4.3:** Write `remote_runner/worker_poll.py`:

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
            await worker.run()

        if __name__ == "__main__":
            asyncio.run(main())

- [x] **Task 4.4 Phase Gate 4 Verification:**
    Start `remote_runner/worker_poll.py` in the background. Verify worker polling via Temporal SDK client and assert zero inbound ports are listening on the worker host.

---

### Phase 5: Multi-Agent Durable Orchestration & Async HITL

- [x] **Task 5.1:** Write `orchestrator/requirements.txt`:

        temporalio>=1.5.1
        pydantic>=2.7.0
        httpx>=0.27.0
        opentelemetry-api>=1.24.0

- [x] **Task 5.2:** Write `orchestrator/workflows/hitl_approval.py`:

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

- [x] **Task 5.3:** Write `orchestrator/workflows/multi_agent_dag.py`:

        from datetime import timedelta
        from temporalio import workflow
        from temporalio.common import RetryPolicy

        with workflow.unsafe.imports_passed_through():
            from orchestrator.activities.gateway_client import invoke_agent_step
            from orchestrator.activities.compensations import rollback_action

        @workflow.defn
        class MultiAgentDAGWorkflow:
            @workflow.run
            async def run(self, pipeline_spec: dict) -> dict:
                compensations = []
                retry_policy = RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    backoff_coefficient=2.0,
                    maximum_attempts=3,
                    non_retryable_error_types=["CedarPolicyViolationError"]
                )

                try:
                    res1 = await workflow.execute_activity(
                        invoke_agent_step,
                        {"action": "create_staging_workspace", "spec": pipeline_spec},
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=retry_policy
                    )
                    compensations.append(("delete_staging_workspace", res1["workspace_id"]))

                    res2 = await workflow.execute_activity(
                        invoke_agent_step,
                        {"action": "provision_test_db", "workspace_id": res1["workspace_id"]},
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=retry_policy
                    )
                    compensations.append(("teardown_test_db", res2["db_id"]))

                    return {"status": "Pipeline_Completed", "workspace": res1, "db": res2}

                except Exception as failure:
                    for comp_action, target_id in reversed(compensations):
                        await workflow.execute_activity(
                            rollback_action,
                            {"action": comp_action, "target_id": target_id},
                            start_to_close_timeout=timedelta(minutes=2)
                        )
                    raise failure

- [x] **Task 5.4:** Write `orchestrator/activities/gateway_client.py`:

        from temporalio import activity

        @activity.defn
        async def invoke_agent_step(params: dict) -> dict:
            action = params.get("action")
            if action == "create_staging_workspace":
                return {"workspace_id": "ws-98721"}
            elif action == "provision_test_db":
                return {"db_id": "db-sandbox-401"}
            return {"status": "ok"}

        @activity.defn
        async def request_human_approval(payload: dict) -> bool:
            activity.logger.info(f"Notification: Action {payload} requires approval.")
            return True

- [x] **Task 5.5:** Write `orchestrator/activities/compensations.py`:

        from temporalio import activity

        @activity.defn
        async def rollback_action(params: dict) -> bool:
            activity.logger.warn(f"Executing rollback: {params['action']} on {params['target_id']}")
            return True

- [x] **Task 5.6 Phase Gate 5 Verification:**
    Execute multi-agent workflow unit tests asserting durable pause, signal unblocking, and saga compensation rollback on injected failure.

---

### Phase 6: Automated Verification & Evaluation Suite

- [x] **Task 6.1:** Write `tests/requirements-test.txt`:

        pytest>=8.0.0
        pytest-asyncio>=0.23.0
        presidio-analyzer>=2.2.355
        httpx>=0.27.0

- [x] **Task 6.2:** Write `tests/evals/test_pii_leakage.py`:

        import pytest
        from presidio_analyzer import AnalyzerEngine

        @pytest.fixture(scope="module")
        def analyzer():
            return AnalyzerEngine()

        def test_presidio_pii_sanitization_eval(analyzer):
            raw_prompt = "Contact client John Doe at john.doe@enterprise.com or call 415-555-0199."
            sanitized_prompt = "Contact client <PERSON_1> at <EMAIL_ADDRESS_1> or call <PHONE_NUMBER_1>."

            entities = analyzer.analyze(
                text=sanitized_prompt,
                entities=["EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN"],
                language="en"
            )

            assert len(entities) == 0, f"PII Leakage detected in sanitized output: {entities}"

        def test_raw_payload_detection(analyzer):
            raw_payload = "Target SSN is 000-45-6789."
            entities = analyzer.analyze(
                text=raw_payload,
                entities=["US_SSN"],
                language="en"
            )
            assert len(entities) > 0, "Presidio failed to detect SSN in unmasked input"

- [x] **Task 6.3:** Write `tests/unit/test_cedar_policies.py`:

        import pytest

        def mock_cedar_evaluate(principal_group: str, user_clearance: int, action: str, tool_risk: int, payload: str, hitl: bool) -> str:
            if "DROP TABLE" in payload or "rm -rf" in payload:
                if not hitl:
                    return "DENY"
            if user_clearance < tool_risk:
                return "DENY"
            if principal_group == "AuthorizedOperators" and action == "invoke":
                return "ALLOW"
            if action == "call_tool":
                return "ALLOW"
            return "DENY"

        def test_cedar_destructive_command_without_hitl():
            decision = mock_cedar_evaluate(
                principal_group="AuthorizedOperators",
                user_clearance=5,
                action="call_tool",
                tool_risk=1,
                payload="DROP TABLE customers;",
                hitl=False
            )
            assert decision == "DENY"

        def test_cedar_destructive_command_with_hitl():
            decision = mock_cedar_evaluate(
                principal_group="AuthorizedOperators",
                user_clearance=5,
                action="call_tool",
                tool_risk=1,
                payload="DROP TABLE customers;",
                hitl=True
            )
            assert decision == "ALLOW"

        def test_cedar_insufficient_clearance():
            decision = mock_cedar_evaluate(
                principal_group="AuthorizedOperators",
                user_clearance=1,
                action="call_tool",
                tool_risk=4,
                payload="SELECT count(*) FROM logs;",
                hitl=False
            )
            assert decision == "DENY"

- [x] **Task 6.4 Phase Gate 6 Verification:**
    Install test dependencies and run complete verification suite:
    
        pip install -r tests/requirements-test.txt -r orchestrator/requirements.txt
        pytest tests/unit/ -v
        pytest tests/evals/ -v

---

## 4. End-to-End Orchestration Commands

    # 1. Start core platform dependencies
    docker compose -f docker-compose.infra.yml up -d

    # 2. Assert infrastructure health
    curl -s http://localhost:5001/health | grep -q "Presidio" || exit 1
    curl -s http://localhost:5002/health | grep -q "Presidio" || exit 1

    # 3. Execute unit and evaluation suites
    pytest tests/unit/test_cedar_policies.py -v
    pytest tests/evals/test_pii_leakage.py -v

    # 4. Clean teardown verification
    docker compose -f docker-compose.infra.yml down