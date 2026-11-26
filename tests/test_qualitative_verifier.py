"""Offline unit tests for deterministic quote verifier and compliance scanner."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sec_dcf.exceptions import QualitativeAuditError
from sec_dcf.models.qualitative import (
    ExtractedGrowthDriver,
    ExtractedRedFlag,
    ExtractedRiskItem,
    QualitativeExtractionPayload,
)
from sec_dcf.qualitative.extractor import build_sections_from_texts
from sec_dcf.qualitative.verifier import (
    audit_qualitative_payload,
    normalize_for_quote_match,
    scan_prohibited_advice,
    verify_quote_in_section,
)


def test_normalize_for_quote_match_whitespace_and_typography():
    raw = (
        "Cyber\u00adsecurity\u200b\ufeff  and  suppliers\u2019\xa0\u201caccelerated\u202f"
        "growth\u201d\u2014across\n\tmultiple   regions."
    )
    normalized = normalize_for_quote_match(raw)
    assert normalized == 'Cybersecurity and suppliers\' "accelerated growth"-across multiple regions.'


def test_verify_quote_in_section_matches_despite_edgar_formatting():
    section = (
        "We rely on third-party\xa0foundries.\n"
        "Any disruption affecting these suppliers\u2019 facilities\n"
        "could delay product shipments and materially harm our cyber\u00adsecurity posture."
    )
    quote = "these suppliers' facilities could delay product shipments and materially harm our cybersecurity posture."
    ok, note = verify_quote_in_section(quote, section)
    assert ok is True
    assert note is None


def test_verify_quote_rejects_short_or_empty_quote():
    section = "Material risk disclosures in Item 1A of the annual Form 10-K report."
    ok_empty, note_empty = verify_quote_in_section("   ", section)
    ok_short, note_short = verify_quote_in_section("risk", section)

    assert ok_empty is False
    assert note_empty is not None
    assert ok_short is False
    assert "below minimum audit length" in (note_short or "")


def test_audit_detects_fabricated_quote_and_section_mismatch():
    doc = build_sections_from_texts(
        ticker="ACME",
        company_name="Acme Corp",
        item_1a_text="Supply chain concentration risk could delay customer deliveries across global markets.",
        item_7_text="Enterprise cloud revenue expanded strongly due to multi-year platform subscriptions.",
    )
    payload = QualitativeExtractionPayload(
        executive_summary="Summary grounded in 10-K disclosures.",
        risks=[
            ExtractedRiskItem(
                title="Supply chain concentration",
                category="supply_chain",
                materiality="high",
                summary="Single-region dependency may delay deliveries.",
                section="Item 1A",
                verbatim_quote="Supply chain concentration risk could delay customer deliveries across global markets.",
            ),
            ExtractedRiskItem(
                title="Fabricated litigation risk",
                category="legal_governance",
                materiality="medium",
                summary="Claimed class action lawsuit.",
                section="Item 1A",
                verbatim_quote="This completely fabricated sentence never appeared in the 10-K filing.",
            ),
        ],
        growth_drivers=[
            ExtractedGrowthDriver(
                title="Cloud expansion (wrong section)",
                summary="Multi-year subscription growth.",
                management_tone="positive",
                section="Item 1A",  # Deliberately mislabeled (quote is in Item 7)
                verbatim_quote="Enterprise cloud revenue expanded strongly due to multi-year platform subscriptions.",
            )
        ],
        red_flags=[],
    )

    report = audit_qualitative_payload(doc, payload, model_name="gemini-3.8-flash", strict=False)
    assert report.total_claims == 3
    assert report.verified_claims == 1
    assert report.verified_quote_ratio == Decimal("0.3333")
    assert report.risks[0].quote_verified is True
    assert report.risks[1].quote_verified is False
    assert report.growth_drivers[0].quote_verified is False
    assert "section mismatch" in (report.growth_drivers[0].verification_note or "")

    with pytest.raises(QualitativeAuditError, match="unverified quotes"):
        audit_qualitative_payload(doc, payload, model_name="gemini-3.8-flash", strict=True)


def test_audit_verifies_against_full_raw_text_even_when_prompt_truncated():
    prefix = "Introductory boilerplate text. " * 20
    tail_quote = "Late-section disclosure regarding long-term debt refinancing obligations."
    full_1a = prefix + tail_quote

    doc = build_sections_from_texts(
        ticker="ACME",
        company_name="Acme Corp",
        item_1a_text=full_1a,
        item_7_text="MD&A overview text of sufficient length.",
        max_chars_per_section=120,
    )
    assert doc.item_1a is not None
    assert doc.item_1a.truncated is True
    assert tail_quote not in doc.item_1a.prompt_text

    payload = QualitativeExtractionPayload(
        executive_summary="Refinancing risk summary.",
        risks=[
            ExtractedRiskItem(
                title="Refinancing risk",
                category="financial_liquidity",
                materiality="medium",
                summary="Debt refinancing obligations noted in Item 1A.",
                section="Item 1A",
                verbatim_quote=tail_quote,
            )
        ],
    )
    report = audit_qualitative_payload(doc, payload, model_name="gemini-3.8-flash", strict=True)
    assert report.verified_quote_ratio == Decimal("1.0000")
    assert report.section_truncation_status == {"Item 1A": True, "Item 7": False}


def test_audit_zero_claims_adds_warning_and_fails_in_strict_mode():
    doc = build_sections_from_texts(
        ticker="ACME",
        company_name="Acme Corp",
        item_1a_text="General business risks apply to our operations.",
        item_7_text="Revenue grew year over year.",
    )
    empty_payload = QualitativeExtractionPayload(
        executive_summary="No items extracted.",
        risks=[],
        growth_drivers=[],
        red_flags=[],
    )

    report = audit_qualitative_payload(doc, empty_payload, model_name="gemini-3.8-flash", strict=False)
    assert report.total_claims == 0
    assert report.verified_claims == 0
    assert report.verified_quote_ratio == Decimal("0")
    assert any("NO_CLAIMS_EXTRACTED" in w for w in report.warnings)

    with pytest.raises(QualitativeAuditError, match="NO_CLAIMS_EXTRACTED"):
        audit_qualitative_payload(doc, empty_payload, model_name="gemini-3.8-flash", strict=True)


def test_compliance_scanner_flags_ai_narrative_but_skips_verbatim_quote():
    doc = build_sections_from_texts(
        ticker="ACME",
        company_name="Acme Corp",
        item_1a_text="Analysts publish a price target and buy rating that can increase stock volatility.",
        item_7_text="Revenue expanded steadily across enterprise accounts.",
    )
    clean_narrative_with_quote_mention = QualitativeExtractionPayload(
        executive_summary="Management notes analyst commentary volatility without making recommendations.",
        risks=[
            ExtractedRiskItem(
                title="Market volatility from analyst coverage",
                category="other",
                materiality="low",
                summary="External analyst coverage changes can increase share price volatility.",
                section="Item 1A",
                verbatim_quote="Analysts publish a price target and buy rating that can increase stock volatility.",
            )
        ],
    )
    assert scan_prohibited_advice(clean_narrative_with_quote_mention) == []

    prohibited_narrative = QualitativeExtractionPayload(
        executive_summary="We issue a Strong Buy with a price target of $250.",
        red_flags=[
            ExtractedRedFlag(
                title="Rekomendacja kupna",
                severity="low",
                rationale="Kupuj akcje przed wynikami.",
                section="Item 7",
                verbatim_quote="Revenue expanded steadily across enterprise accounts.",
            )
        ],
    )
    flags = scan_prohibited_advice(prohibited_narrative)
    assert len(flags) >= 3
    paths = {f.field_path for f in flags}
    assert "executive_summary" in paths
    assert "red_flags[0].title" in paths
    assert "red_flags[0].rationale" in paths

    with pytest.raises(QualitativeAuditError, match="compliance flag"):
        audit_qualitative_payload(
            doc,
            prohibited_narrative,
            model_name="gemini-3.8-flash",
            strict=True,
        )
