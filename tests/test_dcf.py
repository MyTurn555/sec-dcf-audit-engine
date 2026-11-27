"""Offline unit tests for 5-year FCFF/DCF valuation and 2D sensitivity matrix."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sec_dcf.analysis.dcf import (
    build_sensitivity_matrix,
    run_dcf_valuation,
    suggest_dcf_assumptions,
)
from sec_dcf.analysis.wacc import calculate_wacc
from sec_dcf.exceptions import DcfValuationError
from sec_dcf.ingestion.normalizer import normalize_entity_facts
from sec_dcf.models.analysis import DcfAssumptions, WaccInputs
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
        tax_expense=Decimal("38"),
        net_income=Decimal("152"),
        diluted_shares=Decimal("100"),
        cash_and_equivalents=Decimal("100"),
        short_term_investments=Decimal("50"),  # total_cash = 150
        current_assets=Decimal("350"),  # operating CA = 200
        total_assets=Decimal("1000"),
        current_liabilities=Decimal("130"),  # operating CL = 130 - 20 - 10 = 100 -> NWC_0 = 100
        short_term_debt=Decimal("20"),
        long_term_debt=Decimal("180"),  # financial_debt = 200
        current_lease_liabilities=Decimal("10"),
        noncurrent_lease_liabilities=Decimal("40"),  # leases = 50 -> total_debt = 250
        total_liabilities=Decimal("450"),
        stockholders_equity=Decimal("550"),
        noncontrolling_interest=Decimal("10"),
        equity_includes_nci=False,
        ending_shares=Decimal("98"),
        cfo=Decimal("210"),
        capex=Decimal("60"),
    )
    base.update(overrides)
    return AnnualFinancialPeriod(**base)


def _make_report(periods: list[AnnualFinancialPeriod] | None = None) -> CompanyFinancialsReport:
    if periods is None:
        periods = [_make_period(2024, revenue=Decimal("900")), _make_period(2025)]
    return CompanyFinancialsReport(
        ticker="TEST",
        cik="0000999999",
        company_name="Test Corp",
        periods=periods,
    )


def _flat_assumptions(**overrides) -> DcfAssumptions:
    base = dict(
        revenue_growth=Decimal("0.10"),
        ebit_margin=Decimal("0.20"),
        tax_rate=Decimal("0.20"),
        da_pct_revenue=Decimal("0.05"),
        capex_pct_revenue=Decimal("0.05"),
        nwc_pct_revenue=Decimal("0.10"),
        terminal_growth_rate=Decimal("0.02"),
        discounting_convention="end_of_year",
        include_leases=False,
    )
    base.update(overrides)
    return DcfAssumptions(**base)


def test_dcf_end_of_year_vs_mid_year_exact_math_and_bridge():
    report = _make_report()
    wacc = Decimal("0.10")
    assumptions_eoy = _flat_assumptions(discounting_convention="end_of_year")

    # Base: rev_0 = 1000, NWC_0 = 100 (10% of rev_0)
    # Year t (1..5): rev_t = 1000 * 1.1^t
    # EBIT = 0.20 * rev_t; NOPAT = 0.80 * 0.20 * rev_t = 0.16 * rev_t
    # D&A = CapEx = 0.05 * rev_t (cancel out)
    # NWC_t = 0.10 * rev_t -> delta_NWC_t = 0.10 * (rev_t - rev_{t-1}) = 0.10 * 0.10 * rev_{t-1}
    # Let's verify Year 1 explicitly: rev_1 = 1100, NOPAT = 176, delta_NWC = 110 - 100 = 10 -> FCFF_1 = 166
    res_eoy = run_dcf_valuation(report, assumptions_eoy, wacc)
    y1 = res_eoy.projections[0]
    assert res_eoy.initial_nwc == Decimal("100")
    assert res_eoy.initial_nwc_source == "historical_balance_sheet"
    assert y1.revenue == Decimal("1100.00")
    assert y1.nopat == Decimal("176.0000")
    assert y1.delta_nwc == Decimal("10.000")
    assert y1.fcff == Decimal("166.0000")
    assert y1.discount_period == Decimal("1")
    assert y1.pv_fcff == Decimal("166.0000") / Decimal("1.10")

    # Mid-year: FCFF_t discounted at (t - 0.5), TV_5 discounted at 5.0
    assumptions_mid = _flat_assumptions(discounting_convention="mid_year")
    res_mid = run_dcf_valuation(report, assumptions_mid, wacc)
    assert res_mid.projections[0].discount_period == Decimal("0.5")
    assert res_mid.terminal_value.tv_discount_period == Decimal("5")
    assert res_mid.pv_projected_fcff > res_eoy.pv_projected_fcff
    assert res_mid.terminal_value.pv_terminal_value == res_eoy.terminal_value.pv_terminal_value

    # EV -> Equity Value bridge (include_leases=False):
    # debt_deducted = 200, NCI = 10, total_cash = 150 -> net deduction = 60
    bridge = res_eoy.bridge
    assert bridge.debt_deducted == Decimal("200")
    assert bridge.lease_liabilities_included == Decimal("0")
    assert bridge.noncontrolling_interest_deducted == Decimal("10")
    assert bridge.total_cash_added == Decimal("150")
    assert bridge.raw_implied_equity_value == bridge.enterprise_value - Decimal("60")
    assert bridge.implied_value_per_share == bridge.implied_equity_value / Decimal("100")


def test_dcf_five_year_path_and_exit_multiple_crosscheck_and_nwc_fallback():
    p_no_nwc = _make_period(2025, current_assets=None, current_liabilities=None)
    report = _make_report([p_no_nwc])
    assumptions = DcfAssumptions(
        revenue_growth=[
            Decimal("0.12"),
            Decimal("0.10"),
            Decimal("0.08"),
            Decimal("0.06"),
            Decimal("0.04"),
        ],
        ebit_margin=Decimal("0.20"),
        tax_rate=Decimal("0.20"),
        da_pct_revenue=Decimal("0.05"),
        capex_pct_revenue=Decimal("0.05"),
        nwc_pct_revenue=Decimal("0.10"),
        terminal_growth_rate=Decimal("0.025"),
        exit_ebitda_multiple=Decimal("10"),
    )
    res = run_dcf_valuation(report, assumptions, Decimal("0.09"))
    assert res.initial_nwc_source == "model_pct_fallback"
    assert res.initial_nwc == Decimal("100.00")
    assert res.projections[0].revenue == Decimal("1120.00")
    assert res.projections[4].fiscal_year == 2030
    assert res.terminal_value.exit_multiple_crosscheck_tv == res.projections[4].ebitda * Decimal("10")
    assert res.terminal_value.exit_multiple_crosscheck_value_per_share is not None


def test_dcf_gordon_growth_singularity_raises_domain_error():
    report = _make_report()
    with pytest.raises(DcfValuationError, match="Gordon Growth requires WACC"):
        run_dcf_valuation(report, _flat_assumptions(terminal_growth_rate=Decimal("0.10")), Decimal("0.10"))

    with pytest.raises(DcfValuationError, match="Gordon Growth requires WACC"):
        run_dcf_valuation(report, _flat_assumptions(terminal_growth_rate=Decimal("0.12")), Decimal("0.10"))


def test_dcf_include_leases_consistency_with_wacc_result():
    report = _make_report()
    wacc_incl = calculate_wacc(
        report,
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.0"),
            equity_risk_premium=Decimal("0.05"),
            share_price=Decimal("20"),
            include_leases=True,
        ),
    )

    # Mismatch must fail loudly
    with pytest.raises(DcfValuationError, match="include_leases mismatch"):
        run_dcf_valuation(report, _flat_assumptions(include_leases=False), wacc_incl)

    # Matching include_leases=True succeeds and deducts lease liabilities in bridge
    res = run_dcf_valuation(report, _flat_assumptions(include_leases=True), wacc_incl)
    assert res.bridge.include_leases is True
    assert res.bridge.debt_deducted == Decimal("250")
    assert res.bridge.lease_liabilities_included == Decimal("50")


def test_dcf_negative_fcff5_warning_and_negative_equity_floor_to_zero():
    # High CapEx causes negative FCFF in Year 5 -> negative EV -> negative raw Equity Value floored to 0
    report = _make_report()
    assumptions = _flat_assumptions(
        ebit_margin=Decimal("0.02"),
        capex_pct_revenue=Decimal("0.20"),
    )
    res = run_dcf_valuation(report, assumptions, Decimal("0.10"))
    assert res.projections[-1].fcff < Decimal("0")
    assert any("non-market / negative Terminal Value" in note for note in res.notes)
    assert res.bridge.raw_implied_equity_value < Decimal("0")
    assert res.bridge.equity_floored_at_zero is True
    assert res.bridge.implied_equity_value == Decimal("0")
    assert res.bridge.implied_value_per_share == Decimal("0")


def test_dcf_missing_shares_raises_error():
    p_no_shares = _make_period(2025, diluted_shares=None, ending_shares=None)
    with pytest.raises(DcfValuationError, match="diluted_shares"):
        run_dcf_valuation(_make_report([p_no_shares]), _flat_assumptions(), Decimal("0.10"))


def test_sensitivity_matrix_range_monotonicity_and_all_invalid_edge_case():
    report = _make_report()
    assumptions = _flat_assumptions()

    matrix = build_sensitivity_matrix(
        report,
        assumptions,
        wacc_values=[Decimal("0.08"), Decimal("0.10"), Decimal("0.12")],
        terminal_growth_values=[Decimal("0.02"), Decimal("0.08")],
        base_wacc=Decimal("0.10"),
    )
    assert len(matrix.cells) == 3
    assert len(matrix.cells[0]) == 2
    # (wacc=0.08, g=0.08) is invalid
    assert matrix.cells[0][1].is_valid is False
    assert matrix.cells[0][1].implied_value_per_share is None
    # Higher WACC -> lower per-share value (at fixed g=0.02)
    assert (
        matrix.cells[0][0].implied_value_per_share
        > matrix.cells[1][0].implied_value_per_share
        > matrix.cells[2][0].implied_value_per_share
    )
    assert matrix.min_implied_value_per_share == matrix.cells[2][0].implied_value_per_share
    assert matrix.max_implied_value_per_share == matrix.cells[1][1].implied_value_per_share

    # Edge case: ALL cells invalid (every WACC <= g) -> min/max safely None
    all_invalid = build_sensitivity_matrix(
        report,
        assumptions,
        wacc_values=[Decimal("0.03"), Decimal("0.04")],
        terminal_growth_values=[Decimal("0.04"), Decimal("0.05")],
        base_wacc=Decimal("0.08"),
    )
    assert all(not cell.is_valid for row in all_invalid.cells for cell in row)
    assert all_invalid.min_implied_value_per_share is None
    assert all_invalid.max_implied_value_per_share is None


@pytest.mark.parametrize("fixture_name", ["aapl_fixture", "msft_fixture", "amzn_fixture"])
def test_end_to_end_offline_sec_fixture_wacc_dcf_and_sensitivity(request, fixture_name):
    raw, facts = request.getfixturevalue(fixture_name)
    report = normalize_entity_facts(
        facts=facts,
        ticker=raw["ticker"],
        sic=raw["sic"],
        fiscal_year_end=raw["fiscalYearEnd"],
        years=5,
        strict=True,
    )
    assumptions = suggest_dcf_assumptions(report, include_leases=True)
    wacc_res = calculate_wacc(
        report,
        WaccInputs(
            risk_free_rate=Decimal("0.0425"),
            beta=Decimal("1.10"),
            equity_risk_premium=Decimal("0.05"),
            share_price=Decimal("200"),
            include_leases=True,
        ),
    )
    dcf = run_dcf_valuation(report, assumptions, wacc_res)
    assert dcf.bridge.enterprise_value > Decimal("0")
    assert dcf.bridge.implied_value_per_share > Decimal("0")
    assert assumptions.add_back_sbc is False
    assert assumptions.sbc_pct_revenue[0] > Decimal("0")
    assert dcf.sbc_valuation_impact_ev > Decimal("0")
    assert any("do not constitute investment advice" in n for n in dcf.notes)

    matrix = build_sensitivity_matrix(
        report,
        assumptions,
        wacc_values=[wacc_res.wacc - Decimal("0.01"), wacc_res.wacc, wacc_res.wacc + Decimal("0.01")],
        terminal_growth_values=[Decimal("0.02"), Decimal("0.025"), Decimal("0.03")],
        base_wacc=wacc_res,
    )
    assert matrix.min_implied_value_per_share is not None
    assert matrix.max_implied_value_per_share is not None
    assert matrix.min_implied_value_per_share < matrix.max_implied_value_per_share


def test_dcf_add_back_sbc_toggle_preserves_economic_baseline_and_reports_impact():
    p1 = _make_period(2024, revenue=Decimal("900"), sbc=Decimal("27"))
    p2 = _make_period(2025, revenue=Decimal("1000"), sbc=Decimal("30"))
    report = _make_report([p1, p2])

    suggested = suggest_dcf_assumptions(report, include_leases=False)
    assert suggested.add_back_sbc is False
    assert suggested.sbc_pct_revenue[0] == Decimal("0.03")

    assump_econ = _flat_assumptions(sbc_pct_revenue=Decimal("0.03"), add_back_sbc=False)
    assump_cfo = _flat_assumptions(sbc_pct_revenue=Decimal("0.03"), add_back_sbc=True)

    res_econ = run_dcf_valuation(report, assump_econ, Decimal("0.10"))
    res_cfo = run_dcf_valuation(report, assump_cfo, Decimal("0.10"))

    # Economic baseline does NOT subtract SBC twice (GAAP EBIT already deducted it)
    assert res_econ.projections[0].sbc == Decimal("33.00")
    assert res_econ.projections[0].fcff == Decimal("166.0000")
    # CFO add-back adds back 100% of SBC_1 = 33.00
    assert res_cfo.projections[0].fcff == Decimal("199.0000")
    assert res_cfo.bridge.enterprise_value - res_econ.bridge.enterprise_value == res_econ.sbc_valuation_impact_ev
    assert res_econ.sbc_valuation_impact_ev == res_cfo.sbc_valuation_impact_ev > Decimal("0")
    assert any("add_back_sbc=True" in n for n in res_cfo.notes)
