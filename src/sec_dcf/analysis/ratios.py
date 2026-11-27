"""Deterministic historical financial ratios and capital efficiency engine."""

from __future__ import annotations

from decimal import Decimal

from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import (
    AnnualMetrics,
    HistoricalMetricsReport,
    NopatTaxSource,
    RoicBasis,
)
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport

ZERO = Decimal("0")
ONE = Decimal("1")
TWO = Decimal("2")
DEFAULT_TAX_RATE = Decimal("0.21")
DEFAULT_MAX_TAX_RATE = Decimal("0.50")


def resolve_nopat_tax_rate(
    period: AnnualFinancialPeriod,
    default_tax_rate: Decimal = DEFAULT_TAX_RATE,
    max_tax_rate: Decimal = DEFAULT_MAX_TAX_RATE,
) -> tuple[Decimal | None, Decimal, NopatTaxSource, str | None]:
    """
    Compute raw effective tax rate and a bounded safe tax rate for NOPAT.

    When pretax_income <= 0 or tax inputs are missing, raw_effective_tax_rate is None
    and NOPAT uses default_tax_rate to avoid distorting operating profitability.
    """
    if (
        period.pretax_income is not None
        and period.tax_expense is not None
        and period.pretax_income > ZERO
    ):
        raw_etr = period.tax_expense / period.pretax_income
        if raw_etr < ZERO:
            return (
                raw_etr,
                ZERO,
                "clamped",
                f"FY{period.fiscal_year}: negative effective tax rate ({raw_etr:.4f}) clamped to 0.0000 for NOPAT.",
            )
        if raw_etr > max_tax_rate:
            return (
                raw_etr,
                max_tax_rate,
                "clamped",
                (
                    f"FY{period.fiscal_year}: effective tax rate ({raw_etr:.4f}) exceeded "
                    f"cap ({max_tax_rate:.4f}); clamped for NOPAT."
                ),
            )
        return raw_etr, raw_etr, "effective", None

    return (
        None,
        default_tax_rate,
        "fallback_loss_or_missing",
        (
            f"FY{period.fiscal_year}: pretax_income <= 0 or tax data missing; "
            f"used fallback tax rate {default_tax_rate:.4f} for NOPAT."
        ),
    )


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Return numerator / denominator only when denominator > 0, else None."""
    if denominator <= ZERO:
        return None
    return numerator / denominator


def compute_historical_metrics(
    report: CompanyFinancialsReport,
    *,
    include_leases: bool = False,
    default_tax_rate: Decimal = DEFAULT_TAX_RATE,
    max_tax_rate: Decimal = DEFAULT_MAX_TAX_RATE,
) -> HistoricalMetricsReport:
    """
    Compute deterministic historical profitability, ROIC, cash conversion,
    capital intensity, and leverage metrics across all periods in `report`.
    """
    if not report.periods:
        raise DcfValuationError(
            f"{report.ticker}: cannot compute historical metrics without annual periods."
        )
    if default_tax_rate < ZERO or default_tax_rate > ONE:
        raise DcfValuationError(f"default_tax_rate must be in [0, 1], got {default_tax_rate}")
    if max_tax_rate < ZERO or max_tax_rate > ONE:
        raise DcfValuationError(f"max_tax_rate must be in [0, 1], got {max_tax_rate}")
    if default_tax_rate > max_tax_rate:
        raise DcfValuationError(
            f"default_tax_rate ({default_tax_rate}) cannot exceed max_tax_rate ({max_tax_rate})."
        )

    sorted_periods = sorted(report.periods, key=lambda p: p.period_end)
    metrics_list: list[AnnualMetrics] = []
    prev_ic: Decimal | None = None

    for idx, period in enumerate(sorted_periods):
        notes: list[str] = []
        if include_leases:
            notes.append(
                "include_leases=True: per US GAAP (ASC 842), EBIT/EBITDA are not adjusted "
                "for implied operating lease interest (conscious model simplification)."
            )

        ebitda = period.ebit + period.da
        ebit_margin = _safe_ratio(period.ebit, period.revenue)
        ebitda_margin = _safe_ratio(ebitda, period.revenue)
        net_margin = _safe_ratio(period.net_income, period.revenue)
        if period.revenue <= ZERO:
            notes.append(f"FY{period.fiscal_year}: revenue <= 0; revenue-based ratios set to None.")

        raw_etr, nopat_tax_rate, tax_source, tax_note = resolve_nopat_tax_rate(
            period,
            default_tax_rate=default_tax_rate,
            max_tax_rate=max_tax_rate,
        )
        if tax_note is not None:
            notes.append(tax_note)

        nopat = period.ebit * (ONE - nopat_tax_rate)

        equity_total = period.total_equity_for_balance_check
        ic_excl = period.financial_debt + equity_total - period.total_cash
        ic_incl = period.total_debt_incl_leases + equity_total - period.total_cash
        ic = ic_incl if include_leases else ic_excl

        if idx == 0 or prev_ic is None:
            avg_ic = ic
            if avg_ic > ZERO:
                roic_basis: RoicBasis = "ending_fallback"
                roic = nopat / avg_ic
                notes.append(
                    f"FY{period.fiscal_year}: first available year; ROIC uses ending Invested Capital."
                )
            else:
                roic_basis = "not_meaningful"
                roic = None
                notes.append(
                    f"FY{period.fiscal_year}: Invested Capital <= 0 ({avg_ic}); ROIC not meaningful."
                )
        else:
            avg_ic = (prev_ic + ic) / TWO
            if avg_ic > ZERO:
                roic_basis = "average"
                roic = nopat / avg_ic
            else:
                roic_basis = "not_meaningful"
                roic = None
                notes.append(
                    f"FY{period.fiscal_year}: average Invested Capital <= 0 ({avg_ic}); "
                    "ROIC not meaningful."
                )

        prev_ic = ic

        cash_conversion = _safe_ratio(period.fcf_simple, period.net_income)
        if period.net_income <= ZERO:
            notes.append(
                f"FY{period.fiscal_year}: net_income <= 0; cash conversion set to None."
            )

        capex_to_revenue = _safe_ratio(period.capex, period.revenue)
        da_to_revenue = _safe_ratio(period.da, period.revenue)
        sbc_to_revenue = _safe_ratio(period.sbc, period.revenue)
        sbc_to_cfo = _safe_ratio(period.sbc, period.cfo)
        non_cash_nwc_to_revenue = (
            _safe_ratio(period.non_cash_nwc, period.revenue)
            if period.non_cash_nwc is not None
            else None
        )

        net_debt_excl = period.financial_debt - period.total_cash
        net_debt_incl = period.total_debt_incl_leases - period.total_cash

        # Negative Net Debt / EBITDA (net cash position) is valid whenever EBITDA > 0.
        net_debt_to_ebitda_excl = _safe_ratio(net_debt_excl, ebitda)
        net_debt_to_ebitda_incl = _safe_ratio(net_debt_incl, ebitda)
        if ebitda <= ZERO:
            notes.append(
                f"FY{period.fiscal_year}: EBITDA <= 0; Net Debt / EBITDA set to None."
            )

        interest_coverage = _safe_ratio(period.ebit, period.interest_expense)

        metrics_list.append(
            AnnualMetrics(
                fiscal_year=period.fiscal_year,
                period_end=period.period_end,
                revenue=period.revenue,
                ebit=period.ebit,
                ebitda=ebitda,
                net_income=period.net_income,
                ebit_margin=ebit_margin,
                ebitda_margin=ebitda_margin,
                net_margin=net_margin,
                raw_effective_tax_rate=raw_etr,
                nopat_tax_rate=nopat_tax_rate,
                nopat_tax_rate_source=tax_source,
                nopat=nopat,
                include_leases=include_leases,
                invested_capital_excl_leases=ic_excl,
                invested_capital_incl_leases=ic_incl,
                invested_capital=ic,
                average_invested_capital=avg_ic,
                roic_basis=roic_basis,
                roic=roic,
                fcf_simple=period.fcf_simple,
                sbc=period.sbc,
                fcf_ex_sbc=period.fcf_ex_sbc,
                sbc_to_revenue=sbc_to_revenue,
                sbc_to_cfo=sbc_to_cfo,
                cash_conversion=cash_conversion,
                capex_to_revenue=capex_to_revenue,
                da_to_revenue=da_to_revenue,
                non_cash_nwc=period.non_cash_nwc,
                non_cash_nwc_to_revenue=non_cash_nwc_to_revenue,
                net_debt_excl_leases=net_debt_excl,
                net_debt_incl_leases=net_debt_incl,
                net_debt_to_ebitda_excl_leases=net_debt_to_ebitda_excl,
                net_debt_to_ebitda_incl_leases=net_debt_to_ebitda_incl,
                interest_coverage=interest_coverage,
                notes=notes,
            )
        )

    return HistoricalMetricsReport(
        ticker=report.ticker,
        company_name=report.company_name,
        include_leases=include_leases,
        default_tax_rate=default_tax_rate,
        max_tax_rate=max_tax_rate,
        periods=metrics_list,
    )
