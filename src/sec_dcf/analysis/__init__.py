"""Deterministic financial ratios, WACC, FCFF/DCF valuation, and sensitivity engine."""

from sec_dcf.analysis.dcf import (
    build_sensitivity_matrix,
    run_dcf_valuation,
    suggest_dcf_assumptions,
)
from sec_dcf.analysis.fred_client import FredClient, fetch_risk_free_rate
from sec_dcf.analysis.multiples import compute_market_multiples
from sec_dcf.analysis.ratios import compute_historical_metrics
from sec_dcf.analysis.wacc import calculate_wacc

__all__ = [
    "FredClient",
    "build_sensitivity_matrix",
    "calculate_wacc",
    "compute_historical_metrics",
    "compute_market_multiples",
    "fetch_risk_free_rate",
    "run_dcf_valuation",
    "suggest_dcf_assumptions",
]
