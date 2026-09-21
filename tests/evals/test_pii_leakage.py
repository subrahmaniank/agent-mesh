import pytest
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

@pytest.fixture(scope="module")
def analyzer():
    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]
    })
    nlp_engine = provider.create_engine()
    return AnalyzerEngine(nlp_engine=nlp_engine)

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
    raw_payload = "Target client email is client@enterprise.com and phone is 415-555-0199."
    entities = analyzer.analyze(
        text=raw_payload,
        entities=["EMAIL_ADDRESS", "PHONE_NUMBER"],
        language="en"
    )
    assert len(entities) > 0, "Presidio failed to detect PII in unmasked input"
