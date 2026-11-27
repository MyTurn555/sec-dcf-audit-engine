"""Canonical Pydantic v2 models for Module 2: ratios, WACC, DCF, and sensitivity."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)

DiscountingConvention = Literal["end_of_year", "mid_year"]
NopatTaxSource = Literal["effective", "clamped", "fallback_loss_or_missing"]
RoicBasis = Literal["average", "ending_fallback", "not_meaningful"]
EquityWeightSource = Literal["market_cap_override", "share_price_x_shares", "book_equity_fallback"]
CostOfDebtSource = Literal[
    "override",
    "implied_from_financial_debt",
    "rf_plus_spread",
    "zero_debt",
]
WaccTaxRateSource = Literal["override", "latest_nopat_tax_rate"]
InitialNwcSource = Literal["override", "historical_balance_sheet", "model_pct_fallback"]
SharesSource = Literal["override", "diluted_shares", "ending_shares"]


def _coerce_five_year_series(value: Any) -> Any:
    """Expand a scalar Decimal-like value into a 5-year tuple, or validate length 5."""
    if isinstance(value, bool):
        raise ValueError("Boolean value is not valid for a financial assumption.")
    if isinstance(value, (list, tuple)):
        if len(value) != 5:
            raise ValueError(
                f"5-year assumption sequence must contain exactly 5 values, got {len(value)}."
            )
        return tuple(value)
    return (value, value, value, value, value)


FiveYearSeries = Annotated[
    tuple[Decimal, Decimal, Decimal, Decimal, Decimal],
    BeforeValidator(_coerce_five_year_series),
]


class AnnualMetrics(BaseModel):
    """Deterministic historical financial ratios and capital efficiency for one fiscal year."""

    model_config = ConfigDict(frozen=True)

    fiscal_year: int = Field(ge=1900, le=2100)
    period_end: date

    revenue: Decimal
    ebit: Decimal
    ebitda: Decimal
    net_income: Decimal

    # Profitability margins (None when revenue <= 0)
    ebit_margin: Decimal | None = None
    ebitda_margin: Decimal | None = None
    net_margin: Decimal | None = None

    # Tax & NOPAT
    raw_effective_tax_rate: Decimal | None = None
    nopat_tax_rate: Decimal
    nopat_tax_rate_source: NopatTaxSource
    nopat: Decimal

    # Invested Capital & ROIC
    include_leases: bool
    invested_capital_excl_leases: Decimal
    invested_capital_incl_leases: Decimal
    invested_capital: Decimal
    average_invested_capital: Decimal | None = None
    roic_basis: RoicBasis
    roic: Decimal | None = None

    # Cash conversion & capital intensity
    fcf_simple: Decimal
    sbc: Decimal = Decimal("0")
    fcf_ex_sbc: Decimal | None = None
    sbc_to_revenue: Decimal | None = None
    sbc_to_cfo: Decimal | None = None
    cash_conversion: Decimal | None = None
    capex_to_revenue: Decimal | None = None
    da_to_revenue: Decimal | None = None
    non_cash_nwc: Decimal | None = None
    non_cash_nwc_to_revenue: Decimal | None = None

    # Leverage & coverage
    net_debt_excl_leases: Decimal
    net_debt_incl_leases: Decimal
    net_debt_to_ebitda_excl_leases: Decimal | None = None
    net_debt_to_ebitda_incl_leases: Decimal | None = None
    interest_coverage: Decimal | None = None

    notes: list[str] = Field(default_factory=list)


class HistoricalMetricsReport(BaseModel):
    """Multi-year historical financial ratios panel derived from CompanyFinancialsReport."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    company_name: str
    include_leases: bool
    default_tax_rate: Decimal
    max_tax_rate: Decimal
    periods: list[AnnualMetrics]

    @property
    def latest_period(self) -> AnnualMetrics:
        """Return the most recent chronological period."""
        if not self.periods:
            raise ValueError("HistoricalMetricsReport contains no periods.")
        return self.periods[-1]


class RiskFreeRateQuote(BaseModel):
    """Risk-free rate observation (e.g. FRED DGS10 or manual override)."""

    model_config = ConfigDict(frozen=True)

    series_id: str = "DGS10"
    rate: Decimal
    observation_date: date | None = None
    source: Literal["fred", "manual"]


class WaccInputs(BaseModel):
    """Explicit inputs for deterministic CAPM + WACC calculation."""

    model_config = ConfigDict(frozen=True)

    risk_free_rate: Decimal | None = Field(default=None, ge=Decimal("-0.05"), le=Decimal("0.50"))
    beta: Decimal = Field(gt=Decimal("0"))
    equity_risk_premium: Decimal = Field(ge=Decimal("0"), le=Decimal("0.30"))
    share_price: Decimal | None = Field(default=None, gt=Decimal("0"))
    market_cap_override: Decimal | None = Field(default=None, gt=Decimal("0"))
    allow_book_equity_fallback: bool = False
    pretax_cost_of_debt_override: Decimal | None = Field(default=None, ge=Decimal("0"))
    default_credit_spread: Decimal = Field(default=Decimal("0.015"), ge=Decimal("0"))
    tax_rate: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    include_leases: bool = False
    shares_override: Decimal | None = Field(default=None, gt=Decimal("0"))


class WaccResult(BaseModel):
    """Full deterministic breakdown of CAPM, cost of debt, capital weights, and WACC."""

    model_config = ConfigDict(frozen=True)

    risk_free_rate: RiskFreeRateQuote
    cost_of_equity: Decimal
    pretax_cost_of_debt: Decimal
    cost_of_debt_source: CostOfDebtSource
    tax_rate: Decimal
    tax_rate_source: WaccTaxRateSource
    after_tax_cost_of_debt: Decimal
    equity_value_for_weights: Decimal
    equity_weight_source: EquityWeightSource
    debt_value_for_weights: Decimal
    include_leases: bool
    total_capital: Decimal
    equity_weight: Decimal
    debt_weight: Decimal
    wacc: Decimal
    notes: list[str] = Field(default_factory=list)


class DcfAssumptions(BaseModel):
    """Explicit 5-year FCFF/DCF projection assumptions (scalar or 5-year path)."""

    model_config = ConfigDict(frozen=True)

    revenue_growth: FiveYearSeries
    ebit_margin: FiveYearSeries
    tax_rate: FiveYearSeries
    da_pct_revenue: FiveYearSeries
    capex_pct_revenue: FiveYearSeries
    nwc_pct_revenue: FiveYearSeries
    sbc_pct_revenue: FiveYearSeries = (Decimal("0"),) * 5
    add_back_sbc: bool = False
    terminal_growth_rate: Decimal = Field(gt=Decimal("-1"), lt=Decimal("1"))
    discounting_convention: DiscountingConvention = "mid_year"
    include_leases: bool = False
    exit_ebitda_multiple: Decimal | None = Field(default=None, gt=Decimal("0"))
    initial_nwc_override: Decimal | None = None
    shares_override: Decimal | None = Field(default=None, gt=Decimal("0"))

    @field_validator("revenue_growth")
    @classmethod
    def validate_revenue_growth(
        cls, values: tuple[Decimal, Decimal, Decimal, Decimal, Decimal]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        """Revenue growth cannot be <= -100%."""
        for val in values:
            if val <= Decimal("-1"):
                raise ValueError(f"revenue_growth must be > -1.0, got {val}")
        return values

    @field_validator("ebit_margin")
    @classmethod
    def validate_ebit_margin(
        cls, values: tuple[Decimal, Decimal, Decimal, Decimal, Decimal]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        """EBIT margin cannot exceed 100%."""
        for val in values:
            if val > Decimal("1"):
                raise ValueError(f"ebit_margin must be <= 1.0, got {val}")
        return values

    @field_validator("tax_rate")
    @classmethod
    def validate_tax_rate(
        cls, values: tuple[Decimal, Decimal, Decimal, Decimal, Decimal]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        """Effective projection tax rate must lie in [0, 1]."""
        for val in values:
            if val < Decimal("0") or val > Decimal("1"):
                raise ValueError(f"tax_rate must be between 0 and 1, got {val}")
        return values

    @field_validator("da_pct_revenue", "capex_pct_revenue", "sbc_pct_revenue")
    @classmethod
    def validate_non_negative_pct(
        cls, values: tuple[Decimal, Decimal, Decimal, Decimal, Decimal]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        """D&A, CapEx, and SBC percentages of revenue must be non-negative."""
        for val in values:
            if val < Decimal("0"):
                raise ValueError(f"Percentage of revenue must be >= 0, got {val}")
        return values


class ProjectedYearCashFlow(BaseModel):
    """Single projected forecast year (t = 1..5) in the FCFF model."""

    model_config = ConfigDict(frozen=True)

    year_index: int = Field(ge=1, le=5)
    fiscal_year: int
    revenue: Decimal
    ebit: Decimal
    tax_rate: Decimal
    nopat: Decimal
    da: Decimal
    sbc: Decimal = Decimal("0")
    capex: Decimal
    nwc: Decimal
    delta_nwc: Decimal
    fcff: Decimal
    ebitda: Decimal
    discount_period: Decimal
    discount_factor: Decimal
    pv_fcff: Decimal


class TerminalValueSummary(BaseModel):
    """Gordon Growth terminal value and optional EV/EBITDA exit cross-check."""

    model_config = ConfigDict(frozen=True)

    method: Literal["gordon_growth"] = "gordon_growth"
    terminal_fcff: Decimal
    terminal_growth_rate: Decimal
    wacc: Decimal
    undiscounted_tv: Decimal
    tv_discount_period: Decimal
    pv_terminal_value: Decimal
    tv_share_of_ev: Decimal | None = None
    implied_exit_ev_ebitda: Decimal | None = None
    exit_multiple_crosscheck_tv: Decimal | None = None
    exit_multiple_crosscheck_pv_tv: Decimal | None = None
    exit_multiple_crosscheck_ev: Decimal | None = None
    exit_multiple_crosscheck_value_per_share: Decimal | None = None


class EnterpriseToEquityBridge(BaseModel):
    """Deterministic bridge from Enterprise Value to Implied Equity Value per Share."""

    model_config = ConfigDict(frozen=True)

    enterprise_value: Decimal
    debt_deducted: Decimal
    lease_liabilities_included: Decimal
    noncontrolling_interest_deducted: Decimal
    total_cash_added: Decimal
    raw_implied_equity_value: Decimal
    implied_equity_value: Decimal
    equity_floored_at_zero: bool
    diluted_shares_used: Decimal
    shares_source: SharesSource
    raw_implied_value_per_share: Decimal
    implied_value_per_share: Decimal
    include_leases: bool


class DcfValuationResult(BaseModel):
    """Complete 5-year FCFF/DCF valuation output (educational/research only)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    base_fiscal_year: int
    base_revenue: Decimal
    initial_nwc: Decimal
    initial_nwc_source: InitialNwcSource
    wacc: Decimal
    include_leases: bool
    add_back_sbc: bool = False
    discounting_convention: DiscountingConvention
    projections: list[ProjectedYearCashFlow]
    pv_projected_fcff: Decimal
    terminal_value: TerminalValueSummary
    bridge: EnterpriseToEquityBridge
    sbc_valuation_impact_ev: Decimal = Decimal("0")
    sbc_valuation_impact_per_share: Decimal = Decimal("0")
    notes: list[str] = Field(default_factory=list)


class SensitivityCell(BaseModel):
    """One cell of the 2D (WACC x terminal_growth_rate) sensitivity grid."""

    model_config = ConfigDict(frozen=True)

    wacc: Decimal
    terminal_growth_rate: Decimal
    is_valid: bool
    enterprise_value: Decimal | None = None
    implied_equity_value: Decimal | None = None
    implied_value_per_share: Decimal | None = None
    error_reason: str | None = None


class SensitivityMatrix(BaseModel):
    """2D sensitivity matrix producing an implied value-per-share range."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    wacc_values: list[Decimal]
    terminal_growth_values: list[Decimal]
    cells: list[list[SensitivityCell]]
    min_implied_value_per_share: Decimal | None = None
    max_implied_value_per_share: Decimal | None = None
    base_wacc: Decimal
    base_terminal_growth_rate: Decimal


class MultiplesSnapshot(BaseModel):
    """Valuation multiples and cash yields for one valuation basis (Market or DCF)."""

    model_config = ConfigDict(frozen=True)

    ev_to_revenue: Decimal | None = None
    ev_to_ebitda: Decimal | None = None
    ev_to_ebit: Decimal | None = None
    pe_ratio: Decimal | None = None
    reported_fcf_yield: Decimal | None = None
    true_fcf_yield_ex_sbc: Decimal | None = None


class MarketMultiplesComparison(BaseModel):
    """Deterministic comparison of Current Market Multiples vs. Implied DCF Multiples."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    fiscal_year: int
    include_leases: bool
    shares_used: Decimal | None = None
    shares_source: SharesSource | None = None
    share_price_used: Decimal | None = None
    current_market_cap: Decimal | None = None
    net_debt: Decimal
    noncontrolling_interest: Decimal
    current_enterprise_value: Decimal | None = None
    dcf_enterprise_value: Decimal | None = None
    dcf_implied_equity_value: Decimal | None = None
    current: MultiplesSnapshot
    implied_dcf: MultiplesSnapshot
    notes: list[str] = Field(default_factory=list)
