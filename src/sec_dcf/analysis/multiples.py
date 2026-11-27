"""Deterministic current market multiples vs. implied DCF multiples confrontation."""

from __future__ import annotations

from decimal import Decimal

from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import (
    DcfValuationResult,
    MarketMultiplesComparison,
    MultiplesSnapshot,
    SharesSource,
)
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport

ZERO = Decimal("0")


def _safe_multiple(numerator: Decimal | None, denominator: Decimal) -> Decimal | None:
    """Compute numerator / denominator only when numerator is present and denominator > 0."""
    if numerator is None or denominator <= ZERO:
        return None
    return numerator / denominator


def _safe_yield(cash_flow: Decimal, equity_value: Decimal | None) -> Decimal | None:
    """Compute equity-based cash yield only when equity_value is present and > 0."""
    if equity_value is None or equity_value <= ZERO:
        return None
    return cash_flow / equity_value


def _resolve_shares_optional(
    latest: AnnualFinancialPeriod,
    shares_override: Decimal | None,
) -> tuple[Decimal | None, SharesSource | None]:
    """Resolve positive share count if available without throwing on absence."""
    if shares_override is not None:
        return shares_override, "override"
    if latest.diluted_shares is not None and latest.diluted_shares > ZERO:
        return latest.diluted_shares, "diluted_shares"
    if latest.ending_shares is not None and latest.ending_shares > ZERO:
        return latest.ending_shares, "ending_shares"
    return None, None


def _build_snapshot(
    *,
    enterprise_value: Decimal | None,
    equity_value: Decimal | None,
    latest: AnnualFinancialPeriod,
    ebitda: Decimal,
) -> MultiplesSnapshot:
    """Build valuation multiples and FCF yields against latest 10-K fundamentals."""
    return MultiplesSnapshot(
        ev_to_revenue=_safe_multiple(enterprise_value, latest.revenue),
        ev_to_ebitda=_safe_multiple(enterprise_value, ebitda),
        ev_to_ebit=_safe_multiple(enterprise_value, latest.ebit),
        pe_ratio=_safe_multiple(equity_value, latest.net_income),
        reported_fcf_yield=_safe_yield(latest.fcf_simple, equity_value),
        true_fcf_yield_ex_sbc=_safe_yield(latest.fcf_ex_sbc, equity_value),
    )


def compute_market_multiples(
    report: CompanyFinancialsReport,
    *,
    include_leases: bool = False,
    share_price: Decimal | None = None,
    market_cap_override: Decimal | None = None,
    shares_override: Decimal | None = None,
    dcf_result: DcfValuationResult | None = None,
) -> MarketMultiplesComparison:
    """
    Compute current market multiples (from user share_price or market_cap_override)
    alongside implied DCF valuation multiples on the latest normalized 10-K period.

    Net Debt and EV definitions strictly respect `include_leases` (ASC 842) and
    must match `dcf_result.include_leases` when `dcf_result` is provided.
    """
    if not report.periods:
        raise DcfValuationError(
            f"{report.ticker}: cannot compute market multiples without annual periods."
        )
    if share_price is not None and share_price <= ZERO:
        raise DcfValuationError(f"share_price must be positive, got {share_price}.")
    if market_cap_override is not None and market_cap_override <= ZERO:
        raise DcfValuationError(
            f"market_cap_override must be positive, got {market_cap_override}."
        )
    if shares_override is not None and shares_override <= ZERO:
        raise DcfValuationError(f"shares_override must be positive, got {shares_override}.")
    if dcf_result is not None and dcf_result.include_leases != include_leases:
        raise DcfValuationError(
            f"include_leases mismatch: dcf_result has include_leases={dcf_result.include_leases}, "
            f"but compute_market_multiples received include_leases={include_leases}."
        )

    sorted_periods = sorted(report.periods, key=lambda p: p.period_end)
    latest = sorted_periods[-1]
    ebitda = latest.ebit + latest.da
    notes: list[str] = []

    shares_used, shares_source = _resolve_shares_optional(latest, shares_override)

    current_market_cap: Decimal | None = None
    share_price_used: Decimal | None = None
    if market_cap_override is not None:
        current_market_cap = market_cap_override
        if shares_used is not None:
            share_price_used = market_cap_override / shares_used
    elif share_price is not None:
        if shares_used is None:
            raise DcfValuationError(
                f"FY{latest.fiscal_year}: share_price provided, but no positive diluted_shares, "
                "ending_shares, or shares_override is available to compute Market Cap."
            )
        current_market_cap = share_price * shares_used
        share_price_used = share_price
    else:
        notes.append(
            "Brak ceny akcji lub Market Cap (np. aktywny fallback księgowy w WACC) — "
            "bieżące mnożniki rynkowe są ustawione na None."
        )

    debt_used = (
        latest.total_debt_incl_leases if include_leases else latest.financial_debt
    )
    net_debt = debt_used - latest.total_cash
    nci = latest.noncontrolling_interest
    current_ev = (
        current_market_cap + net_debt + nci if current_market_cap is not None else None
    )

    if current_ev is not None and current_ev <= ZERO:
        notes.append(
            f"FY{latest.fiscal_year}: bieżące Enterprise Value ({current_ev:.2f}) <= 0 "
            "wskutek dużej pozycji gotówki netto."
        )
    if latest.net_income <= ZERO:
        notes.append(
            f"FY{latest.fiscal_year}: net_income <= 0 ({latest.net_income:.2f}); "
            "wskaźnik P/E ustawiono na None."
        )

    current_snapshot = _build_snapshot(
        enterprise_value=current_ev,
        equity_value=current_market_cap,
        latest=latest,
        ebitda=ebitda,
    )

    dcf_ev = dcf_result.bridge.enterprise_value if dcf_result is not None else None
    dcf_equity = dcf_result.bridge.implied_equity_value if dcf_result is not None else None
    if dcf_result is not None and dcf_result.bridge.equity_floored_at_zero:
        notes.append(
            "DCF Implied Equity Value ograniczono do $0.00 — implikowane FCF Yield ustawiono na None."
        )

    implied_snapshot = _build_snapshot(
        enterprise_value=dcf_ev,
        equity_value=dcf_equity,
        latest=latest,
        ebitda=ebitda,
    )

    return MarketMultiplesComparison(
        ticker=report.ticker,
        fiscal_year=latest.fiscal_year,
        include_leases=include_leases,
        shares_used=shares_used,
        shares_source=shares_source,
        share_price_used=share_price_used,
        current_market_cap=current_market_cap,
        net_debt=net_debt,
        noncontrolling_interest=nci,
        current_enterprise_value=current_ev,
        dcf_enterprise_value=dcf_ev,
        dcf_implied_equity_value=dcf_equity,
        current=current_snapshot,
        implied_dcf=implied_snapshot,
        notes=notes,
    )
