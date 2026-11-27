"""Offline unit tests for GeminiQualitativeAnalyzer using mocked google-genai client."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sec_dcf.exceptions import GeminiApiError
from sec_dcf.models.qualitative import ExtractedRiskItem, QualitativeExtractionPayload
from sec_dcf.qualitative.extractor import build_sections_from_texts
from sec_dcf.qualitative.gemini_client import (
    DEFAULT_GEMINI_MODEL,
    GeminiQualitativeAnalyzer,
)


@pytest.fixture(autouse=True)
def block_network_gemini_calls(monkeypatch):
    """Forbid real google.genai.Client network instantiation unless explicitly mocked."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Real genai.Client instantiated during offline tests!")

    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.genai.Client", _forbid)


def _sample_doc():
    return build_sections_from_texts(
        ticker="ACME",
        company_name="Acme Corp",
        item_1a_text="Supply chain disruptions could adversely impact our quarterly hardware deliveries.",
        item_7_text="Enterprise subscription revenue grew 20% due to strong customer retention.",
    )


def test_missing_gemini_api_key_raises_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.load_dotenv", lambda **kwargs: False)

    with pytest.raises(GeminiApiError, match="GEMINI_API_KEY is missing"):
        GeminiQualitativeAnalyzer()


def test_client_configures_millisecond_timeout_and_model(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, *, api_key=None, http_options=None, **kwargs):
            captured["api_key"] = api_key
            captured["http_options"] = http_options
            self.models = SimpleNamespace()

    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.genai.Client", FakeClient)

    analyzer = GeminiQualitativeAnalyzer(
        api_key="secret-test-key",
        model="gemini-4-hydrogen",
        timeout_seconds=45.0,
        load_env=False,
    )
    assert analyzer.model == "gemini-4-hydrogen"
    assert captured["api_key"] == "secret-test-key"
    assert captured["http_options"].timeout == 45_000


def test_analyze_sections_uses_structured_output_and_parses_response():
    expected_payload = QualitativeExtractionPayload(
        executive_summary="Focused summary.",
        risks=[
            ExtractedRiskItem(
                title="Supply chain risk",
                category="supply_chain",
                materiality="high",
                summary="Potential delivery delays.",
                section="Item 1A",
                verbatim_quote="Supply chain disruptions could adversely impact our quarterly hardware deliveries.",
            )
        ],
    )
    calls: list[dict[str, Any]] = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append({"model": model, "contents": contents, "config": config})
            return SimpleNamespace(parsed=expected_payload, text=expected_payload.model_dump_json())

    fake_client = SimpleNamespace(models=FakeModels())
    analyzer = GeminiQualitativeAnalyzer(client=fake_client)

    assert analyzer.model == DEFAULT_GEMINI_MODEL
    result = analyzer.analyze_sections(_sample_doc())

    assert result == expected_payload
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-3.8-flash"
    cfg = calls[0]["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is QualitativeExtractionPayload
    assert cfg.temperature == 0.0
    assert "NEVER perform financial calculations" in cfg.system_instruction
    assert "leases not yet commenced" in cfg.system_instruction


def test_analyze_sections_falls_back_to_response_text_json():
    payload = QualitativeExtractionPayload(executive_summary="JSON text fallback works.")

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            return SimpleNamespace(parsed=None, text=payload.model_dump_json())

    analyzer = GeminiQualitativeAnalyzer(client=SimpleNamespace(models=FakeModels()))
    result = analyzer.analyze_sections(_sample_doc())
    assert result.executive_summary == "JSON text fallback works."


def test_api_error_masks_byok_key_and_invalid_json_raises(monkeypatch):
    secret = "AIzaSySecretKey1234567890"

    class FailingModels:
        def generate_content(self, **kwargs):
            raise RuntimeError(
                f"Upstream error at https://generativelanguage.googleapis.com?key={secret} using {secret}"
            )

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FailingModels()

    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.genai.Client", FakeClient)

    analyzer = GeminiQualitativeAnalyzer(api_key=secret, load_env=False)
    with pytest.raises(GeminiApiError) as exc_info:
        analyzer.analyze_sections(_sample_doc())

    err_msg = str(exc_info.value)
    assert secret not in err_msg
    assert "***" in err_msg

    # Invalid JSON payload from mock client
    class BadJsonModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(parsed=None, text="{not valid json")

    bad_analyzer = GeminiQualitativeAnalyzer(client=SimpleNamespace(models=BadJsonModels()))
    with pytest.raises(GeminiApiError, match="schema validation"):
        bad_analyzer.analyze_sections(_sample_doc())
