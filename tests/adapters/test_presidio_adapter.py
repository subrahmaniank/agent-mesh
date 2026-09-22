"""presidio-adapter tests.

Presidio is mocked, so these run with no Docker and no network. They assert the
agentgateway webhook contract and, most importantly, that the adapter fails
CLOSED — an unavailable scanner must never become an open door.
"""

import json
import os
import re

import httpx
import pytest

from conftest import MockBackend, load_module, serve

SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def presidio_routes():
    """A stand-in Presidio: regex for SSN/email, plus a fake NER name hit."""
    def analyze(body):
        text = body.get("text", "")
        findings = []
        for match in SSN.finditer(text):
            findings.append({"entity_type": "US_SSN", "start": match.start(),
                             "end": match.end(), "score": 0.95})
        for match in EMAIL.finditer(text):
            findings.append({"entity_type": "EMAIL_ADDRESS", "start": match.start(),
                             "end": match.end(), "score": 0.9})
        # NER-only entity that no regex layer would catch.
        idx = text.find("Dr. Alice Fairweather")
        if idx >= 0:
            findings.append({"entity_type": "PERSON", "start": idx,
                             "end": idx + len("Dr. Alice Fairweather"), "score": 0.85})
        return 200, findings

    def anonymize(body):
        text = body.get("text", "")
        text = SSN.sub("<US_SSN>", text)
        text = EMAIL.sub("<EMAIL_ADDRESS>", text)
        text = text.replace("Dr. Alice Fairweather", "<PERSON>")
        return 200, {"text": text}

    return {"/analyze": analyze, "/anonymize": anonymize}


def boot(backend_url, action="reject"):
    os.environ.update({
        "PRESIDIO_ANALYZER_URL": backend_url,
        "PRESIDIO_ANONYMIZER_URL": backend_url,
        "GUARDRAIL_ACTION": action,
        "PRESIDIO_SCORE_THRESHOLD": "0.5",
    })
    return load_module("presidio-adapter/main.py", f"presidio_adapter_{action}")


def req_envelope(text):
    return {"body": {"messages": [{"role": "user", "content": text}]}}


def resp_envelope(text):
    return {"body": {"choices": [{"message": {"role": "assistant", "content": text}}]}}


# --- endpoint construction ----------------------------------------------------

def test_hostname_containing_analyze_does_not_swallow_the_path():
    """`"analyze" in url` would match the HOSTNAME presidio-analyzer, leaving
    the path off and posting to the container root."""
    module = load_module("presidio-adapter/main.py", "presidio_endpoint_check")
    assert module.endpoint("http://presidio-analyzer:3000", "analyze") == \
        "http://presidio-analyzer:3000/analyze"
    assert module.endpoint("http://presidio-anonymizer:3000", "anonymize") == \
        "http://presidio-anonymizer:3000/anonymize"
    assert module.endpoint("http://x:3000/analyze", "analyze") == "http://x:3000/analyze"


# --- the contract -------------------------------------------------------------

def test_clean_prompt_passes():
    with MockBackend(presidio_routes()) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request", json=req_envelope("summarise Q3 revenue"), timeout=10)
        finally:
            stop()
    assert r.status_code == 200
    action = r.json()["action"]
    assert "status_code" not in action, "clean content must not be rejected"


def test_pii_prompt_is_rejected_with_403_inside_the_action():
    """agentgateway always gets HTTP 200; `status_code` in the action rejects."""
    with MockBackend(presidio_routes()) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request",
                           json=req_envelope("my ssn is 123-45-6789"), timeout=10)
        finally:
            stop()
    assert r.status_code == 200
    action = r.json()["action"]
    assert action["status_code"] == 403
    assert "US_SSN" in action["reason"]


def test_ner_only_entity_is_caught_where_regex_would_miss():
    """The whole reason Presidio sits behind the regex layer."""
    with MockBackend(presidio_routes()) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request",
                           json=req_envelope("escalate to Dr. Alice Fairweather"), timeout=10)
        finally:
            stop()
    assert r.json()["action"]["status_code"] == 403
    assert "PERSON" in r.json()["action"]["reason"]


def test_response_envelope_is_inspected_too():
    with MockBackend(presidio_routes()) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/response",
                           json=resp_envelope("the ssn is 123-45-6789"), timeout=10)
        finally:
            stop()
    assert r.json()["action"]["status_code"] == 403


def test_multimodal_text_parts_are_inspected():
    envelope = {"body": {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "reference 123-45-6789"},
        {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
    ]}]}}
    with MockBackend(presidio_routes()) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request", json=envelope, timeout=10)
        finally:
            stop()
    assert r.json()["action"]["status_code"] == 403


# --- masking mode -------------------------------------------------------------

def test_mask_mode_returns_a_redacted_body():
    with MockBackend(presidio_routes()) as backend:
        module = boot(backend.url, action="mask")
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request",
                           json=req_envelope("ssn 123-45-6789 mail a@b.com"), timeout=10)
        finally:
            stop()
    action = r.json()["action"]
    assert "status_code" not in action
    rewritten = json.dumps(action["body"])
    assert "123-45-6789" not in rewritten
    assert "a@b.com" not in rewritten
    assert "<US_SSN>" in rewritten


# --- fail closed --------------------------------------------------------------

def test_presidio_unreachable_rejects_rather_than_passing():
    """Inference leaves the building. A dead scanner must block, not wave through."""
    os.environ.update({
        "PRESIDIO_ANALYZER_URL": "http://127.0.0.1:1",
        "PRESIDIO_ANONYMIZER_URL": "http://127.0.0.1:1",
        "GUARDRAIL_ACTION": "reject",
    })
    module = load_module("presidio-adapter/main.py", "presidio_adapter_down")
    base, stop = serve(module)
    try:
        r = httpx.post(f"{base}/request", json=req_envelope("ssn 123-45-6789"), timeout=15)
    finally:
        stop()
    assert r.status_code == 200
    assert r.json()["action"]["status_code"] == 403
    assert "unavailable" in r.json()["action"]["reason"].lower()


def test_malformed_envelope_is_rejected_not_ignored():
    with MockBackend(presidio_routes()) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request", content=b"{not json",
                           headers={"Content-Type": "application/json"}, timeout=10)
        finally:
            stop()
    assert r.json()["action"]["status_code"] == 403


def test_presidio_error_response_rejects():
    routes = {"/analyze": lambda body: (500, {"error": "boom"})}
    with MockBackend(routes) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request", json=req_envelope("ssn 123-45-6789"), timeout=10)
        finally:
            stop()
    assert r.json()["action"]["status_code"] == 403


def test_low_confidence_findings_are_ignored():
    routes = {"/analyze": lambda body: (200, [
        {"entity_type": "PERSON", "start": 0, "end": 3, "score": 0.2}
    ])}
    with MockBackend(routes) as backend:
        module = boot(backend.url)
        base, stop = serve(module)
        try:
            r = httpx.post(f"{base}/request", json=req_envelope("bob went home"), timeout=10)
        finally:
            stop()
    assert "status_code" not in r.json()["action"]
