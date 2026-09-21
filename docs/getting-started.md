# Getting Started & Verification

Follow this guide to set up, run, and verify the platform locally.

---

## 1. Prerequisites

- **Python 3.11+ / 3.12+**
- **uv** package manager (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Docker & Docker Compose**

---

## 2. Environment Setup

1. Clone or navigate to the repository:
   ```bash
   cd agent-mesh
   ```
2. Create and populate `.env`:
   ```bash
   cp .env.example .env
   ```
3. Initialize the virtual environment and install dependencies:
   ```bash
   uv venv .venv
   uv pip install -r tests/requirements-test.txt -r orchestrator/requirements.txt
   ```

---

## 3. Running Services

### Step 1: Start Infrastructure (Docker)
```bash
docker compose -f docker-compose.infra.yml up -d
```

| Service | Port | Endpoint |
| --- | --- | --- |
| Temporal UI | 8233 | http://localhost:8233 |
| Temporal gRPC | 7233 | localhost:7233 |
| Agent Gateway | 8080 | http://localhost:8080/healthz |
| Presidio Analyzer | 5001 | http://localhost:5001/health |
| Presidio Anonymizer | 5002 | http://localhost:5002/health |
| Redis | 6379 | localhost:6379 |
| OTel Collector | 4317 / 4318 | localhost:4317 |

### Step 2: Start the Orchestrator Worker
```bash
uv run python -m orchestrator.worker
```

### Step 3: Start the Remote Runner Node
```bash
uv run python -m remote_runner.worker_poll
```

### Step 4: Launch the Unified Web Control Portal (Optional)
```bash
uv run python -m portal.server
```
Navigate to **http://localhost:8000** for the integrated dashboard hub, agent onboarding wizard, and Cedar sandbox.

---

## 4. Running the Verification Suite

Run all Cedar policy unit tests, gateway routing tests, Presidio PII DLP evals, and hallucination grounding tests:

```bash
# Run all tests
uv run pytest tests/ -v

# Run unit tests only
uv run pytest tests/unit/ -v

# Run evaluation tests only
uv run pytest tests/evals/ -v
```

---

## 5. Teardown
```bash
docker compose -f docker-compose.infra.yml down
```
