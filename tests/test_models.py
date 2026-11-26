"""Unit tests for canonical Pydantic financial models."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sec_dcf.models.financials import (
    AnnualFinancialPeriod,
    CompanyFinancialsReport,
    ValidationIssue,
)


def _sample_period(**overrides) -> AnnualFinancialPeriod:
    base = dict(
        fiscal_year=2025,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        revenue=Decimal("1000000000"),
        ebit=Decimal("200000000"),
        da=Decimal("50000000"),
        interest_expense=Decimal("10000000"),
        pretax_income=Decimal("190000000"),
        tax_expense=Decimal("40000000"),
        net_income=Decimal("150000000"),
        diluted_shares=Decimal("100000000"),
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
        equity_includes_nci=False,
        ending_shares=Decimal("98000000"),
        cfo=Decimal("210000000"),
        capex=Decimal("60000000"),
    )
    base.update(overrides)
    return AnnualFinancialPeriod(**base)


def test_computed_financial_metrics_precision_and_no_double_count():
    p = _sample_period()
    assert isinstance(p.total_cash, Decimal)
    assert p.total_cash == Decimal("150000000")
    assert p.financial_debt == Decimal("220000000")
    assert p.lease_liabilities == Decimal("50000000")
    assert p.total_debt_incl_leases == Decimal("270000000")
    # Operating CA = 300M - 150M = 150M; Operating CL = 180M - 20M - 10M = 150M
    assert p.non_cash_nwc == Decimal("0")
    assert p.fcf_simple == Decimal("150000000")


def test_equity_includes_nci_prevents_double_counting():
    p_sep = _sample_period(
        stockholders_equity=Decimal("500000000"),
        noncontrolling_interest=Decimal("50000000"),
        equity_includes_nci=False,
    )
    assert p_sep.total_equity_for_balance_check == Decimal("550000000")

    p_incl = _sample_period(
        stockholders_equity=Decimal("550000000"),
        noncontrolling_interest=Decimal("50000000"),
        equity_includes_nci=True,
    )
    assert p_incl.total_equity_for_balance_check == Decimal("550000000")


def test_non_cash_nwc_returns_none_when_current_items_absent():
    p = _sample_period(current_assets=None)
    assert p.non_cash_nwc is None


def test_negative_capex_rejected_by_model():
    with pytest.raises(ValidationError):
        _sample_period(capex=Decimal("-100"))


def test_report_is_valid_reflects_errors_vs_warnings():
    period = _sample_period()
    empty_report = CompanyFinancialsReport(
        ticker="TEST", cik="0000000001", company_name="Test Corp"
    )
    assert empty_report.is_valid is False

    warn_report = CompanyFinancialsReport(
        ticker="TEST",
        cik="0000000001",
        company_name="Test Corp",
        periods=[period],
        validation_issues=[
            ValidationIssue(
                code="SOME_WARNING",
                severity="warning",
                fiscal_year=2025,
                message="Informational warning",
            )
        ],
    )
    assert warn_report.is_valid is True

    err_report = warn_report.model_copy(
        update={
            "validation_issues": [
                ValidationIssue(
                    code="SOME_ERROR",
                    severity="error",
                    fiscal_year=2025,
                    message="Fatal error",
                )
            ]
        }
    )
    assert err_report.is_valid is False
