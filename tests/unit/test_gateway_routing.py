import pytest
import httpx
from unittest.mock import AsyncMock, patch
from remote_runner.runner_shim import UniversalAgentShim

@pytest.mark.asyncio
async def test_universal_agent_shim_routing():
    shim = UniversalAgentShim(agent_id="test-agent-1", tenant_id="tenant-alpha")
    mock_response_data = {
        "status": "success",
        "result": "query executed"
    }

    mock_resp = httpx.Response(
        status_code=200,
        json=mock_response_data,
        request=httpx.Request("POST", "http://localhost:8080/v1/tools/call")
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        res = await shim.execute_step(
            tool_name="sql_query",
            payload={"query": "SELECT * FROM users"},
            user_context={"user_id": "u-123", "user_clearance": 3}
        )

        assert res["status"] == "success"
        assert res["result"] == "query executed"
        mock_post.assert_called_once()
