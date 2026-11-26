"""Offline integration tests using saved SEC EDGAR MSFT and AAPL fixtures."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sec_dcf.ingestion.normalizer import normalize_entity_facts
from sec_dcf.validation.accounting import balance_sheet_tolerance, validate_financials


@pytest.fixture(autouse=True)
def block_network_sec_calls(monkeypatch):
    """Ensure offline integration tests never contact SEC EDGAR over the network."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Network SEC EDGAR call attempted during offline tests!")

    monkeypatch.setattr("edgar.Company", _forbid)
    monkeypatch.setattr("sec_dcf.ingestion.edgar_client.Company", _forbid)


@pytest.mark.parametrize("fixture_name,expected_ticker", [("aapl_fixture", "AAPL"), ("msft_fixture", "MSFT")])
def test_five_year_10k_ingestion_and_validation_offline(request, fixture_name, expected_ticker):
    raw, facts = request.getfixturevalue(fixture_name)

    report = normalize_entity_facts(
        facts=facts,
        ticker=raw["ticker"],
        sic=raw["sic"],
        fiscal_year_end=raw["fiscalYearEnd"],
        years=5,
        strict=True,
    )
    validated = validate_financials(report, strict=True, expected_years=5)

    assert validated.ticker == expected_ticker
    assert validated.is_valid is True
    assert len(validated.periods) == 5

    fiscal_years = [p.fiscal_year for p in validated.periods]
    assert fiscal_years == sorted(fiscal_years)
    assert len(set(fiscal_years)) == 5

    for period in validated.periods:
        # All 9 core lines + DCF supporting metrics populated and positive where expected
        assert period.revenue > Decimal("0")
        assert period.ebit > Decimal("0")
        assert period.da > Decimal("0")
        assert period.net_income > Decimal("0")
        assert period.total_cash > Decimal("0")
        assert period.financial_debt >= Decimal("0")
        assert period.stockholders_equity > Decimal("0")
        assert period.cfo > Decimal("0")
        assert period.capex >= Decimal("0")
        assert period.fcf_simple == period.cfo - period.capex
        assert period.diluted_shares is not None and period.diluted_shares > Decimal("0")
        assert period.ending_shares is not None and period.ending_shares > Decimal("0")
        assert period.non_cash_nwc is not None

        # Exact/tolerant accounting identity
        rhs = period.total_liabilities + period.total_equity_for_balance_check
        assert abs(period.total_assets - rhs) <= balance_sheet_tolerance(period.total_assets)

        # Complete audit trail
        for required_key in (
            "revenue",
            "ebit",
            "da",
            "net_income",
            "cash_and_equivalents",
            "short_term_debt",
            "long_term_debt",
            "total_liabilities",
            "stockholders_equity",
            "cfo",
            "capex",
        ):
            assert required_key in period.audit_trail
