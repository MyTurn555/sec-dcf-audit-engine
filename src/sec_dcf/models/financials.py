"""Canonical Pydantic v2 models for annual 10-K financial statements."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

AuditSourceKind = Literal[
    "tag_exact",
    "tag_fallback",
    "derived",
    "derived_from_assets",
    "zero_if_absent",
    "missing",
]

ValidationSeverity = Literal["error", "warning"]


class LineItemAudit(BaseModel):
    """Audit trail describing the exact XBRL source or derivation for a line item."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    concept: str | None = None
    label: str | None = None
    raw_value: Decimal | None = None
    raw_unit: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    filing_date: date | None = None
    form_type: str | None = None
    accession: str | None = None
    source_kind: AuditSourceKind
    note: str | None = None


class ValidationIssue(BaseModel):
    """Deterministic accounting or data-quality check finding."""

    model_config = ConfigDict(frozen=True)

    code: str
    severity: ValidationSeverity
    fiscal_year: int | None = None
    message: str
    details: dict[str, str] = Field(default_factory=dict)


class AnnualFinancialPeriod(BaseModel):
    """Normalized annual financial statement slice for one fiscal year (USD / shares)."""

    model_config = ConfigDict(frozen=True)

    # Period metadata
    fiscal_year: int = Field(ge=1900, le=2100)
    period_start: date | None = None
    period_end: date
    filing_date: date | None = None
    accession: str | None = None
    form_type: str = "10-K"

    # Income Statement (duration, USD unless noted)
    revenue: Decimal
    ebit: Decimal
    da: Decimal
    interest_expense: Decimal = Decimal("0")
    pretax_income: Decimal | None = None
    tax_expense: Decimal | None = None
    net_income: Decimal
    diluted_shares: Decimal | None = None

    # Balance Sheet (instant, USD unless noted)
    cash_and_equivalents: Decimal
    short_term_investments: Decimal = Decimal("0")
    current_assets: Decimal | None = None
    total_assets: Decimal
    current_liabilities: Decimal | None = None
    short_term_debt: Decimal = Decimal("0")
    long_term_debt: Decimal = Decimal("0")
    current_lease_liabilities: Decimal = Decimal("0")
    noncurrent_lease_liabilities: Decimal = Decimal("0")
    total_liabilities: Decimal
    stockholders_equity: Decimal
    noncontrolling_interest: Decimal = Decimal("0")
    equity_includes_nci: bool = False
    ending_shares: Decimal | None = None

    # Cash Flow Statement (duration, USD)
    cfo: Decimal
    capex: Decimal

    # Full field-level lineage
    audit_trail: dict[str, LineItemAudit] = Field(default_factory=dict)

    @field_validator("capex")
    @classmethod
    def validate_capex_non_negative(cls, value: Decimal) -> Decimal:
        """CapEx must be normalized to a non-negative cash outflow amount."""
        if value < Decimal("0"):
            raise ValueError(f"capex must be non-negative after normalization, got {value}")
        return value

    @computed_field
    @property
    def total_cash(self) -> Decimal:
        """Cash and cash equivalents plus short-term marketable securities."""
        return self.cash_and_equivalents + self.short_term_investments

    @computed_field
    @property
    def financial_debt(self) -> Decimal:
        """Total interest-bearing financial debt excluding lease liabilities."""
        return self.short_term_debt + self.long_term_debt

    @computed_field
    @property
    def lease_liabilities(self) -> Decimal:
        """Total operating and finance lease liabilities (current + noncurrent)."""
        return self.current_lease_liabilities + self.noncurrent_lease_liabilities

    @computed_field
    @property
    def total_debt_incl_leases(self) -> Decimal:
        """Interest-bearing debt plus total lease liabilities."""
        return self.financial_debt + self.lease_liabilities

    @computed_field
    @property
    def total_equity_for_balance_check(self) -> Decimal:
        """Total equity avoiding double-counting NCI when equity already includes it."""
        if self.equity_includes_nci:
            return self.stockholders_equity
        return self.stockholders_equity + self.noncontrolling_interest

    @computed_field
    @property
    def non_cash_nwc(self) -> Decimal | None:
        """Operating net working capital excluding cash, short-term debt, and current leases."""
        if self.current_assets is None or self.current_liabilities is None:
            return None
        operating_current_assets = self.current_assets - self.total_cash
        operating_current_liabilities = (
            self.current_liabilities
            - self.short_term_debt
            - self.current_lease_liabilities
        )
        return operating_current_assets - operating_current_liabilities

    @computed_field
    @property
    def fcf_simple(self) -> Decimal:
        """Simple Free Cash Flow: Operating Cash Flow minus normalized CapEx."""
        return self.cfo - self.capex


class CompanyFinancialsReport(BaseModel):
    """Complete multi-year normalized and audited financial report for a company."""

    ticker: str
    cik: str
    company_name: str
    sic: str | None = None
    fiscal_year_end: str | None = None
    currency: str = "USD"
    periods: list[AnnualFinancialPeriod] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)

    @computed_field
    @property
    def is_valid(self) -> bool:
        """True if at least one period exists and no error-severity issues were found."""
        return bool(self.periods) and not any(
            issue.severity == "error" for issue in self.validation_issues
        )
