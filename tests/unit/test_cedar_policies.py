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
