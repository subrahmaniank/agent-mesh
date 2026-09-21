# Framework Integrations (CrewAI, LangGraph, AutoGen)

The platform enables running agents built in any framework (CrewAI, LangGraph, AutoGen, or custom REST microservices) while enforcing centralized policy and privacy guardrails.

---

## 1. CrewAI Integration Example

To build a standalone CrewAI project that connects to the platform:

### 1.1 Secure Tool Wrapper (`gateway_tool.py`)

```python
import os
import httpx
from crewai.tools import BaseTool
from pydantic import Field

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")

class PlatformSecureTool(BaseTool):
    name: str = "platform_secure_tool"
    description: str = "Executes operations governed by Cedar RBAC and Presidio DLP."
    
    agent_id: str = Field(default_factory=lambda: os.getenv("AGENT_ID", "crew-researcher-01"))
    tenant_id: str = Field(default_factory=lambda: os.getenv("TENANT_ID", "tenant-alpha"))

    def _run(self, tool_name: str, payload: dict) -> str:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{GATEWAY_URL}/v1/tools/call",
                json={
                    "agent_id": self.agent_id,
                    "tenant_id": self.tenant_id,
                    "tool": tool_name,
                    "arguments": payload,
                    "context": {
                        "user_id": os.getenv("USER_ID", "user-01"),
                        "user_clearance": int(os.getenv("USER_CLEARANCE", "3")),
                        "hitl_approved": False,
                        "payload": str(payload)
                    }
                }
            )
            response.raise_for_status()
            return str(response.json())
```

### 1.2 Agent Definition (`main.py`)

```python
from crewai import Agent, Task, Crew
from gateway_tool import PlatformSecureTool

secure_tool = PlatformSecureTool()

researcher = Agent(
    role="Security Analyst",
    goal="Safely query customer metadata.",
    backstory="Enterprise agent subject to Cedar policy constraints.",
    tools=[secure_tool]
)

task = Task(
    description="Query user logs with payload {'query': 'SELECT * FROM logs LIMIT 5'}",
    expected_output="Sanitized summary of query results.",
    agent=researcher
)

crew = Crew(agents=[researcher], tasks=[task])
result = crew.kickoff()
print(result)
```

---

## 2. Universal Agent Shim (`remote_runner/runner_shim.py`)

For LangGraph or AutoGen agents, use `UniversalAgentShim`:

```python
from remote_runner.runner_shim import UniversalAgentShim

shim = UniversalAgentShim(agent_id="langgraph-agent-01", tenant_id="tenant-alpha")
result = await shim.execute_step(
    tool_name="database_read",
    payload={"query": "SELECT count(*) FROM transactions"},
    user_context={"user_id": "u-100", "user_clearance": 4}
)
```
