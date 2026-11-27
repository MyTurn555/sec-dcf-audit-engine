"""Deterministic, seed-reproducible vectorized Monte Carlo engine for FCFF/DCF valuation."""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

import numpy as np

from sec_dcf.analysis.dcf import (
    EDUCATIONAL_DISCLAIMER,
    _resolve_initial_nwc,
    _resolve_shares,
    _resolve_wacc_decimal,
    run_dcf_valuation,
)
from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import (
    DcfAssumptions,
    MonteCarloConfig,
    MonteCarloDistribution,
    MonteCarloResult,
    WaccResult,
)
from sec_dcf.models.financials import CompanyFinancialsReport

ZERO = Decimal("0")
PRICE_QUANT = Decimal("0.0001")
PROB_QUANT = Decimal("0.000001")


def _to_decimal(value: float, quant: Decimal = PRICE_QUANT) -> Decimal:
    """Convert a finite float statistic into a quantized Decimal."""
    return Decimal(str(float(value))).quantize(quant, rounding=ROUND_HALF_UP)


def _norm_cdf(z: np.ndarray) -> np.ndarray:
    """
    Vectorized standard normal CDF Φ(z) via Abramowitz & Stegun (7.1.26) erf approximation.

    Avoids requiring scipy while remaining accurate to ~1.5e-7 and clamped away from {0, 1}.
    """
    x = np.abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (
        0.254829592
        + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )
    erf_abs = 1.0 - poly * np.exp(-(x * x))
    erf_z = np.sign(z) * erf_abs
    phi = 0.5 * (1.0 + erf_z)
    return np.clip(phi, 1e-12, 1.0 - 1e-12)


def _sample_perturbation(
    z: np.ndarray,
    *,
    std: float,
    distribution: MonteCarloDistribution,
) -> np.ndarray:
    """Map standard normal draws `z` to zero-mean shocks with target `std`."""
    if std == 0.0:
        return np.zeros(z.shape[0], dtype=np.float64)
    if distribution == "normal":
        return std * z

    # Symmetric triangular distribution on [-a, +a] with variance a^2 / 6 = std^2 -> a = sqrt(6) * std.
    u = np.clip(_norm_cdf(z), 1e-12, 1.0 - 1e-12)
    half_width = math.sqrt(6.0) * std
    lower = half_width * (np.sqrt(2.0 * u) - 1.0)
    upper = half_width * (1.0 - np.sqrt(2.0 * (1.0 - u)))
    return np.where(u < 0.5, lower, upper)


def run_monte_carlo_dcf(
    report: CompanyFinancialsReport,
    assumptions: DcfAssumptions,
    wacc: Decimal | WaccResult,
    config: MonteCarloConfig | None = None,
) -> MonteCarloResult:
    """
    Execute a deterministic, seed-reproducible vectorized Monte Carlo simulation
    for the 5-year FCFF/DCF model over N scenarios (default 10,000).
    """
    if not report.periods:
        raise DcfValuationError(
            f"{report.ticker}: cannot run Monte Carlo DCF without annual periods."
        )

    cfg = config or MonteCarloConfig()
    wacc_value = _resolve_wacc_decimal(wacc, assumptions)
    if wacc_value <= ZERO:
        raise DcfValuationError(f"WACC must be positive, got {wacc_value}.")

    sorted_periods = sorted(report.periods, key=lambda p: p.period_end)
    latest = sorted_periods[-1]
    shares_used, _ = _resolve_shares(latest, assumptions)
    initial_nwc, _ = _resolve_initial_nwc(latest, assumptions)

    base_per_share: Decimal | None = None
    if wacc_value > assumptions.terminal_growth_rate:
        base_result = run_dcf_valuation(report, assumptions, wacc)
        base_per_share = base_result.bridge.implied_value_per_share

    n_total = cfg.n_simulations
    rng = np.random.default_rng(cfg.seed)
    z_matrix = rng.standard_normal((n_total, 4))
    z_wacc = z_matrix[:, 0]
    z_growth = z_matrix[:, 1]
    z_margin_indep = z_matrix[:, 2]
    z_term_g = z_matrix[:, 3]

    rho = float(cfg.growth_margin_correlation)
    if rho != 0.0:
        z_margin = rho * z_growth + math.sqrt(1.0 - rho * rho) * z_margin_indep
    else:
        z_margin = z_margin_indep

    wacc_std_f = float(cfg.wacc_std)
    rev_growth_std_f = float(cfg.revenue_growth_std)
    ebit_margin_std_f = float(cfg.ebit_margin_std)
    term_g_std_f = float(cfg.terminal_growth_std)

    base_wacc_f = float(wacc_value)
    base_term_g_f = float(assumptions.terminal_growth_rate)

    if wacc_std_f == 0.0:
        wacc_vec = np.full(n_total, base_wacc_f, dtype=np.float64)
    else:
        d_wacc = _sample_perturbation(z_wacc, std=wacc_std_f, distribution=cfg.distribution)
        wacc_vec = np.clip(
            base_wacc_f + d_wacc,
            float(cfg.wacc_bounds[0]),
            float(cfg.wacc_bounds[1]),
        )

    if term_g_std_f == 0.0:
        g_term_vec = np.full(n_total, base_term_g_f, dtype=np.float64)
    else:
        d_term_g = _sample_perturbation(z_term_g, std=term_g_std_f, distribution=cfg.distribution)
        g_term_vec = np.clip(
            base_term_g_f + d_term_g,
            float(cfg.terminal_growth_bounds[0]),
            float(cfg.terminal_growth_bounds[1]),
        )

    min_spread_f = float(cfg.min_wacc_g_spread)
    if min_spread_f > 0.0:
        valid_mask = (wacc_vec - g_term_vec) >= min_spread_f
    else:
        valid_mask = wacc_vec > g_term_vec

    valid_count = int(np.count_nonzero(valid_mask))
    discarded_count = n_total - valid_count

    notes: list[str] = [EDUCATIONAL_DISCLAIMER]
    if discarded_count > 0:
        if min_spread_f > 0.0:
            notes.append(
                f"Discarded {discarded_count} of {n_total} scenarios where "
                f"(WACC - g) < min_wacc_g_spread ({cfg.min_wacc_g_spread:.2%})."
            )
        else:
            notes.append(
                f"Discarded {discarded_count} of {n_total} scenarios where WACC <= g."
            )

    if valid_count == 0:
        notes.append(
            "All Monte Carlo scenarios were discarded by the Gordon Growth spread guard; "
            "no valuation distribution statistics are available."
        )
        return MonteCarloResult(
            ticker=report.ticker,
            seed=cfg.seed,
            distribution=cfg.distribution,
            total_simulations=n_total,
            valid_simulations=0,
            discarded_simulations=discarded_count,
            floored_at_zero_count=0,
            min_wacc_g_spread=cfg.min_wacc_g_spread,
            include_leases=assumptions.include_leases,
            add_back_sbc=assumptions.add_back_sbc,
            discounting_convention=assumptions.discounting_convention,
            base_implied_value_per_share=base_per_share,
            reference_share_price=cfg.reference_share_price,
            notes=notes,
        )

    wacc_valid = wacc_vec[valid_mask]
    g_term_valid = g_term_vec[valid_mask]

    base_rev_growth = np.array([float(x) for x in assumptions.revenue_growth], dtype=np.float64)
    base_ebit_margin = np.array([float(x) for x in assumptions.ebit_margin], dtype=np.float64)

    if rev_growth_std_f == 0.0:
        rev_growth_5y = np.broadcast_to(base_rev_growth[None, :], (valid_count, 5))
    else:
        d_growth = _sample_perturbation(
            z_growth, std=rev_growth_std_f, distribution=cfg.distribution
        )
        rev_growth_5y = np.clip(
            base_rev_growth[None, :] + d_growth[valid_mask, None],
            float(cfg.revenue_growth_bounds[0]),
            float(cfg.revenue_growth_bounds[1]),
        )

    if ebit_margin_std_f == 0.0:
        ebit_margin_5y = np.broadcast_to(base_ebit_margin[None, :], (valid_count, 5))
    else:
        d_margin = _sample_perturbation(
            z_margin, std=ebit_margin_std_f, distribution=cfg.distribution
        )
        ebit_margin_5y = np.clip(
            base_ebit_margin[None, :] + d_margin[valid_mask, None],
            float(cfg.ebit_margin_bounds[0]),
            float(cfg.ebit_margin_bounds[1]),
        )

    tax_5y = np.array([float(x) for x in assumptions.tax_rate], dtype=np.float64)
    da_pct_5y = np.array([float(x) for x in assumptions.da_pct_revenue], dtype=np.float64)
    sbc_pct_5y = np.array([float(x) for x in assumptions.sbc_pct_revenue], dtype=np.float64)
    capex_pct_5y = np.array([float(x) for x in assumptions.capex_pct_revenue], dtype=np.float64)
    nwc_pct_5y = np.array([float(x) for x in assumptions.nwc_pct_revenue], dtype=np.float64)

    base_revenue_f = float(latest.revenue)
    initial_nwc_f = float(initial_nwc)

    revenues = base_revenue_f * np.cumprod(1.0 + rev_growth_5y, axis=1)
    ebit = revenues * ebit_margin_5y
    nopat = ebit * (1.0 - tax_5y[None, :])
    da = revenues * da_pct_5y[None, :]
    capex = revenues * capex_pct_5y[None, :]
    nwc = revenues * nwc_pct_5y[None, :]

    prev_nwc = np.empty_like(nwc)
    prev_nwc[:, 0] = initial_nwc_f
    prev_nwc[:, 1:] = nwc[:, :-1]
    delta_nwc = nwc - prev_nwc

    if assumptions.add_back_sbc:
        sbc = revenues * sbc_pct_5y[None, :]
        fcff = nopat + da + sbc - capex - delta_nwc
    else:
        fcff = nopat + da - capex - delta_nwc

    periods_1d = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float64)
    if assumptions.discounting_convention == "mid_year":
        periods_1d = periods_1d - 0.5

    one_plus_wacc = 1.0 + wacc_valid
    discount_factors = np.power(one_plus_wacc[:, None], -periods_1d[None, :])
    pv_fcff = np.sum(fcff * discount_factors, axis=1)

    terminal_fcff = fcff[:, -1] * (1.0 + g_term_valid)
    undiscounted_tv = terminal_fcff / (wacc_valid - g_term_valid)
    pv_tv = undiscounted_tv * np.power(one_plus_wacc, -5.0)
    enterprise_value = pv_fcff + pv_tv

    debt_deducted = (
        latest.total_debt_incl_leases if assumptions.include_leases else latest.financial_debt
    )
    bridge_offset_f = float(
        latest.total_cash - debt_deducted - latest.noncontrolling_interest
    )
    raw_equity = enterprise_value + bridge_offset_f
    floored_mask = raw_equity < 0.0
    floored_count = int(np.count_nonzero(floored_mask))
    implied_equity = np.where(floored_mask, 0.0, raw_equity)
    per_share = implied_equity / float(shares_used)

    if floored_count > 0:
        notes.append(
            f"{floored_count} of {valid_count} valid scenarios had negative raw equity "
            "and were floored at $0.00 per share (limited liability)."
        )

    p10, p25, p50, p75, p90 = np.percentile(per_share, [10.0, 25.0, 50.0, 75.0, 90.0])

    prob_above_ref: Decimal | None = None
    if cfg.reference_share_price is not None:
        ref_f = float(cfg.reference_share_price)
        prob_val = float(np.mean(per_share > ref_f))
        prob_above_ref = _to_decimal(prob_val, quant=PROB_QUANT)
        notes.append(
            f"Neutral reference-price frequency: {prob_above_ref:.2%} of valid simulated scenarios "
            f"exceed reference share price ${cfg.reference_share_price:,.2f} under user assumptions "
            "(educational statistic only; not a Buy/Sell recommendation or target price)."
        )

    return MonteCarloResult(
        ticker=report.ticker,
        seed=cfg.seed,
        distribution=cfg.distribution,
        total_simulations=n_total,
        valid_simulations=valid_count,
        discarded_simulations=discarded_count,
        floored_at_zero_count=floored_count,
        min_wacc_g_spread=cfg.min_wacc_g_spread,
        include_leases=assumptions.include_leases,
        add_back_sbc=assumptions.add_back_sbc,
        discounting_convention=assumptions.discounting_convention,
        base_implied_value_per_share=base_per_share,
        mean_value_per_share=_to_decimal(float(np.mean(per_share))),
        std_value_per_share=_to_decimal(float(np.std(per_share, ddof=0))),
        min_value_per_share=_to_decimal(float(np.min(per_share))),
        max_value_per_share=_to_decimal(float(np.max(per_share))),
        p10_value_per_share=_to_decimal(float(p10)),
        p25_value_per_share=_to_decimal(float(p25)),
        p50_value_per_share=_to_decimal(float(p50)),
        p75_value_per_share=_to_decimal(float(p75)),
        p90_value_per_share=_to_decimal(float(p90)),
        reference_share_price=cfg.reference_share_price,
        prob_above_reference_price=prob_above_ref,
        simulated_values_per_share=tuple(float(v) for v in per_share.tolist()),
        notes=notes,
    )
