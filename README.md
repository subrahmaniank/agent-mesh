# AgentMesh: Enterprise Agent Platform

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue.svg)](https://python.org)
[![Temporal](https://img.shields.io/badge/Orchestrator-Temporal-black.svg)](https://temporal.io)
[![Cedar](https://img.shields.io/badge/Policy%20Engine-Cedar-orange.svg)](https://www.cedarpolicy.com)
[![Presidio](https://img.shields.io/badge/DLP%20%26%20PII-Microsoft%20Presidio-blue.svg)](https://microsoft.github.io/presidio/)
[![OpenTelemetry](https://img.shields.io/badge/Observability-OpenTelemetry-purple.svg)](https://opentelemetry.io)
[![Tests](https://img.shields.io/badge/Tests-8%20Passed-brightgreen.svg)]()

**AgentMesh** is an enterprise-grade, secure, durable, and modular platform for deploying, governing, and orchestrating autonomous AI agents across heterogeneous frameworks (CrewAI, LangGraph, AutoGen, and custom REST microservices).

---

## Architecture at a Glance

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

## Key Platform Capabilities

- 🛡️ **Sub-Millisecond Policy Guardrails:** Embedded **AWS Cedar** policy engine enforcing fine-grained RBAC/ABAC and OWASP LLM06 destructive invariants before every tool invocation.
- 🔒 **In-Flight DLP & Privacy:** Real-time **Microsoft Presidio** entity detection and anonymization (SSNs, emails, phones, credit cards) preventing sensitive data leaks.
- 🌐 **Network Inversion & Zero Inbound Ports:** Remote worker execution powered by an outbound long-polling pull architecture over Temporal.
- ⏳ **Durable Multi-Agent Orchestration:** **Temporal** state machines with asynchronous Human-in-the-Loop (HITL) approval gates and LIFO Saga compensation rollbacks.
- 📊 **Decoupled Telemetry Spine:** OpenTelemetry Collector pipeline supporting Zero Data Retention (ZDR) and hot-swappable backends (**Langfuse**, **AgentOps.ai**).
- 🧩 **Heterogeneous Agent Support:** Plug-and-play integration for **CrewAI**, **LangGraph**, **AutoGen**, and Anthropic **Model Context Protocol (MCP)** tools.

---

## Repository Layout

```
agent-mesh/
├── .antigravity/                   # Antigravity IDE & workflow configuration
│   └── config.json
├── docs/                           # Modular documentation & integration guides
│   ├── architecture.md             # Topology, components & network flows
│   ├── security-and-governance.md  # Cedar policies, Presidio DLP, ZDR
│   ├── orchestration-and-hitl.md   # Temporal workflows, HITL, Sagas
│   ├── observability.md            # OTel Collector, tracing, Langfuse/AgentOps
│   ├── framework-integrations.md   # CrewAI, LangGraph, AutoGen onboarding
│   └── getting-started.md          # Setup, commands, testing & verification
├── gateway/                        # Policy Enforcement Point (PEP) & Cedar PDP
│   ├── Dockerfile
│   ├── config.yaml                 # Gateway server, Presidio & OTel config
│   ├── server.py                   # In-memory Cedar PDP & Presidio DLP server
│   └── policies/
│       ├── schema.cedarschema      # Cedar entity & action definitions
│       └── base_guardrails.cedar   # Clearance & destructive invariants
├── presidio/                       # Presidio custom recognizers
│   └── conf/
│       └── recognizers.yaml
├── otel/                           # OpenTelemetry Collector configuration
│   └── otel-collector-config.yaml  # PII stripping transform & exporters
├── orchestrator/                   # Temporal orchestration workers & activities
│   ├── requirements.txt
│   ├── worker.py                   # Orchestrator worker entrypoint
│   ├── workflows/
│   │   ├── __init__.py
│   │   ├── hitl_approval.py        # Asynchronous HITL approval workflow
│   │   └── multi_agent_dag.py      # Multi-agent DAG with Saga rollbacks
│   └── activities/
│       ├── __init__.py
│       ├── gateway_client.py       # Activity invocations & human approvals
│       └── compensations.py        # Automated saga rollback activities
├── remote_runner/                  # Remote worker runtime plane
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── runner_shim.py              # UniversalAgentShim adapter
│   └── worker_poll.py              # Outbound Temporal long-poller
├── portal/                         # Unified Web Control Portal & Dashboards
│   ├── server.py                   # Portal HTTP host
│   └── index.html                  # Interactive UI, Cedar sandbox & onboarding wizard
├── tests/                          # Automated verification & evaluation suite
│   ├── requirements-test.txt
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_cedar_policies.py  # Cedar RBAC/ABAC unit tests
│   │   └── test_gateway_routing.py # Universal shim routing unit tests
│   └── evals/
│       ├── test_pii_leakage.py     # Presidio PII sanitization eval
│       └── test_hallucination_grounding.py # Hallucination & grounding eval
├── docker-compose.infra.yml        # Storage, Temporal, Presidio, OTel, Redis, Gateway
├── implementation_plan.md          # 6-Phase build & verification specification
├── .env.example                    # Environment template
└── README.md                       # Main documentation portal
```

---

## Detailed Documentation

| Guide | Description |
| :--- | :--- |
| 🚀 [**Agent Onboarding Guide**](docs/agent-onboarding-guide.md) | End-to-end guide to register, configure IAM/Cedar, setup OTel/DLP, and run a new agent. |
| 📐 [**System Architecture**](docs/architecture.md) | In-depth topology, network inversion, and component breakdown. |
| 🛡️ [**Security & Governance**](docs/security-and-governance.md) | Cedar policy schema, guardrail rules, Presidio DLP, and Zero Data Retention. |
| ⏳ [**Orchestration & HITL**](docs/orchestration-and-hitl.md) | Temporal workflows, async approval gates, and Saga compensation rollbacks. |
| 📊 [**Observability & Analytics**](docs/observability.md) | OpenTelemetry Collector configuration, backend swapping, and trace propagation. |
| 🤖 [**Framework Integrations**](docs/framework-integrations.md) | Step-by-step guide to onboard CrewAI, LangGraph, and AutoGen agents. |
| 🛠️ [**Getting Started & Verification**](docs/getting-started.md) | Prerequisites, environment setup, running workers, and test verification. |

---

## Quickstart

### 1. Environment & Dependencies
```bash
# Copy environment configuration
cp .env.example .env

# Create and activate virtual environment
uv venv .venv

# On Windows (PowerShell):
.\.venv\Scripts\activate.ps1

# On Linux/macOS:
source .venv/bin/activate

# Install all runtime, worker, and test dependencies
uv pip install -r tests/requirements-test.txt -r orchestrator/requirements.txt
```

### 2. Start Core Infrastructure & Gateway (Docker)
```bash
docker compose -f docker-compose.infra.yml up -d
```

#### Service Endpoints & Health Checks

| Service | Port | Endpoint / Health Check | Purpose |
| :--- | :--- | :--- | :--- |
| **Temporal Web UI** | `8233` | [http://localhost:8233](http://localhost:8233) | Multi-agent DAG workflow & Saga visualizer |
| **Temporal gRPC** | `7233` | `localhost:7233` | Orchestration server gRPC endpoint |
| **Agent Gateway (PEP)** | `8080` | [http://localhost:8080/healthz](http://localhost:8080/healthz) | In-memory Cedar PDP & Presidio DLP enforcement |
| **Presidio Analyzer** | `5001` | [http://localhost:5001/health](http://localhost:5001/health) | Sensitive entity & PII analyzer microservice |
| **Presidio Anonymizer** | `5002` | [http://localhost:5002/health](http://localhost:5002/health) | Structured token & PII masking microservice |
| **Redis** | `6379` | `localhost:6379` | State cache & temporary key-value store |
| **OTel Collector** | `4317` / `4318` | `localhost:4317` | Zero Data Retention telemetry transform spine |

### 3. Start Workers & Control Portal

Run the following processes across separate terminals:

```bash
# Terminal 1: Start Orchestrator Worker
uv run python -m orchestrator.worker

# Terminal 2: Start Remote Worker Node (Outbound Long-Polling)
uv run python -m remote_runner.worker_poll

# Terminal 3: Launch Unified Control Portal
uv run python -m portal.server
```

Open [**http://localhost:8000**](http://localhost:8000) in your browser to access the integrated dashboard, agent onboarding wizard, and interactive Cedar sandbox.

### 4. Run Automated Test & Evaluation Suite

```bash
# Run all tests (unit tests + Presidio DLP & grounding evals)
uv run pytest tests/ -v

# Run Cedar policy & gateway routing unit tests only
uv run pytest tests/unit/ -v

# Run Presidio PII & hallucination evaluation tests only
uv run pytest tests/evals/ -v
```

### 5. Infrastructure Teardown
```bash
docker compose -f docker-compose.infra.yml down
```

---

## Verification Suite

The repository includes a comprehensive unit and evaluation test suite:

- `test_cedar_policies.py`: Asserts clearance enforcement, operator role permissions, and blocks destructive commands without confirmed HITL.
- `test_gateway_routing.py`: Validates universal agent shim request framing and gateway routing.
- `test_pii_leakage.py`: Microsoft Presidio post-run assertions detecting unmasked PII and verifying sanitized outputs.
- `test_hallucination_grounding.py`: Context overlap scoring and hallucination evaluations.

To run the complete verification suite:
```bash
uv run pytest tests/ -v
```
