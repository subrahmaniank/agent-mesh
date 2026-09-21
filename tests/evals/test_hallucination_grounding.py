import pytest

def evaluate_grounding_score(response: str, ground_truth_context: list[str]) -> float:
    """Mock grounding evaluator checking overlap with verified context chunks."""
    matched_chunks = [chunk for chunk in ground_truth_context if chunk.lower() in response.lower()]
    return len(matched_chunks) / max(len(ground_truth_context), 1)

def test_hallucination_grounding_pass():
    context = ["Database migration completed on 2026-09-01", "Schema version 2.4 active"]
    response = "According to records, Database migration completed on 2026-09-01 and Schema version 2.4 active."
    score = evaluate_grounding_score(response, context)
    assert score >= 0.8

def test_hallucination_grounding_fail():
    context = ["Database migration completed on 2026-09-01", "Schema version 2.4 active"]
    response = "The system is running MySQL on AWS Aurora with schema version 9.9."
    score = evaluate_grounding_score(response, context)
    assert score < 0.5
