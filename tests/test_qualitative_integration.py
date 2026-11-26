"""End-to-end offline integration tests for Module 3 using local 10-K text fixtures."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from sec_dcf import (
    ExtractedGrowthDriver,
    ExtractedRedFlag,
    ExtractedRiskItem,
    GeminiQualitativeAnalyzer,
    QualitativeExtractionPayload,
    analyze_10k_qualitative,
    build_sections_from_texts,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def block_all_network_calls(monkeypatch):
    """Guarantee zero outbound network calls during offline integration tests."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Outbound network call attempted during offline Module 3 test!")

    monkeypatch.setattr("edgar.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.extractor.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.genai.Client", _forbid)
    monkeypatch.setattr("urllib.request.urlopen", _forbid)
    monkeypatch.setattr("socket.create_connection", _forbid)


def test_end_to_end_qualitative_audit_with_local_fixture():
    fixture_data = json.loads(
        (FIXTURES_DIR / "sample_10k_sections.json").read_text(encoding="utf-8")
    )
    doc = build_sections_from_texts(
        ticker=fixture_data["ticker"],
        company_name=fixture_data["company_name"],
        cik=fixture_data["cik"],
        filing_date=fixture_data["filing_date"],
        period_of_report=fixture_data["period_of_report"],
        accession_number=fixture_data["accession_number"],
        item_1a_text=fixture_data["item_1a"],
        item_7_text=fixture_data["item_7"],
    )

    # 2 valid verbatim quotes (despite EDGAR \xa0, \u00ad, curly quotes) + 1 fabricated quote
    mock_payload = QualitativeExtractionPayload(
        executive_summary=(
            "Acme Cloud Systems faces foundry concentration and cybersecurity risks while "
            "growing enterprise platform adoption and extending distributor payment terms."
        ),
        risks=[
            ExtractedRiskItem(
                title="Taiwan foundry concentration",
                category="supply_chain",
                materiality="high",
                summary="Reliance on limited Taiwanese foundries for AI accelerators.",
                section="Item 1A",
                verbatim_quote=(
                    "Any geopolitical disruption, export restriction, or natural disaster "
                    "affecting these suppliers' facilities could delay product shipments "
                    "and materially harm our revenue."
                ),
            ),
            ExtractedRiskItem(
                title="Fabricated patent lawsuit",
                category="legal_governance",
                materiality="medium",
                summary="Unverified litigation claim.",
                section="Item 1A",
                verbatim_quote="A federal jury ordered the company to halt all cloud sales immediately.",
            ),
        ],
        growth_drivers=[
            ExtractedGrowthDriver(
                title="Enterprise inference adoption",
                summary="Revenue growth supported by enterprise platform adoption and retention.",
                management_tone="positive",
                section="Item 7",
                verbatim_quote=(
                    'Consolidated revenue increased 24% year-over-year in fiscal 2025, '
                    'driven primarily by "accelerated enterprise adoption" of our '
                    "multi-region inference platform"
                ),
            )
        ],
        red_flags=[
            ExtractedRedFlag(
                title="Extended distributor payment terms",
                severity="medium",
                rationale="Payment terms doubled from 45 to 90 days, outpacing revenue with AR growth.",
                section="Item 7",
                verbatim_quote=(
                    "we extended payment terms for select strategic distributors from 45 days "
                    "to 90 days, which contributed to an expansion in accounts receivable faster "
                    "than revenue growth."
                ),
            )
        ],
    )

    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(parsed=mock_payload, text=mock_payload.model_dump_json())

    analyzer = GeminiQualitativeAnalyzer(
        model="gemini-4-hydrogen",
        client=SimpleNamespace(models=FakeModels()),
    )

    report = analyze_10k_qualitative(document=doc, analyzer=analyzer, strict=False)

    assert report.ticker == "ACME"
    assert report.model_name == "gemini-4-hydrogen"
    assert report.total_claims == 4
    assert report.verified_claims == 3
    assert report.verified_quote_ratio == Decimal("0.7500")
    assert report.risks[0].quote_verified is True
    assert report.risks[1].quote_verified is False
    assert report.growth_drivers[0].quote_verified is True
    assert report.red_flags[0].quote_verified is True
    assert report.compliance_flags == []
    assert "Educational & research use only" in report.educational_disclaimer
