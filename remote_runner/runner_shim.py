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
