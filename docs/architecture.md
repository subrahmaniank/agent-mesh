# System Architecture & Topology

The **AgentMesh Platform** is designed for enterprise-grade AI agent deployment, governance, durable orchestration, and privacy enforcement.

---

## 1. High-Level Topology

```
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
```

---

## 2. Core Architectural Pillars

### 2.1 Outbound-Only Remote Execution (Network Inversion)
- Remote worker sandboxes connect outbound to Temporal via gRPC (`localhost:7233` or TLS 443).
- **Zero open inbound firewall ports** or public IPs are required on worker nodes.
- Tasks are pulled via long-polling on dedicated task queues (e.g., `remote-agent-fleet-prod`).

### 2.2 Agent Gateway (Policy Enforcement Point - PEP)
- Intercepts all tool invocations and agent communication.
- Houses the in-memory **Cedar Policy Decision Point (PDP)** for sub-millisecond RBAC/ABAC authorization.
- Routes payloads through **Microsoft Presidio** for inline PII tokenization and masking.
- Supports Anthropic **Model Context Protocol (MCP)** and standard REST/JSON-RPC tool definitions.

### 2.3 Durable Orchestrator (Temporal)
- Executes stateful multi-agent DAGs without maintaining in-memory server state.
- Supports asynchronous Human-in-the-Loop (HITL) approval gates that can pause execution for hours or days without consuming active compute resources.
- Implements the **Saga Pattern** with automated, ordered compensation rollbacks upon step failure.

### 2.4 Decoupled Telemetry Spine (OpenTelemetry)
- Standardized OTLP pipeline with an in-flight transform processor.
- Enforces **Zero Data Retention (ZDR)** by redacting raw prompts and completions before forwarding to observability sinks (AgentOps, Langfuse).
- Maintains cryptographic SHA-256 hashes and metadata for compliance audits.
