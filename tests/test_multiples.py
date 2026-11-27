"""Offline unit and integration tests for Current vs. Implied Market Multiples."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sec_dcf.analysis.dcf import run_dcf_valuation, suggest_dcf_assumptions
from sec_dcf.analysis.multiples import compute_market_multiples
from sec_dcf.analysis.wacc import calculate_wacc
from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import WaccInputs
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport
from sec_dcf.ui.demo_loader import load_demo_financials


def _make_period(fiscal_year: int = 2025, **overrides) -> AnnualFinancialPeriod:
    base = dict(
        fiscal_year=fiscal_year,
        period_start=date(fiscal_year, 1, 1),
        period_end=date(fiscal_year, 12, 31),
        revenue=Decimal("1000"),
        ebit=Decimal("200"),
        da=Decimal("50"),
        interest_expense=Decimal("10"),
        pretax_income=Decimal("190"),
        tax_expense=Decimal("38"),
        net_income=Decimal("152"),
        diluted_shares=Decimal("100"),
        cash_and_equivalents=Decimal("80"),
        short_term_investments=Decimal("20"),
        current_assets=Decimal("300"),
        total_assets=Decimal("1000"),
        current_liabilities=Decimal("150"),
        short_term_debt=Decimal("20"),
        long_term_debt=Decimal("180"),
        current_lease_liabilities=Decimal("10"),
        noncurrent_lease_liabilities=Decimal("40"),
        total_liabilities=Decimal("400"),
        stockholders_equity=Decimal("600"),
        noncontrolling_interest=Decimal("10"),
        equity_includes_nci=False,
        ending_shares=Decimal("98"),
        cfo=Decimal("210"),
        capex=Decimal("60"),
        sbc=Decimal("30"),
    )
    base.update(overrides)
    return AnnualFinancialPeriod(**base)


def _make_report(periods: list[AnnualFinancialPeriod] | None = None) -> CompanyFinancialsReport:
    return CompanyFinancialsReport(
        ticker="TEST",
        cik="0000999999",
        company_name="Test Corp",
        periods=periods or [_make_period(2025)],
    )


def test_market_multiples_exact_math_with_and_without_leases():
    report = _make_report()
    # share_price=20, diluted_shares=100 -> Market Cap = 2000
    # total_cash=100, financial_debt=200 -> net_debt_excl=100, NCI=10 -> EV_excl = 2110
    # lease_liabilities=50 -> net_debt_incl=150, NCI=10 -> EV_incl = 2160
    # revenue=1000, ebitda=250, ebit=200, net_income=152, fcf_simple=150, fcf_ex_sbc=120
    res_excl = compute_market_multiples(
        report,
        include_leases=False,
        share_price=Decimal("20"),
    )
    assert res_excl.current_market_cap == Decimal("2000")
    assert res_excl.net_debt == Decimal("100")
    assert res_excl.current_enterprise_value == Decimal("2110")
    assert res_excl.current.ev_to_revenue == Decimal("2.11")
    assert res_excl.current.ev_to_ebitda == Decimal("2110") / Decimal("250")
    assert res_excl.current.ev_to_ebit == Decimal("2110") / Decimal("200")
    assert res_excl.current.pe_ratio == Decimal("2000") / Decimal("152")
    assert res_excl.current.reported_fcf_yield == Decimal("150") / Decimal("2000")
    assert res_excl.current.true_fcf_yield_ex_sbc == Decimal("120") / Decimal("2000")

    res_incl = compute_market_multiples(
        report,
        include_leases=True,
        share_price=Decimal("20"),
    )
    assert res_incl.net_debt == Decimal("150")
    assert res_incl.current_enterprise_value == Decimal("2160")
    assert res_incl.current.ev_to_ebitda == Decimal("2160") / Decimal("250")


def test_market_multiples_confrontation_with_dcf_and_edge_cases():
    report = _make_report()
    assumptions = suggest_dcf_assumptions(report, include_leases=True)
    wacc_res = calculate_wacc(
        report,
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.1"),
            equity_risk_premium=Decimal("0.05"),
            market_cap_override=Decimal("2500"),
            include_leases=True,
        ),
    )
    dcf_res = run_dcf_valuation(report, assumptions, wacc_res)

    cmp_res = compute_market_multiples(
        report,
        include_leases=True,
        market_cap_override=Decimal("2500"),
        dcf_result=dcf_res,
    )
    assert cmp_res.current_market_cap == Decimal("2500")
    assert cmp_res.share_price_used == Decimal("25")
    assert cmp_res.implied_dcf.ev_to_ebitda == dcf_res.bridge.enterprise_value / Decimal("250")
    assert cmp_res.implied_dcf.true_fcf_yield_ex_sbc == (
        Decimal("120") / dcf_res.bridge.implied_equity_value
    )

    # Book equity fallback (no share_price / market_cap) -> current multiples are None
    no_mkt = compute_market_multiples(report, include_leases=True, dcf_result=dcf_res)
    assert no_mkt.current_market_cap is None
    assert no_mkt.current.ev_to_ebitda is None
    assert no_mkt.implied_dcf.ev_to_ebitda is not None
    assert len(no_mkt.notes) >= 1

    # Negative net income -> P/E is None; include_leases mismatch raises DcfValuationError
    loss_report = _make_report([_make_period(2025, net_income=Decimal("-10"))])
    loss_cmp = compute_market_multiples(loss_report, share_price=Decimal("20"))
    assert loss_cmp.current.pe_ratio is None

    with pytest.raises(DcfValuationError, match="include_leases mismatch"):
        compute_market_multiples(report, include_leases=False, dcf_result=dcf_res)


def test_market_multiples_offline_demo_amzn():
    report = load_demo_financials("AMZN")
    assumptions = suggest_dcf_assumptions(report, include_leases=True)
    dcf_res = run_dcf_valuation(report, assumptions, Decimal("0.09"))
    cmp_res = compute_market_multiples(
        report,
        include_leases=True,
        share_price=Decimal("210"),
        dcf_result=dcf_res,
    )
    assert cmp_res.ticker == "AMZN"
    assert cmp_res.current.ev_to_revenue is not None and cmp_res.current.ev_to_revenue > Decimal("0")
    assert cmp_res.current.reported_fcf_yield is not None
    assert cmp_res.current.true_fcf_yield_ex_sbc is not None
    assert cmp_res.current.true_fcf_yield_ex_sbc < cmp_res.current.reported_fcf_yield
