"""Unit tests for deterministic accounting consistency and validation rules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sec_dcf.exceptions import AccountingValidationError
from sec_dcf.models.financials import (
    AnnualFinancialPeriod,
    CompanyFinancialsReport,
    LineItemAudit,
)
from sec_dcf.validation.accounting import validate_financials


def _make_period(fiscal_year: int = 2025, **overrides) -> AnnualFinancialPeriod:
    base = dict(
        fiscal_year=fiscal_year,
        period_start=date(fiscal_year, 1, 1),
        period_end=date(fiscal_year, 12, 31),
        revenue=Decimal("1000000000"),
        ebit=Decimal("200000000"),
        da=Decimal("50000000"),
        interest_expense=Decimal("10000000"),
        pretax_income=Decimal("190000000"),
        tax_expense=Decimal("40000000"),
        net_income=Decimal("150000000"),
        cash_and_equivalents=Decimal("100000000"),
        short_term_investments=Decimal("50000000"),
        current_assets=Decimal("300000000"),
        total_assets=Decimal("1000000000"),
        current_liabilities=Decimal("180000000"),
        short_term_debt=Decimal("20000000"),
        long_term_debt=Decimal("200000000"),
        current_lease_liabilities=Decimal("10000000"),
        noncurrent_lease_liabilities=Decimal("40000000"),
        total_liabilities=Decimal("450000000"),
        stockholders_equity=Decimal("550000000"),
        noncontrolling_interest=Decimal("0"),
        cfo=Decimal("210000000"),
        capex=Decimal("60000000"),
    )
    base.update(overrides)
    return AnnualFinancialPeriod(**base)


def _make_report(periods: list[AnnualFinancialPeriod]) -> CompanyFinancialsReport:
    return CompanyFinancialsReport(
        ticker="TEST",
        cik="0000000001",
        company_name="Test Corp",
        periods=periods,
    )


def test_balance_sheet_tolerance_boundary():
    # Total Assets = 2,000,000,000 -> tolerance = 0.1% = 2,000,000
    within_tol = _make_period(
        total_assets=Decimal("2000000000"),
        total_liabilities=Decimal("998000000"),
        stockholders_equity=Decimal("1000000000"),
    )
    rep_ok = validate_financials(_make_report([within_tol]), strict=True, expected_years=1)
    assert rep_ok.is_valid is True

    over_tol = _make_period(
        total_assets=Decimal("2000000000"),
        total_liabilities=Decimal("997000000"),
        stockholders_equity=Decimal("1000000000"),
    )
    rep_bad = validate_financials(_make_report([over_tol]), strict=False, expected_years=1)
    assert rep_bad.is_valid is False
    assert any(i.code == "BALANCE_SHEET_IMBALANCE" for i in rep_bad.validation_issues)

    with pytest.raises(AccountingValidationError):
        validate_financials(_make_report([over_tol]), strict=True, expected_years=1)


def test_balance_sheet_derived_from_assets_skips_tautological_error():
    p = _make_period(
        total_assets=Decimal("1000000000"),
        total_liabilities=Decimal("450000000"),
        stockholders_equity=Decimal("550000000"),
        audit_trail={
            "total_liabilities": LineItemAudit(
                field_name="total_liabilities",
                source_kind="derived_from_assets",
            )
        },
    )
    rep = validate_financials(_make_report([p]), strict=True, expected_years=1)
    assert rep.is_valid is True
    assert any(
        i.code == "BALANCE_SHEET_IDENTITY_UNVERIFIED_DERIVED_LIABILITIES"
        for i in rep.validation_issues
    )


def test_pretax_tax_net_income_check_direct_and_handles_losses():
    # Must compare pretax - tax directly with net_income (ignoring balance sheet NCI)
    p_mismatch = _make_period(
        pretax_income=Decimal("200000000"),
        tax_expense=Decimal("40000000"),
        net_income=Decimal("140000000"),
        stockholders_equity=Decimal("530000000"),
        noncontrolling_interest=Decimal("20000000"),
    )
    rep = validate_financials(_make_report([p_mismatch]), strict=True, expected_years=1)
    assert any(i.code == "PRETAX_TAX_NET_INCOME_MISMATCH" for i in rep.validation_issues)

    # Loss scenario: pretax = -500M, tax benefit = -100M, net_income = -400M -> matches!
    # Tolerance uses abs(pretax_income) = 500M -> 1% = 5M (no false alarm)
    p_loss = _make_period(
        pretax_income=Decimal("-500000000"),
        tax_expense=Decimal("-100000000"),
        net_income=Decimal("-400000000"),
    )
    rep_loss = validate_financials(_make_report([p_loss]), strict=True, expected_years=1)
    assert not any(
        i.code == "PRETAX_TAX_NET_INCOME_MISMATCH" for i in rep_loss.validation_issues
    )


def test_duplicate_period_and_irregular_gap_detection():
    p1 = _make_period(fiscal_year=2024, period_end=date(2024, 12, 31))
    p2 = _make_period(fiscal_year=2024, period_end=date(2024, 12, 31))
    rep_dup = validate_financials(_make_report([p1, p2]), strict=False, expected_years=2)
    assert rep_dup.is_valid is False
    assert any(i.code == "DUPLICATE_REPORTING_PERIOD" for i in rep_dup.validation_issues)

    p_short_gap = _make_period(fiscal_year=2025, period_end=date(2025, 6, 30))
    rep_gap = validate_financials(_make_report([p1, p_short_gap]), strict=False, expected_years=2)
    assert rep_gap.is_valid is False
    assert any(i.code == "IRREGULAR_PERIOD_GAP" for i in rep_gap.validation_issues)
