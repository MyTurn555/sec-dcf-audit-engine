"""Deterministic CAPM and WACC calculation engine."""

from __future__ import annotations

from decimal import Decimal

from sec_dcf.analysis.fred_client import FredClient
from sec_dcf.analysis.ratios import resolve_nopat_tax_rate
from sec_dcf.exceptions import WaccCalculationError
from sec_dcf.models.analysis import (
    CostOfDebtSource,
    EquityWeightSource,
    RiskFreeRateQuote,
    WaccInputs,
    WaccResult,
    WaccTaxRateSource,
)
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport

ZERO = Decimal("0")
ONE = Decimal("1")
TWO = Decimal("2")
MAX_PLAUSIBLE_COST_OF_DEBT = Decimal("0.25")


def _resolve_shares_for_wacc(
    latest: AnnualFinancialPeriod,
    inputs: WaccInputs,
) -> Decimal | None:
    """Resolve positive share count for Market Cap calculation."""
    if inputs.shares_override is not None:
        return inputs.shares_override
    if latest.diluted_shares is not None and latest.diluted_shares > ZERO:
        return latest.diluted_shares
    if latest.ending_shares is not None and latest.ending_shares > ZERO:
        return latest.ending_shares
    return None


def _resolve_equity_for_weights(
    latest: AnnualFinancialPeriod,
    inputs: WaccInputs,
) -> tuple[Decimal, EquityWeightSource]:
    """Resolve Equity value (E) for WACC capital structure weights."""
    if inputs.market_cap_override is not None:
        return inputs.market_cap_override, "market_cap_override"

    if inputs.share_price is not None:
        shares = _resolve_shares_for_wacc(latest, inputs)
        if shares is None:
            raise WaccCalculationError(
                f"FY{latest.fiscal_year}: share_price provided, but no positive diluted_shares, "
                "ending_shares, or shares_override is available to compute Market Cap."
            )
        return inputs.share_price * shares, "share_price_x_shares"

    if inputs.allow_book_equity_fallback:
        if latest.stockholders_equity > ZERO:
            return latest.stockholders_equity, "book_equity_fallback"
        raise WaccCalculationError(
            f"FY{latest.fiscal_year}: stockholders_equity ({latest.stockholders_equity}) "
            "is non-positive; cannot use book equity fallback for WACC weights."
        )

    raise WaccCalculationError(
        "WACC requires market value of equity: provide market_cap_override, share_price, "
        "or set allow_book_equity_fallback=True."
    )


def _resolve_pretax_cost_of_debt(
    sorted_periods: list[AnnualFinancialPeriod],
    debt_for_weights: Decimal,
    rf_rate: Decimal,
    inputs: WaccInputs,
    notes: list[str],
) -> tuple[Decimal, CostOfDebtSource]:
    """
    Resolve pre-tax Cost of Debt (Kd).

    Per US GAAP (ASC 842), operating lease cost is not recorded in interest_expense,
    so implied Kd is always computed against financial_debt (2-year average when available)
    rather than total_debt_incl_leases, with sanity bounds [Rf, 25%].
    """
    if inputs.pretax_cost_of_debt_override is not None:
        return inputs.pretax_cost_of_debt_override, "override"

    if debt_for_weights == ZERO:
        return ZERO, "zero_debt"

    latest = sorted_periods[-1]
    if len(sorted_periods) >= 2:
        prev = sorted_periods[-2]
        avg_financial_debt = (prev.financial_debt + latest.financial_debt) / TWO
        financial_debt_base = (
            avg_financial_debt if avg_financial_debt > ZERO else latest.financial_debt
        )
    else:
        financial_debt_base = latest.financial_debt

    if financial_debt_base > ZERO and latest.interest_expense > ZERO:
        implied_kd = latest.interest_expense / financial_debt_base
        if implied_kd < rf_rate or implied_kd > MAX_PLAUSIBLE_COST_OF_DEBT:
            fallback_kd = max(ZERO, rf_rate + inputs.default_credit_spread)
            notes.append(
                f"FY{latest.fiscal_year}: implied Kd ({implied_kd:.4f} on financial_debt) "
                f"fell outside plausible range [{rf_rate:.4f}, {MAX_PLAUSIBLE_COST_OF_DEBT:.4f}]; "
                f"used fallback Rf + spread ({fallback_kd:.4f})."
            )
            return fallback_kd, "rf_plus_spread"
        return implied_kd, "implied_from_financial_debt"

    fallback_kd = max(ZERO, rf_rate + inputs.default_credit_spread)
    notes.append(
        f"FY{latest.fiscal_year}: financial_debt or interest_expense is zero while debt_for_weights > 0; "
        f"used fallback Rf + spread ({fallback_kd:.4f}) for pre-tax Kd."
    )
    return fallback_kd, "rf_plus_spread"


def calculate_wacc(
    report: CompanyFinancialsReport,
    inputs: WaccInputs,
    *,
    fred_client: FredClient | None = None,
) -> WaccResult:
    """
    Compute deterministic CAPM Cost of Equity, after-tax Cost of Debt, and WACC.

    Consistency invariant: `inputs.include_leases` governs the debt weight in WACC
    and must match `DcfAssumptions.include_leases` during DCF valuation.
    """
    if not report.periods:
        raise WaccCalculationError(
            f"{report.ticker}: cannot calculate WACC without annual periods."
        )

    sorted_periods = sorted(report.periods, key=lambda p: p.period_end)
    latest = sorted_periods[-1]
    notes: list[str] = []

    if inputs.risk_free_rate is not None:
        rf_quote = RiskFreeRateQuote(
            series_id="MANUAL",
            rate=inputs.risk_free_rate,
            observation_date=None,
            source="manual",
        )
    else:
        client = fred_client or FredClient()
        rf_quote = client.get_latest_rate("DGS10")

    cost_of_equity = rf_quote.rate + inputs.beta * inputs.equity_risk_premium

    if inputs.tax_rate is not None:
        tax_rate = inputs.tax_rate
        tax_rate_source: WaccTaxRateSource = "override"
    else:
        _, tax_rate, _, tax_note = resolve_nopat_tax_rate(latest)
        tax_rate_source = "latest_nopat_tax_rate"
        if tax_note is not None:
            notes.append(tax_note)

    debt_value = (
        latest.total_debt_incl_leases if inputs.include_leases else latest.financial_debt
    )
    if inputs.include_leases:
        notes.append(
            "include_leases=True: debt weight includes lease liabilities, while implied Kd "
            "uses interest-bearing financial_debt (ASC 842 consistency)."
        )

    pretax_kd, kd_source = _resolve_pretax_cost_of_debt(
        sorted_periods=sorted_periods,
        debt_for_weights=debt_value,
        rf_rate=rf_quote.rate,
        inputs=inputs,
        notes=notes,
    )
    after_tax_kd = pretax_kd * (ONE - tax_rate)

    equity_value, equity_source = _resolve_equity_for_weights(latest, inputs)
    if equity_source == "book_equity_fallback":
        notes.append(
            f"FY{latest.fiscal_year}: WACC weights use book stockholders_equity fallback "
            "(less accurate than market capitalization)."
        )

    total_capital = equity_value + debt_value
    if total_capital <= ZERO:
        raise WaccCalculationError(
            f"Total capital (E + D = {total_capital}) must be positive for WACC."
        )

    equity_weight = equity_value / total_capital
    debt_weight = debt_value / total_capital
    wacc = equity_weight * cost_of_equity + debt_weight * after_tax_kd

    if wacc <= ZERO:
        raise WaccCalculationError(f"Calculated WACC ({wacc}) must be positive.")

    return WaccResult(
        risk_free_rate=rf_quote,
        cost_of_equity=cost_of_equity,
        pretax_cost_of_debt=pretax_kd,
        cost_of_debt_source=kd_source,
        tax_rate=tax_rate,
        tax_rate_source=tax_rate_source,
        after_tax_cost_of_debt=after_tax_kd,
        equity_value_for_weights=equity_value,
        equity_weight_source=equity_source,
        debt_value_for_weights=debt_value,
        include_leases=inputs.include_leases,
        total_capital=total_capital,
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        wacc=wacc,
        notes=notes,
    )
