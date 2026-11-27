"""UI helper layer for the SEC DCF Audit Engine Streamlit application."""

from __future__ import annotations

from sec_dcf.ui.charts import (
    create_cash_conversion_capex_chart,
    create_ev_bridge_waterfall_chart,
    create_monte_carlo_histogram,
    create_revenue_ebit_fcf_chart,
    create_roic_and_margins_chart,
    create_sensitivity_heatmap,
    create_valuation_football_field_chart,
)
from sec_dcf.ui.demo_loader import (
    DEMO_MARKET_DEFAULTS,
    DEMO_TICKERS,
    DemoMarketDefaults,
    load_demo_financials,
    load_demo_qualitative_audit,
    resolve_fixtures_dir,
)
from sec_dcf.ui.presenters import (
    FIVE_YEAR_FIELD_SPECS,
    FRED_ATTRIBUTION_TEXT,
    LEGAL_DISCLAIMER_EN,
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
    dataframe_to_dcf_assumptions,
)
from sec_dcf.ui.qualitative_runner import (
    AVAILABLE_GEMINI_MODELS,
    FALLBACK_GEMINI_MODEL,
    QualitativeRunOutcome,
    is_503_unavailable_error,
    run_qualitative_audit_with_fallback,
)

__all__ = [
    "AVAILABLE_GEMINI_MODELS",
    "DEMO_MARKET_DEFAULTS",
    "DEMO_TICKERS",
    "DemoMarketDefaults",
    "FALLBACK_GEMINI_MODEL",
    "FIVE_YEAR_FIELD_SPECS",
    "FRED_ATTRIBUTION_TEXT",
    "LEGAL_DISCLAIMER_EN",
    "LEGAL_DISCLAIMER_PL",
    "QualitativeRunOutcome",
    "assumptions_to_5y_dataframe",
    "build_audit_trail_dataframe",
    "build_dcf_projections_dataframe",
    "build_ev_bridge_rows",
    "build_historical_metrics_dataframe",
    "build_multiples_comparison_dataframe",
    "build_sensitivity_dataframe",
    "build_sensitivity_grid_params",
    "build_validation_summary",
    "create_cash_conversion_capex_chart",
    "create_ev_bridge_waterfall_chart",
    "create_monte_carlo_histogram",
    "create_revenue_ebit_fcf_chart",
    "create_roic_and_margins_chart",
    "create_sensitivity_heatmap",
    "create_valuation_football_field_chart",
    "dataframe_to_dcf_assumptions",
    "is_503_unavailable_error",
    "load_demo_financials",
    "load_demo_qualitative_audit",
    "resolve_fixtures_dir",
    "run_qualitative_audit_with_fallback",
]
