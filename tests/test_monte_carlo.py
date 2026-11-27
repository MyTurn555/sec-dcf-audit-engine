"""Offline unit, edge-case, performance, and UI smoke tests for Monte Carlo DCF."""

from __future__ import annotations

import math
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from sec_dcf.analysis.dcf import run_dcf_valuation, suggest_dcf_assumptions
from sec_dcf.analysis.monte_carlo import run_monte_carlo_dcf
from sec_dcf.analysis.wacc import calculate_wacc
from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import (
    DcfAssumptions,
    MonteCarloConfig,
    WaccInputs,
)
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport
from sec_dcf.ui.charts import create_monte_carlo_histogram
from sec_dcf.ui.demo_loader import load_demo_financials

APP_PATH = Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py"


@pytest.fixture(autouse=True)
def block_all_network_calls(monkeypatch):
    """Ensure all Monte Carlo and Streamlit AppTest checks stay 100% offline."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Outbound network call attempted during offline Monte Carlo test!")

    monkeypatch.setattr("edgar.Company", _forbid)
    monkeypatch.setattr("sec_dcf.ingestion.edgar_client.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.extractor.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.genai.Client", _forbid)
    monkeypatch.setattr("urllib.request.urlopen", _forbid)
    monkeypatch.setattr("socket.create_connection", _forbid)


def _make_period(fiscal_year: int = 2025, **overrides) -> AnnualFinancialPeriod:
    base = dict(
        fiscal_year=fiscal_year,
        period_start=date(fiscal_year, 1, 1),
        period_end=date(fiscal_year, 12, 31),
        revenue=Decimal("1000"),
        ebit=Decimal("200"),
        da=Decimal("50"),
        sbc=Decimal("20"),
        interest_expense=Decimal("10"),
        pretax_income=Decimal("190"),
        tax_expense=Decimal("38"),
        net_income=Decimal("152"),
        diluted_shares=Decimal("100"),
        cash_and_equivalents=Decimal("100"),
        short_term_investments=Decimal("50"),
        current_assets=Decimal("350"),
        total_assets=Decimal("1000"),
        current_liabilities=Decimal("130"),
        short_term_debt=Decimal("20"),
        long_term_debt=Decimal("180"),
        current_lease_liabilities=Decimal("10"),
        noncurrent_lease_liabilities=Decimal("40"),
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


def _make_report() -> CompanyFinancialsReport:
    return CompanyFinancialsReport(
        ticker="TEST",
        cik="0000999999",
        company_name="Test Corp",
        periods=[_make_period(2024, revenue=Decimal("900")), _make_period(2025)],
    )


def _base_assumptions(**overrides) -> DcfAssumptions:
    base = dict(
        revenue_growth=(
            Decimal("0.12"),
            Decimal("0.10"),
            Decimal("0.08"),
            Decimal("0.07"),
            Decimal("0.06"),
        ),
        ebit_margin=(
            Decimal("0.20"),
            Decimal("0.21"),
            Decimal("0.22"),
            Decimal("0.22"),
            Decimal("0.23"),
        ),
        tax_rate=Decimal("0.20"),
        da_pct_revenue=Decimal("0.05"),
        capex_pct_revenue=Decimal("0.05"),
        nwc_pct_revenue=Decimal("0.10"),
        sbc_pct_revenue=Decimal("0.02"),
        add_back_sbc=False,
        terminal_growth_rate=Decimal("0.025"),
        discounting_convention="mid_year",
        include_leases=False,
    )
    base.update(overrides)
    return DcfAssumptions(**base)


@pytest.mark.parametrize("distribution", ["normal", "triangular"])
def test_monte_carlo_seed_reproducibility(distribution):
    report = _make_report()
    assumptions = _base_assumptions()
    cfg_a = MonteCarloConfig(
        n_simulations=1_000,
        seed=123,
        distribution=distribution,
        growth_margin_correlation=Decimal("0.40"),
    )
    cfg_b = MonteCarloConfig(
        n_simulations=1_000,
        seed=456,
        distribution=distribution,
        growth_margin_correlation=Decimal("0.40"),
    )

    res_1 = run_monte_carlo_dcf(report, assumptions, Decimal("0.09"), cfg_a)
    res_2 = run_monte_carlo_dcf(report, assumptions, Decimal("0.09"), cfg_a)
    res_diff = run_monte_carlo_dcf(report, assumptions, Decimal("0.09"), cfg_b)

    assert res_1 == res_2
    assert res_1.p50_value_per_share != res_diff.p50_value_per_share
    assert all(math.isfinite(v) for v in res_1.simulated_values_per_share)


@pytest.mark.parametrize("convention", ["mid_year", "end_of_year"])
@pytest.mark.parametrize("distribution", ["normal", "triangular"])
def test_monte_carlo_zero_std_matches_deterministic_dcf_even_outside_default_bounds(
    convention, distribution
):
    report = _make_report()
    # Intentionally set ebit_margin=0.90 (above default clamp bound 0.80) to prove std==0 skips clipping
    assumptions = _base_assumptions(
        ebit_margin=Decimal("0.90"),
        discounting_convention=convention,
    )
    wacc = Decimal("0.09")
    det = run_dcf_valuation(report, assumptions, wacc)

    cfg = MonteCarloConfig(
        n_simulations=500,
        seed=42,
        distribution=distribution,
        wacc_std=Decimal("0"),
        revenue_growth_std=Decimal("0"),
        ebit_margin_std=Decimal("0"),
        terminal_growth_std=Decimal("0"),
    )
    mc = run_monte_carlo_dcf(report, assumptions, wacc, cfg)

    assert mc.valid_simulations == 500
    assert mc.discarded_simulations == 0
    expected = det.bridge.implied_value_per_share
    assert mc.std_value_per_share == Decimal("0.0000")
    assert abs(mc.p10_value_per_share - expected) <= Decimal("0.0001")
    assert abs(mc.p50_value_per_share - expected) <= Decimal("0.0001")
    assert abs(mc.p90_value_per_share - expected) <= Decimal("0.0001")
    assert abs(mc.mean_value_per_share - expected) <= Decimal("0.0001")


@pytest.mark.parametrize("include_leases", [False, True])
@pytest.mark.parametrize("add_back_sbc", [False, True])
def test_monte_carlo_respects_include_leases_and_add_back_sbc(include_leases, add_back_sbc):
    report = _make_report()
    wacc_res = calculate_wacc(
        report,
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.1"),
            equity_risk_premium=Decimal("0.045"),
            share_price=Decimal("25"),
            include_leases=include_leases,
        ),
    )
    assumptions = _base_assumptions(
        include_leases=include_leases,
        add_back_sbc=add_back_sbc,
    )
    det = run_dcf_valuation(report, assumptions, wacc_res)

    zero_cfg = MonteCarloConfig(
        n_simulations=300,
        wacc_std=Decimal("0"),
        revenue_growth_std=Decimal("0"),
        ebit_margin_std=Decimal("0"),
        terminal_growth_std=Decimal("0"),
    )
    mc_zero = run_monte_carlo_dcf(report, assumptions, wacc_res, zero_cfg)
    assert abs(mc_zero.p50_value_per_share - det.bridge.implied_value_per_share) <= Decimal("0.0001")
    assert mc_zero.include_leases is include_leases
    assert mc_zero.add_back_sbc is add_back_sbc

    # Mismatched include_leases between WaccResult and DcfAssumptions must raise DcfValuationError
    bad_assumptions = _base_assumptions(include_leases=not include_leases)
    with pytest.raises(DcfValuationError, match="include_leases mismatch"):
        run_monte_carlo_dcf(report, bad_assumptions, wacc_res)


def test_monte_carlo_min_wacc_g_spread_and_discarded_counting():
    report = _make_report()
    assumptions = _base_assumptions(terminal_growth_rate=Decimal("0.035"))
    base_wacc = Decimal("0.045")

    cfg_strict = MonteCarloConfig(
        n_simulations=2_000,
        seed=7,
        wacc_std=Decimal("0.015"),
        terminal_growth_std=Decimal("0.010"),
        min_wacc_g_spread=Decimal("0.005"),
    )
    cfg_zero_guard = cfg_strict.model_copy(update={"min_wacc_g_spread": Decimal("0")})

    res_strict = run_monte_carlo_dcf(report, assumptions, base_wacc, cfg_strict)
    res_zero = run_monte_carlo_dcf(report, assumptions, base_wacc, cfg_zero_guard)

    assert res_strict.valid_simulations + res_strict.discarded_simulations == 2_000
    assert res_zero.valid_simulations + res_zero.discarded_simulations == 2_000
    assert res_strict.discarded_simulations > res_zero.discarded_simulations > 0
    assert res_strict.max_value_per_share <= res_zero.max_value_per_share

    # Edge case: 100% discarded when base WACC <= terminal_growth_rate and std=0
    all_discarded_cfg = MonteCarloConfig(
        n_simulations=200,
        seed=7,
        wacc_std=Decimal("0"),
        terminal_growth_std=Decimal("0"),
        min_wacc_g_spread=Decimal("0.005"),
    )
    res_none = run_monte_carlo_dcf(
        report,
        assumptions,
        Decimal("0.035"),  # spread == 0 < 0.005
        all_discarded_cfg,
    )
    assert res_none.valid_simulations == 0
    assert res_none.discarded_simulations == 200
    assert res_none.p50_value_per_share is None
    assert res_none.base_implied_value_per_share is None
    assert any("discarded" in note.lower() for note in res_none.notes)


def test_monte_carlo_equity_floor_and_reference_price_probability():
    # High debt + negative margins -> many scenarios floored at $0.00
    levered_period = _make_period(
        2025,
        short_term_debt=Decimal("200"),
        long_term_debt=Decimal("1800"),
        cash_and_equivalents=Decimal("50"),
        short_term_investments=Decimal("0"),
    )
    report = CompanyFinancialsReport(
        ticker="LEV",
        cik="0000999998",
        company_name="Levered Corp",
        periods=[levered_period],
    )
    assumptions = _base_assumptions(ebit_margin=Decimal("0.05"))
    cfg = MonteCarloConfig(
        n_simulations=2_000,
        seed=99,
        ebit_margin_std=Decimal("0.08"),
        reference_share_price=Decimal("10.00"),
    )
    res = run_monte_carlo_dcf(report, assumptions, Decimal("0.10"), cfg)

    assert res.floored_at_zero_count > 0
    assert res.min_value_per_share == Decimal("0.0000")
    assert res.prob_above_reference_price is not None
    assert Decimal("0") <= res.prob_above_reference_price <= Decimal("1")
    assert any("not a buy/sell recommendation" in n.lower() for n in res.notes)

    # Without reference price, prob_above_reference_price must remain None
    res_no_ref = run_monte_carlo_dcf(
        report,
        assumptions,
        Decimal("0.10"),
        cfg.model_copy(update={"reference_share_price": None}),
    )
    assert res_no_ref.prob_above_reference_price is None


def test_monte_carlo_correlation_increases_dispersion_when_positive():
    report = _make_report()
    assumptions = _base_assumptions()

    cfg_neg = MonteCarloConfig(
        n_simulations=3_000,
        seed=11,
        wacc_std=Decimal("0"),
        terminal_growth_std=Decimal("0"),
        revenue_growth_std=Decimal("0.04"),
        ebit_margin_std=Decimal("0.03"),
        growth_margin_correlation=Decimal("-0.75"),
    )
    cfg_pos = cfg_neg.model_copy(update={"growth_margin_correlation": Decimal("0.75")})

    res_neg = run_monte_carlo_dcf(report, assumptions, Decimal("0.09"), cfg_neg)
    res_pos = run_monte_carlo_dcf(report, assumptions, Decimal("0.09"), cfg_pos)

    assert res_pos.std_value_per_share > res_neg.std_value_per_share


def test_monte_carlo_10k_performance_under_one_second():
    report = load_demo_financials("AAPL")
    assumptions = suggest_dcf_assumptions(report, include_leases=True)
    cfg = MonteCarloConfig(
        n_simulations=10_000,
        seed=42,
        distribution="triangular",
        growth_margin_correlation=Decimal("0.30"),
        reference_share_price=Decimal("225.00"),
    )

    t0 = time.perf_counter()
    res = run_monte_carlo_dcf(report, assumptions, Decimal("0.085"), cfg)
    elapsed = time.perf_counter() - t0

    assert res.total_simulations == 10_000
    assert res.valid_simulations > 9_500
    assert elapsed < 1.0, f"10,000 Monte Carlo scenarios took {elapsed:.3f}s (>= 1.0s)"


def test_monte_carlo_histogram_handles_right_tail_clipping_and_empty_valid():
    report = _make_report()
    assumptions = _base_assumptions(terminal_growth_rate=Decimal("0.03"))
    # Narrow min_wacc_g_spread=0.0001 creates extreme right-tail outliers when WACC approaches g
    cfg = MonteCarloConfig(
        n_simulations=1_000,
        seed=5,
        wacc_std=Decimal("0.025"),
        terminal_growth_std=Decimal("0.015"),
        min_wacc_g_spread=Decimal("0.0001"),
        reference_share_price=Decimal("20.00"),
    )
    res = run_monte_carlo_dcf(report, assumptions, Decimal("0.055"), cfg)
    fig = create_monte_carlo_histogram(res)
    assert len(fig.data) == 1
    assert fig.data[0].type == "histogram"

    # Empty valid scenarios case
    empty_res = run_monte_carlo_dcf(
        report,
        assumptions,
        Decimal("0.03"),
        MonteCarloConfig(n_simulations=100, wacc_std=Decimal("0"), terminal_growth_std=Decimal("0")),
    )
    empty_fig = create_monte_carlo_histogram(empty_res)
    assert len(empty_fig.data) == 0
    assert len(empty_fig.layout.annotations) == 1


def test_streamlit_app_monte_carlo_ui_smoke():
    at = AppTest.from_file(str(APP_PATH), default_timeout=25)
    at.run()
    assert not at.exception

    # Configure smaller scenario count for fast AppTest smoke and click run button
    at.number_input(key="mc_n_simulations").set_value(1_000).run()
    at.button(key="run_monte_carlo_btn").click().run()

    assert not at.exception
    assert "mc_simulation_entry" in at.session_state
    entry = at.session_state["mc_simulation_entry"]
    assert entry["result"].total_simulations == 1_000
    assert entry["result"].valid_simulations > 0

    # Switching ticker clears stale Monte Carlo state cleanly
    at.sidebar.selectbox(key="demo_ticker").select("AMZN").run()
    assert not at.exception
    assert "mc_simulation_entry" not in at.session_state

    # Verify Tab 3 handles invalid Tab 2 state (WACC <= g) gracefully without NoneType errors
    at.number_input(key="dcf_terminal_g_pct").set_value(8.0).run()
    at.number_input(key="wacc_rf_pct").set_value(0.5).run()
    at.number_input(key="wacc_erp_pct").set_value(1.0).run()
    assert not at.exception
    assert len(at.warning) >= 2
