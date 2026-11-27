"""Offline unit tests for Module 4 UI helpers (`sec_dcf.ui`)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from sec_dcf import (
    DcfValuationError,
    ExtractedRiskItem,
    GeminiApiError,
    GeminiQualitativeAnalyzer,
    QualitativeExtractionPayload,
    WaccInputs,
    build_sensitivity_matrix,
    calculate_wacc,
    compute_historical_metrics,
    run_dcf_valuation,
    suggest_dcf_assumptions,
)
from sec_dcf.ui import (
    AVAILABLE_GEMINI_MODELS,
    DEMO_MARKET_DEFAULTS,
    DEMO_TICKERS,
    FALLBACK_GEMINI_MODEL,
    FIVE_YEAR_FIELD_SPECS,
    LEGAL_DISCLAIMER_PL,
    assumptions_to_5y_dataframe,
    build_audit_trail_dataframe,
    build_dcf_projections_dataframe,
    build_ev_bridge_rows,
    build_historical_metrics_dataframe,
    build_multiples_comparison_dataframe,
    build_sensitivity_dataframe,
    build_sensitivity_grid_params,
    build_validation_summary,
    create_cash_conversion_capex_chart,
    create_ev_bridge_waterfall_chart,
    create_revenue_ebit_fcf_chart,
    create_roic_and_margins_chart,
    create_sensitivity_heatmap,
    create_valuation_football_field_chart,
    dataframe_to_dcf_assumptions,
    is_503_unavailable_error,
    load_demo_financials,
    load_demo_qualitative_audit,
    resolve_fixtures_dir,
    run_qualitative_audit_with_fallback,
)


@pytest.fixture(autouse=True)
def block_all_network_calls(monkeypatch):
    """Guarantee zero outbound network calls across all UI helper tests."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Outbound network call attempted during offline UI helper test!")

    monkeypatch.setattr("edgar.Company", _forbid)
    monkeypatch.setattr("sec_dcf.ingestion.edgar_client.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.extractor.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.genai.Client", _forbid)
    monkeypatch.setattr("urllib.request.urlopen", _forbid)
    monkeypatch.setattr("socket.create_connection", _forbid)


def test_resolve_fixtures_dir_and_demo_loading(tmp_path: Path):
    fixtures_dir = resolve_fixtures_dir()
    assert fixtures_dir.is_dir()
    assert (fixtures_dir / "aapl_companyfacts.json").is_file()

    # Fallback to CWD works even if explicit path check isn't passed
    for ticker in DEMO_TICKERS:
        report = load_demo_financials(ticker)
        assert report.ticker == ticker
        assert report.is_valid is True
        assert len(report.periods) == 5

    with pytest.raises(ValueError, match="Unsupported demo ticker"):
        load_demo_financials("UNKNOWN")

    with pytest.raises(FileNotFoundError):
        resolve_fixtures_dir(tmp_path / "does_not_exist")


def test_load_demo_qualitative_audit_has_verified_and_unverified_badges():
    doc, report = load_demo_qualitative_audit()
    assert doc.ticker == "ACME"
    assert report.total_claims == 4
    assert report.verified_claims == 3
    assert report.verified_quote_ratio == Decimal("0.7500")
    assert report.compliance_flags == []
    assert any(item.quote_verified is True for item in report.risks)
    assert any(item.quote_verified is False for item in report.risks)


def test_presenters_and_5y_dataframe_roundtrip_exact_decimal():
    report = load_demo_financials("MSFT")
    summary = build_validation_summary(report)
    assert summary["is_valid"] is True
    assert summary["periods_count"] == 5

    latest_fy = max(p.fiscal_year for p in report.periods)
    audit_df = build_audit_trail_dataframe(report, latest_fy)
    assert not audit_df.empty
    assert "Tag XBRL / Koncept" in audit_df.columns

    metrics = compute_historical_metrics(report, include_leases=True)
    metrics_df = build_historical_metrics_dataframe(metrics)
    assert len(metrics_df) == 5
    assert "ROIC (%)" in metrics_df.columns
    assert "True FCF ex-SBC ($ mld)" in metrics_df.columns
    assert "SBC / Przychody (%)" in metrics_df.columns

    base_assumptions = suggest_dcf_assumptions(report, include_leases=True)
    df_5y = assumptions_to_5y_dataframe(base_assumptions, latest_fy)
    assert len(df_5y) == 5

    # Customize Year 1..5 revenue growth with non-constant float values
    growth_col = FIVE_YEAR_FIELD_SPECS[0][1]
    df_5y.loc[0, growth_col] = 15.30
    df_5y.loc[1, growth_col] = 12.15

    parsed = dataframe_to_dcf_assumptions(
        df_5y,
        terminal_growth_rate=Decimal("0.025"),
        discounting_convention="mid_year",
        include_leases=True,
        exit_ebitda_multiple=Decimal("18.0"),
    )
    assert parsed.revenue_growth[0] == Decimal("0.153")
    assert parsed.revenue_growth[1] == Decimal("0.1215")
    assert parsed.exit_ebitda_multiple == Decimal("18.0")

    # Empty / NaN cell triggers clean DcfValuationError
    bad_df = df_5y.copy()
    bad_df.loc[2, growth_col] = float("nan")
    with pytest.raises(DcfValuationError, match="Pusta lub nieprawidłowa wartość"):
        dataframe_to_dcf_assumptions(
            bad_df,
            terminal_growth_rate=Decimal("0.025"),
            discounting_convention="mid_year",
            include_leases=True,
        )


def test_charts_and_resilience_when_all_sensitivity_cells_invalid_or_equity_floored():
    report = load_demo_financials("AAPL")
    demo_cfg = DEMO_MARKET_DEFAULTS["AAPL"]
    wacc_res = calculate_wacc(
        report,
        WaccInputs(
            risk_free_rate=demo_cfg.risk_free_rate,
            beta=demo_cfg.beta,
            equity_risk_premium=demo_cfg.equity_risk_premium,
            share_price=demo_cfg.share_price,
            include_leases=True,
        ),
    )
    assumptions = suggest_dcf_assumptions(
        report,
        include_leases=True,
        discounting_convention="mid_year",
    ).model_copy(update={"exit_ebitda_multiple": Decimal("20.0")})
    dcf_res = run_dcf_valuation(report, assumptions, wacc_res)

    wacc_grid, g_grid = build_sensitivity_grid_params(wacc_res.wacc, assumptions.terminal_growth_rate)
    matrix = build_sensitivity_matrix(report, assumptions, wacc_grid, g_grid, base_wacc=wacc_res)

    metrics = compute_historical_metrics(report, include_leases=True)
    fig_rev = create_revenue_ebit_fcf_chart(metrics)
    fig_roic = create_roic_and_margins_chart(metrics)
    fig_cash = create_cash_conversion_capex_chart(metrics)
    fig_wf = create_ev_bridge_waterfall_chart(dcf_res)
    fig_heat = create_sensitivity_heatmap(matrix)
    fig_ff = create_valuation_football_field_chart(dcf_res, matrix)

    assert len(fig_rev.data) == 3
    assert len(fig_roic.data) == 4
    assert len(fig_cash.data) == 3
    assert len(fig_wf.data) == 1
    assert len(fig_heat.data) == 1
    assert len(fig_ff.data) >= 2

    # Ensure zero buy/sell or target price wording in chart titles and legal disclaimer
    for title_text in (fig_ff.layout.title.text, LEGAL_DISCLAIMER_PL):
        lowered = title_text.lower()
        assert "kup" not in lowered or "brak rekomendacji kup/sprzedaj" in lowered
        assert "target price" not in lowered
        assert "buy/sell" not in lowered

    assert len(build_dcf_projections_dataframe(dcf_res)) == 5
    assert len(build_ev_bridge_rows(dcf_res)) == 7
    assert build_sensitivity_dataframe(matrix).shape == (len(wacc_grid), len(g_grid))

    from sec_dcf import compute_market_multiples

    multiples = compute_market_multiples(
        report,
        include_leases=True,
        share_price=demo_cfg.share_price,
        dcf_result=dcf_res,
    )
    mult_df = build_multiples_comparison_dataframe(multiples)
    assert len(mult_df) == 6
    assert "True FCF Yield (ex-SBC)" in set(mult_df["Mnożnik / Stopa zwrotu"])

    # Edge case 1: all sensitivity cells invalid (WACC <= g everywhere)
    invalid_matrix = build_sensitivity_matrix(
        report,
        assumptions,
        wacc_values=[Decimal("0.02"), Decimal("0.03")],
        terminal_growth_values=[Decimal("0.04"), Decimal("0.05")],
        base_wacc=wacc_res,
    )
    assert invalid_matrix.min_implied_value_per_share is None
    fig_invalid_heat = create_sensitivity_heatmap(invalid_matrix)
    fig_invalid_ff = create_valuation_football_field_chart(dcf_res, invalid_matrix)
    assert len(fig_invalid_heat.layout.annotations) >= 1
    assert len(fig_invalid_ff.layout.annotations) >= 1

    # Edge case 2: floored equity waterfall annotation preserves raw arithmetic bar
    floored_assumptions = assumptions.model_copy(update={"ebit_margin": (Decimal("-0.20"),) * 5})
    floored_dcf = run_dcf_valuation(report, floored_assumptions, wacc_res)
    assert floored_dcf.bridge.equity_floored_at_zero is True
    fig_floored_wf = create_ev_bridge_waterfall_chart(floored_dcf)
    assert len(fig_floored_wf.layout.annotations) == 1
    assert fig_floored_wf.data[0].y[-1] < 0


def test_qualitative_503_fallback_and_non_503_propagation():
    doc, _ = load_demo_qualitative_audit()
    assert FALLBACK_GEMINI_MODEL in AVAILABLE_GEMINI_MODELS

    valid_payload = QualitativeExtractionPayload(
        executive_summary="Summary grounded in 10-K.",
        risks=[
            ExtractedRiskItem(
                title="Foundry concentration",
                category="supply_chain",
                materiality="high",
                summary="Taiwan foundry reliance.",
                section="Item 1A",
                verbatim_quote="We rely on a limited number of third-party semiconductor foundries located in Taiwan",
            )
        ],
    )

    class Failing503Models:
        def generate_content(self, **kwargs):
            raise RuntimeError("503 UNAVAILABLE: model overloaded")

    class SuccessModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(parsed=valid_payload, text=valid_payload.model_dump_json())

    primary_503 = GeminiQualitativeAnalyzer(
        model="gemini-4-hydrogen",
        client=SimpleNamespace(models=Failing503Models()),
    )
    fallback_ok = GeminiQualitativeAnalyzer(
        model=FALLBACK_GEMINI_MODEL,
        client=SimpleNamespace(models=SuccessModels()),
    )

    # 1. 503 triggers fallback when enabled
    outcome = run_qualitative_audit_with_fallback(
        document=doc,
        selected_model="gemini-4-hydrogen",
        enable_503_fallback=True,
        primary_analyzer=primary_503,
        fallback_analyzer=fallback_ok,
    )
    assert outcome.fallback_triggered is True
    assert outcome.requested_model == "gemini-4-hydrogen"
    assert outcome.executed_model == FALLBACK_GEMINI_MODEL
    assert outcome.report.verified_claims == 1

    # 2. 503 propagates when fallback is disabled
    with pytest.raises(GeminiApiError, match="503"):
        run_qualitative_audit_with_fallback(
            document=doc,
            selected_model="gemini-4-hydrogen",
            enable_503_fallback=False,
            primary_analyzer=primary_503,
            fallback_analyzer=fallback_ok,
        )

    # 3. Non-503 error (e.g. 401/permission) never triggers fallback
    class Failing401Models:
        def generate_content(self, **kwargs):
            raise RuntimeError("401 PERMISSION_DENIED: invalid key")

    primary_401 = GeminiQualitativeAnalyzer(
        model="gemini-3.8-flash",
        client=SimpleNamespace(models=Failing401Models()),
    )
    assert is_503_unavailable_error(GeminiApiError("503 UNAVAILABLE")) is True
    assert is_503_unavailable_error(GeminiApiError("401 PERMISSION_DENIED")) is False

    with pytest.raises(GeminiApiError, match="401"):
        run_qualitative_audit_with_fallback(
            document=doc,
            selected_model="gemini-3.8-flash",
            enable_503_fallback=True,
            primary_analyzer=primary_401,
            fallback_analyzer=fallback_ok,
        )
