"""Offline unit and integration tests for historical financial ratios and ROIC."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sec_dcf.analysis.ratios import compute_historical_metrics
from sec_dcf.exceptions import DcfValuationError
from sec_dcf.ingestion.normalizer import normalize_entity_facts
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport


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
        tax_expense=Decimal("38"),  # 20% ETR
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
        noncontrolling_interest=Decimal("0"),
        equity_includes_nci=False,
        ending_shares=Decimal("98"),
        cfo=Decimal("210"),
        capex=Decimal("60"),
    )
    base.update(overrides)
    return AnnualFinancialPeriod(**base)


def _make_report(periods: list[AnnualFinancialPeriod], ticker: str = "SYNTH") -> CompanyFinancialsReport:
    return CompanyFinancialsReport(
        ticker=ticker,
        cik="0000999999",
        company_name="Synthetic Corp",
        periods=periods,
    )


def test_margins_nopat_ic_roic_cash_and_leverage_exact_math():
    p1 = _make_period(
        2024,
        revenue=Decimal("800"),
        ebit=Decimal("160"),
        da=Decimal("40"),
        pretax_income=Decimal("150"),
        tax_expense=Decimal("30"),
        net_income=Decimal("120"),
        short_term_debt=Decimal("20"),
        long_term_debt=Decimal("140"),  # financial_debt = 160
        stockholders_equity=Decimal("500"),
        cash_and_equivalents=Decimal("50"),
        short_term_investments=Decimal("10"),  # total_cash = 60 -> IC_excl = 600
    )
    p2 = _make_period(2025)  # financial_debt = 200, equity = 600, total_cash = 100 -> IC_excl = 700
    report = _make_report([p2, p1])  # intentionally unsorted to verify chronological sorting

    res = compute_historical_metrics(report, include_leases=False)
    assert [p.fiscal_year for p in res.periods] == [2024, 2025]

    m1, m2 = res.periods
    # 2024: first year uses ending IC fallback = 600
    assert m1.roic_basis == "ending_fallback"
    assert m1.average_invested_capital == Decimal("600")
    assert m1.nopat == Decimal("160") * Decimal("0.8")
    assert m1.roic == Decimal("128") / Decimal("600")

    # 2025: margins, ETR, NOPAT
    assert m2.ebitda == Decimal("250")
    assert m2.ebit_margin == Decimal("0.2")
    assert m2.ebitda_margin == Decimal("0.25")
    assert m2.net_margin == Decimal("0.152")
    assert m2.raw_effective_tax_rate == Decimal("0.2")
    assert m2.nopat_tax_rate == Decimal("0.2")
    assert m2.nopat_tax_rate_source == "effective"
    assert m2.nopat == Decimal("160")

    # Average IC = (600 + 700) / 2 = 650
    assert m2.invested_capital_excl_leases == Decimal("700")
    assert m2.invested_capital_incl_leases == Decimal("750")
    assert m2.invested_capital == Decimal("700")
    assert m2.average_invested_capital == Decimal("650")
    assert m2.roic_basis == "average"
    assert m2.roic == Decimal("160") / Decimal("650")

    # Cash quality & capital intensity
    assert m2.fcf_simple == Decimal("150")
    assert m2.cash_conversion == Decimal("150") / Decimal("152")
    assert m2.capex_to_revenue == Decimal("0.06")
    assert m2.da_to_revenue == Decimal("0.05")
    # Operating CA = 300 - 100 = 200; Operating CL = 150 - 20 - 10 = 120; NWC = 80
    assert m2.non_cash_nwc == Decimal("80")
    assert m2.non_cash_nwc_to_revenue == Decimal("0.08")

    # Leverage & coverage
    assert m2.net_debt_excl_leases == Decimal("100")  # 200 - 100
    assert m2.net_debt_incl_leases == Decimal("150")  # 250 - 100
    assert m2.net_debt_to_ebitda_excl_leases == Decimal("100") / Decimal("250")
    assert m2.net_debt_to_ebitda_incl_leases == Decimal("150") / Decimal("250")
    assert m2.interest_coverage == Decimal("20")


def test_include_leases_adds_asc842_note_and_updates_roic():
    p = _make_period(2025)
    res = compute_historical_metrics(_make_report([p]), include_leases=True)
    m = res.latest_period
    assert m.invested_capital == Decimal("750")
    assert m.roic == Decimal("160") / Decimal("750")
    assert any("ASC 842" in note for note in m.notes)


def test_effective_tax_rate_clamping_and_loss_fallback():
    p_neg_etr = _make_period(2023, pretax_income=Decimal("100"), tax_expense=Decimal("-20"))
    p_high_etr = _make_period(2024, pretax_income=Decimal("100"), tax_expense=Decimal("80"))
    p_loss = _make_period(
        2025,
        ebit=Decimal("-50"),
        pretax_income=Decimal("-60"),
        tax_expense=Decimal("-10"),
        net_income=Decimal("-50"),
    )
    res = compute_historical_metrics(_make_report([p_neg_etr, p_high_etr, p_loss]))

    m_neg, m_high, m_loss = res.periods
    assert m_neg.raw_effective_tax_rate == Decimal("-0.2")
    assert m_neg.nopat_tax_rate == Decimal("0")
    assert m_neg.nopat_tax_rate_source == "clamped"
    assert m_neg.nopat == Decimal("200")

    assert m_high.raw_effective_tax_rate == Decimal("0.8")
    assert m_high.nopat_tax_rate == Decimal("0.50")
    assert m_high.nopat_tax_rate_source == "clamped"
    assert m_high.nopat == Decimal("100")

    assert m_loss.raw_effective_tax_rate is None
    assert m_loss.nopat_tax_rate == Decimal("0.21")
    assert m_loss.nopat_tax_rate_source == "fallback_loss_or_missing"
    assert m_loss.nopat == Decimal("-50") * Decimal("0.79")
    assert m_loss.cash_conversion is None  # net_income <= 0


def test_negative_invested_capital_negative_ebitda_and_net_cash_edge_cases():
    # Negative IC (large cash + negative equity), negative EBITDA, zero interest
    p_neg_ic = _make_period(
        2024,
        ebit=Decimal("-100"),
        da=Decimal("20"),  # EBITDA = -80 <= 0
        interest_expense=Decimal("0"),
        cash_and_equivalents=Decimal("500"),
        short_term_investments=Decimal("100"),
        short_term_debt=Decimal("0"),
        long_term_debt=Decimal("50"),
        stockholders_equity=Decimal("100"),  # IC_excl = 50 + 100 - 600 = -450
    )
    # Net cash position with positive EBITDA -> negative net_debt_to_ebitda MUST NOT be None
    p_net_cash = _make_period(
        2025,
        ebit=Decimal("200"),
        da=Decimal("50"),  # EBITDA = 250 > 0
        interest_expense=Decimal("0"),
        cash_and_equivalents=Decimal("300"),
        short_term_investments=Decimal("100"),  # total_cash = 400
        short_term_debt=Decimal("10"),
        long_term_debt=Decimal("90"),  # financial_debt = 100 -> net_debt_excl = -300
        stockholders_equity=Decimal("600"),
    )
    res = compute_historical_metrics(_make_report([p_neg_ic, p_net_cash]))
    m_neg_ic, m_net_cash = res.periods

    assert m_neg_ic.invested_capital < Decimal("0")
    assert m_neg_ic.roic is None
    assert m_neg_ic.roic_basis == "not_meaningful"
    assert m_neg_ic.net_debt_to_ebitda_excl_leases is None
    assert m_neg_ic.net_debt_to_ebitda_incl_leases is None
    assert m_neg_ic.interest_coverage is None

    assert m_net_cash.net_debt_excl_leases == Decimal("-300")
    assert m_net_cash.net_debt_to_ebitda_excl_leases == Decimal("-300") / Decimal("250")
    assert m_net_cash.net_debt_to_ebitda_incl_leases == Decimal("-250") / Decimal("250")
    assert m_net_cash.interest_coverage is None


def test_empty_report_raises_domain_error():
    empty = _make_report([])
    with pytest.raises(DcfValuationError):
        compute_historical_metrics(empty)


@pytest.mark.parametrize("fixture_name", ["aapl_fixture", "msft_fixture", "amzn_fixture"])
def test_historical_metrics_on_offline_sec_fixtures(request, fixture_name):
    raw, facts = request.getfixturevalue(fixture_name)
    report = normalize_entity_facts(
        facts=facts,
        ticker=raw["ticker"],
        sic=raw["sic"],
        fiscal_year_end=raw["fiscalYearEnd"],
        years=5,
        strict=True,
    )
    metrics = compute_historical_metrics(report, include_leases=True)
    assert len(metrics.periods) == 5
    latest = metrics.latest_period
    assert latest.ebit_margin is not None and latest.ebit_margin > Decimal("0")
    assert latest.ebitda_margin is not None and latest.ebitda_margin > latest.ebit_margin
    assert latest.nopat > Decimal("0")
    assert latest.roic is not None and latest.roic > Decimal("0")
    assert latest.sbc > Decimal("0")
    assert latest.fcf_ex_sbc == latest.fcf_simple - latest.sbc
    assert latest.sbc_to_revenue is not None and latest.sbc_to_revenue > Decimal("0")
    assert latest.sbc_to_cfo is not None and latest.sbc_to_cfo > Decimal("0")


def test_sbc_ratios_and_negative_cfo_edge_case():
    p_ok = _make_period(2024, revenue=Decimal("1000"), cfo=Decimal("200"), capex=Decimal("50"), sbc=Decimal("40"))
    p_neg_cfo = _make_period(2025, revenue=Decimal("1000"), cfo=Decimal("-20"), capex=Decimal("50"), sbc=Decimal("40"))
    res = compute_historical_metrics(_make_report([p_ok, p_neg_cfo]))

    m_ok, m_neg = res.periods
    assert m_ok.fcf_ex_sbc == Decimal("110")
    assert m_ok.sbc_to_revenue == Decimal("0.04")
    assert m_ok.sbc_to_cfo == Decimal("0.2")

    assert m_neg.fcf_ex_sbc == Decimal("-110")
    assert m_neg.sbc_to_revenue == Decimal("0.04")
    assert m_neg.sbc_to_cfo is None
