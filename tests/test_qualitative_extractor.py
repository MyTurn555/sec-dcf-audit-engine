"""Offline unit tests for 10-K text extraction (edgartools mocks & local text builder)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from sec_dcf.exceptions import (
    DataFetchError,
    EdgarIdentityError,
    UnsupportedCompanyTypeError,
)
from sec_dcf.qualitative.extractor import TenKTextExtractor, build_sections_from_texts


@pytest.fixture(autouse=True)
def block_network_sec_calls(monkeypatch):
    """Ensure extractor tests never make live SEC EDGAR calls."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Live SEC EDGAR call attempted during offline tests!")

    monkeypatch.setattr("edgar.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.extractor.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.extractor.set_identity", lambda _: None)


def test_extractor_requires_valid_edgar_identity(monkeypatch):
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.setattr("sec_dcf.ingestion.edgar_client.load_dotenv", lambda **kwargs: False)

    with pytest.raises(EdgarIdentityError):
        TenKTextExtractor(identity=None)

    with pytest.raises(EdgarIdentityError):
        TenKTextExtractor(identity="missing-at-sign")


def test_extractor_blocks_financial_institution(monkeypatch):
    mock_company = SimpleNamespace(
        name="Mock Bank Corp",
        sic="6021",
        not_found=False,
        is_financial_institution=lambda: True,
    )
    monkeypatch.setattr("sec_dcf.qualitative.extractor.Company", lambda _: mock_company)

    extractor = TenKTextExtractor(identity="AuditTest test@example.com")
    with pytest.raises(UnsupportedCompanyTypeError):
        extractor.fetch_10k_sections("JPM")


def test_extractor_fetches_sections_and_supports_getitem_fallback(monkeypatch):
    class FallbackTenK:
        def __init__(self):
            self.risk_factors = None  # Force fallback to ["Item 1A"]
            self.management_discussion = "Item 7 direct attribute text for management discussion."

        def __getitem__(self, key: str):
            if key == "Item 1A":
                return SimpleNamespace(text=lambda: "Item 1A fallback section text from indexing.")
            raise KeyError(key)

    mock_filing = SimpleNamespace(
        form="10-K",
        filing_date="2026-02-15",
        period_of_report="2025-12-31",
        accession_no="0000111111-26-000001",
        obj=lambda: FallbackTenK(),
    )
    mock_filings = SimpleNamespace(latest=lambda: mock_filing)
    mock_company = SimpleNamespace(
        name="Mock Operating Corp",
        cik="0000111111",
        sic="7372",
        not_found=False,
        is_financial_institution=lambda: False,
        get_filings=lambda form, amendments=False: mock_filings,
    )
    monkeypatch.setattr("sec_dcf.qualitative.extractor.Company", lambda _: mock_company)

    extractor = TenKTextExtractor(identity="AuditTest test@example.com")
    doc = extractor.fetch_10k_sections("MOCK")

    assert doc.ticker == "MOCK"
    assert doc.company_name == "Mock Operating Corp"
    assert doc.filing_date == date(2026, 2, 15)
    assert doc.period_of_report == date(2025, 12, 31)
    assert doc.item_1a is not None
    assert "fallback section text" in doc.item_1a.raw_text
    assert doc.item_7 is not None
    assert "direct attribute text" in doc.item_7.raw_text
    assert doc.warnings == []


def test_build_sections_from_texts_missing_one_vs_both():
    partial = build_sections_from_texts(
        ticker="ACME",
        company_name="Acme Corp",
        item_1a_text="Only Item 1A is present in this filing.",
        item_7_text=None,
    )
    assert partial.item_1a is not None
    assert partial.item_7 is None
    assert any("MISSING_SECTION: Item 7" in w for w in partial.warnings)

    with pytest.raises(DataFetchError, match="Neither Item 1A"):
        build_sections_from_texts(
            ticker="ACME",
            company_name="Acme Corp",
            item_1a_text="   ",
            item_7_text=None,
        )
