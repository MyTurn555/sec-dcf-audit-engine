"""Deterministic 5-year FCFF/DCF valuation and 2D sensitivity engine."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sec_dcf.analysis.ratios import compute_historical_metrics
from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import (
    DcfAssumptions,
    DcfValuationResult,
    DiscountingConvention,
    EnterpriseToEquityBridge,
    InitialNwcSource,
    ProjectedYearCashFlow,
    SensitivityCell,
    SensitivityMatrix,
    SharesSource,
    TerminalValueSummary,
    WaccResult,
)
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport

ZERO = Decimal("0")
HALF = Decimal("0.5")
ONE = Decimal("1")
FIVE = Decimal("5")

EDUCATIONAL_DISCLAIMER = (
    "Educational/research output only: implied valuation figures reflect user-supplied "
    "assumptions and do not constitute investment advice or a Buy/Sell recommendation."
)


def _decimal_median(values: Sequence[Decimal], fallback: Decimal) -> Decimal:
    """Compute exact Decimal median (midpoint average for even counts) with fallback."""
    if not values:
        return fallback
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return sorted_vals[mid]
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / Decimal("2")


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    """Clamp a Decimal value to [low, high]."""
    return max(low, min(high, value))


def suggest_dcf_assumptions(
    report: CompanyFinancialsReport,
    *,
    include_leases: bool = False,
    terminal_growth_rate: Decimal = Decimal("0.025"),
    discounting_convention: DiscountingConvention = "mid_year",
) -> DcfAssumptions:
    """
    Derive a transparent, deterministic starting-point DcfAssumptions object
    from historical medians of the company's normalized 10-K periods.
    """
    metrics_report = compute_historical_metrics(report, include_leases=include_leases)
    periods = metrics_report.periods

    rev_growths: list[Decimal] = []
    for prev, curr in zip(periods, periods[1:]):
        if prev.revenue > ZERO and curr.revenue > ZERO:
            rev_growths.append((curr.revenue - prev.revenue) / prev.revenue)

    ebit_margins = [p.ebit_margin for p in periods if p.ebit_margin is not None]
    tax_rates = [p.nopat_tax_rate for p in periods]
    da_pcts = [p.da_to_revenue for p in periods if p.da_to_revenue is not None]
    capex_pcts = [p.capex_to_revenue for p in periods if p.capex_to_revenue is not None]
    nwc_pcts = [
        p.non_cash_nwc_to_revenue for p in periods if p.non_cash_nwc_to_revenue is not None
    ]

    suggested_growth = _clamp(
        _decimal_median(rev_growths, Decimal("0.05")),
        Decimal("-0.20"),
        Decimal("0.30"),
    )
    suggested_ebit_margin = _clamp(
        _decimal_median(ebit_margins, Decimal("0.15")),
        Decimal("-0.50"),
        Decimal("0.60"),
    )
    suggested_tax = _clamp(
        _decimal_median(tax_rates, Decimal("0.21")),
        Decimal("0.00"),
        Decimal("0.50"),
    )
    suggested_da = max(ZERO, _decimal_median(da_pcts, Decimal("0.04")))
    suggested_capex = max(ZERO, _decimal_median(capex_pcts, Decimal("0.05")))
    suggested_nwc = _decimal_median(nwc_pcts, Decimal("0.00"))

    return DcfAssumptions(
        revenue_growth=suggested_growth,
        ebit_margin=suggested_ebit_margin,
        tax_rate=suggested_tax,
        da_pct_revenue=suggested_da,
        capex_pct_revenue=suggested_capex,
        nwc_pct_revenue=suggested_nwc,
        terminal_growth_rate=terminal_growth_rate,
        discounting_convention=discounting_convention,
        include_leases=include_leases,
    )


def _resolve_wacc_decimal(
    wacc: Decimal | WaccResult,
    assumptions: DcfAssumptions,
) -> Decimal:
    """Extract WACC as Decimal and enforce lease-treatment consistency."""
    if isinstance(wacc, WaccResult):
        if wacc.include_leases != assumptions.include_leases:
            raise DcfValuationError(
                f"include_leases mismatch: WaccResult has include_leases={wacc.include_leases}, "
                f"but DcfAssumptions has include_leases={assumptions.include_leases}."
            )
        return wacc.wacc
    return wacc


def _resolve_shares(
    latest: AnnualFinancialPeriod,
    assumptions: DcfAssumptions,
) -> tuple[Decimal, SharesSource]:
    """Resolve positive diluted shares for Implied Value per Share."""
    if assumptions.shares_override is not None:
        return assumptions.shares_override, "override"
    if latest.diluted_shares is not None and latest.diluted_shares > ZERO:
        return latest.diluted_shares, "diluted_shares"
    if latest.ending_shares is not None and latest.ending_shares > ZERO:
        return latest.ending_shares, "ending_shares"
    raise DcfValuationError(
        f"FY{latest.fiscal_year}: cannot compute value per share without positive "
        "diluted_shares, ending_shares, or shares_override."
    )


def _resolve_initial_nwc(
    latest: AnnualFinancialPeriod,
    assumptions: DcfAssumptions,
) -> tuple[Decimal, InitialNwcSource]:
    """Resolve base year (t=0) non-cash NWC for Year 1 delta NWC."""
    if assumptions.initial_nwc_override is not None:
        return assumptions.initial_nwc_override, "override"
    if latest.non_cash_nwc is not None:
        return latest.non_cash_nwc, "historical_balance_sheet"
    return assumptions.nwc_pct_revenue[0] * latest.revenue, "model_pct_fallback"


def _build_bridge(
    enterprise_value: Decimal,
    latest: AnnualFinancialPeriod,
    assumptions: DcfAssumptions,
    shares_used: Decimal,
    shares_source: SharesSource,
) -> EnterpriseToEquityBridge:
    """Bridge Enterprise Value -> Equity Value -> Implied Value per Share."""
    if assumptions.include_leases:
        debt_deducted = latest.total_debt_incl_leases
        lease_included = latest.lease_liabilities
    else:
        debt_deducted = latest.financial_debt
        lease_included = ZERO

    raw_equity = (
        enterprise_value
        - debt_deducted
        - latest.noncontrolling_interest
        + latest.total_cash
    )
    floored = raw_equity < ZERO
    implied_equity = ZERO if floored else raw_equity
    raw_per_share = raw_equity / shares_used
    implied_per_share = ZERO if floored else raw_per_share

    return EnterpriseToEquityBridge(
        enterprise_value=enterprise_value,
        debt_deducted=debt_deducted,
        lease_liabilities_included=lease_included,
        noncontrolling_interest_deducted=latest.noncontrolling_interest,
        total_cash_added=latest.total_cash,
        raw_implied_equity_value=raw_equity,
        implied_equity_value=implied_equity,
        equity_floored_at_zero=floored,
        diluted_shares_used=shares_used,
        shares_source=shares_source,
        raw_implied_value_per_share=raw_per_share,
        implied_value_per_share=implied_per_share,
        include_leases=assumptions.include_leases,
    )


def run_dcf_valuation(
    report: CompanyFinancialsReport,
    assumptions: DcfAssumptions,
    wacc: Decimal | WaccResult,
) -> DcfValuationResult:
    """
    Execute a deterministic 5-year FCFF/DCF valuation from normalized 10-K financials.

    Raises DcfValuationError when WACC <= terminal_growth_rate, when include_leases
    differs between WaccResult and DcfAssumptions, or when shares/periods are missing.
    """
    if not report.periods:
        raise DcfValuationError(
            f"{report.ticker}: cannot run DCF valuation without annual periods."
        )

    wacc_value = _resolve_wacc_decimal(wacc, assumptions)
    if wacc_value <= ZERO:
        raise DcfValuationError(f"WACC must be positive, got {wacc_value}.")
    if wacc_value <= assumptions.terminal_growth_rate:
        raise DcfValuationError(
            f"Gordon Growth requires WACC ({wacc_value}) > terminal_growth_rate "
            f"({assumptions.terminal_growth_rate})."
        )

    sorted_periods = sorted(report.periods, key=lambda p: p.period_end)
    latest = sorted_periods[-1]
    shares_used, shares_source = _resolve_shares(latest, assumptions)
    initial_nwc, initial_nwc_source = _resolve_initial_nwc(latest, assumptions)

    notes: list[str] = [EDUCATIONAL_DISCLAIMER]
    if assumptions.include_leases:
        notes.append(
            "include_leases=True: EV -> Equity Value bridge deducts total_debt_incl_leases "
            "(ASC 842 lease liabilities included consistently with WACC)."
        )
    if initial_nwc_source == "model_pct_fallback":
        notes.append(
            f"FY{latest.fiscal_year}: balance-sheet non_cash_nwc unavailable; "
            "initial NWC_0 derived from year-1 nwc_pct_revenue * base_revenue."
        )

    projections: list[ProjectedYearCashFlow] = []
    prev_revenue = latest.revenue
    prev_nwc = initial_nwc
    pv_projected_fcff = ZERO
    one_plus_wacc = ONE + wacc_value

    for idx in range(5):
        year_num = idx + 1
        fiscal_year = latest.fiscal_year + year_num
        g_t = assumptions.revenue_growth[idx]
        margin_t = assumptions.ebit_margin[idx]
        tax_t = assumptions.tax_rate[idx]
        da_pct_t = assumptions.da_pct_revenue[idx]
        capex_pct_t = assumptions.capex_pct_revenue[idx]
        nwc_pct_t = assumptions.nwc_pct_revenue[idx]

        revenue_t = prev_revenue * (ONE + g_t)
        ebit_t = revenue_t * margin_t
        nopat_t = ebit_t * (ONE - tax_t)
        da_t = revenue_t * da_pct_t
        capex_t = revenue_t * capex_pct_t
        nwc_t = revenue_t * nwc_pct_t
        delta_nwc_t = nwc_t - prev_nwc
        fcff_t = nopat_t + da_t - capex_t - delta_nwc_t
        ebitda_t = ebit_t + da_t

        discount_period = (
            Decimal(year_num)
            if assumptions.discounting_convention == "end_of_year"
            else Decimal(year_num) - HALF
        )
        discount_factor = ONE / (one_plus_wacc**discount_period)
        pv_fcff = fcff_t * discount_factor
        pv_projected_fcff += pv_fcff

        projections.append(
            ProjectedYearCashFlow(
                year_index=year_num,
                fiscal_year=fiscal_year,
                revenue=revenue_t,
                ebit=ebit_t,
                tax_rate=tax_t,
                nopat=nopat_t,
                da=da_t,
                capex=capex_t,
                nwc=nwc_t,
                delta_nwc=delta_nwc_t,
                fcff=fcff_t,
                ebitda=ebitda_t,
                discount_period=discount_period,
                discount_factor=discount_factor,
                pv_fcff=pv_fcff,
            )
        )
        prev_revenue = revenue_t
        prev_nwc = nwc_t

    year5 = projections[-1]
    if year5.fcff <= ZERO:
        notes.append(
            f"Warning: terminal year FCFF_5 ({year5.fcff:.2f}) <= 0; Gordon Growth formula "
            "produces a non-market / negative Terminal Value."
        )

    terminal_fcff = year5.fcff * (ONE + assumptions.terminal_growth_rate)
    undiscounted_tv = terminal_fcff / (wacc_value - assumptions.terminal_growth_rate)
    tv_discount_factor = ONE / (one_plus_wacc**FIVE)
    pv_tv = undiscounted_tv * tv_discount_factor

    enterprise_value = pv_projected_fcff + pv_tv
    tv_share_of_ev = (pv_tv / enterprise_value) if enterprise_value > ZERO else None
    if tv_share_of_ev is not None and tv_share_of_ev > Decimal("0.80"):
        notes.append(
            f"Terminal Value represents {tv_share_of_ev:.1%} of Enterprise Value (> 80%); "
            "valuation is highly sensitive to WACC and terminal growth assumptions."
        )

    implied_exit_ev_ebitda = (
        undiscounted_tv / year5.ebitda if year5.ebitda > ZERO else None
    )

    exit_tv: Decimal | None = None
    exit_pv_tv: Decimal | None = None
    exit_ev: Decimal | None = None
    exit_per_share: Decimal | None = None
    if assumptions.exit_ebitda_multiple is not None and year5.ebitda > ZERO:
        exit_tv = year5.ebitda * assumptions.exit_ebitda_multiple
        exit_pv_tv = exit_tv * tv_discount_factor
        exit_ev = pv_projected_fcff + exit_pv_tv
        exit_bridge = _build_bridge(exit_ev, latest, assumptions, shares_used, shares_source)
        exit_per_share = exit_bridge.implied_value_per_share
    elif assumptions.exit_ebitda_multiple is not None:
        notes.append(
            f"Year-5 EBITDA ({year5.ebitda:.2f}) <= 0; exit EV/EBITDA cross-check skipped."
        )

    tv_summary = TerminalValueSummary(
        terminal_fcff=terminal_fcff,
        terminal_growth_rate=assumptions.terminal_growth_rate,
        wacc=wacc_value,
        undiscounted_tv=undiscounted_tv,
        tv_discount_period=FIVE,
        pv_terminal_value=pv_tv,
        tv_share_of_ev=tv_share_of_ev,
        implied_exit_ev_ebitda=implied_exit_ev_ebitda,
        exit_multiple_crosscheck_tv=exit_tv,
        exit_multiple_crosscheck_pv_tv=exit_pv_tv,
        exit_multiple_crosscheck_ev=exit_ev,
        exit_multiple_crosscheck_value_per_share=exit_per_share,
    )

    bridge = _build_bridge(enterprise_value, latest, assumptions, shares_used, shares_source)
    if bridge.equity_floored_at_zero:
        notes.append(
            f"Raw implied equity value ({bridge.raw_implied_equity_value:.2f}) is negative; "
            "limited-liability equity value and value per share are floored at 0."
        )

    return DcfValuationResult(
        ticker=report.ticker,
        base_fiscal_year=latest.fiscal_year,
        base_revenue=latest.revenue,
        initial_nwc=initial_nwc,
        initial_nwc_source=initial_nwc_source,
        wacc=wacc_value,
        include_leases=assumptions.include_leases,
        discounting_convention=assumptions.discounting_convention,
        projections=projections,
        pv_projected_fcff=pv_projected_fcff,
        terminal_value=tv_summary,
        bridge=bridge,
        notes=notes,
    )


def build_sensitivity_matrix(
    report: CompanyFinancialsReport,
    assumptions: DcfAssumptions,
    wacc_values: Sequence[Decimal],
    terminal_growth_values: Sequence[Decimal],
    *,
    base_wacc: Decimal | WaccResult,
) -> SensitivityMatrix:
    """
    Build a 2D (WACC x terminal_growth_rate) sensitivity matrix returning the
    implied value-per-share range across all valid (WACC > g) combinations.
    """
    if not wacc_values or not terminal_growth_values:
        raise DcfValuationError(
            "wacc_values and terminal_growth_values must both be non-empty."
        )

    resolved_base_wacc = _resolve_wacc_decimal(base_wacc, assumptions)
    wacc_list = list(wacc_values)
    growth_list = list(terminal_growth_values)

    cells: list[list[SensitivityCell]] = []
    valid_per_share_values: list[Decimal] = []

    for w in wacc_list:
        row: list[SensitivityCell] = []
        for g in growth_list:
            cell_assumptions = assumptions.model_copy(update={"terminal_growth_rate": g})
            try:
                val = run_dcf_valuation(report, cell_assumptions, w)
            except DcfValuationError as exc:
                row.append(
                    SensitivityCell(
                        wacc=w,
                        terminal_growth_rate=g,
                        is_valid=False,
                        error_reason=str(exc),
                    )
                )
            else:
                per_share = val.bridge.implied_value_per_share
                valid_per_share_values.append(per_share)
                row.append(
                    SensitivityCell(
                        wacc=w,
                        terminal_growth_rate=g,
                        is_valid=True,
                        enterprise_value=val.bridge.enterprise_value,
                        implied_equity_value=val.bridge.implied_equity_value,
                        implied_value_per_share=per_share,
                    )
                )
        cells.append(row)

    min_val = min(valid_per_share_values) if valid_per_share_values else None
    max_val = max(valid_per_share_values) if valid_per_share_values else None

    return SensitivityMatrix(
        ticker=report.ticker,
        wacc_values=wacc_list,
        terminal_growth_values=growth_list,
        cells=cells,
        min_implied_value_per_share=min_val,
        max_implied_value_per_share=max_val,
        base_wacc=resolved_base_wacc,
        base_terminal_growth_rate=assumptions.terminal_growth_rate,
    )
