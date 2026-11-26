"""Canonical Pydantic v2 models for Module 3: auditable 10-K qualitative analysis."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SectionName = Literal["Item 1A", "Item 7"]
RiskCategory = Literal[
    "macroeconomic",
    "operational",
    "regulatory",
    "competitive",
    "financial_liquidity",
    "cybersecurity_technology",
    "legal_governance",
    "supply_chain",
    "other",
]
MaterialityLevel = Literal["high", "medium", "low"]
ManagementTone = Literal["positive", "neutral", "cautious", "mixed"]

EDUCATIONAL_DISCLAIMER = (
    "Educational & research use only. This qualitative audit summarizes disclosures "
    "from SEC Form 10-K Item 1A and Item 7 without financial calculations, price targets, "
    "or buy/sell investment recommendations."
)


class SectionText(BaseModel):
    """Extracted 10-K section preserving full raw text for quote verification."""

    model_config = ConfigDict(frozen=True)

    section: SectionName
    raw_text: str
    prompt_text: str
    original_char_count: int = Field(ge=0)
    sent_char_count: int = Field(ge=0)
    truncated: bool = False


class TenKSectionsDocument(BaseModel):
    """Container for extracted 10-K Item 1A (Risk Factors) and Item 7 (MD&A) sections."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    company_name: str
    cik: str | None = None
    filing_date: date | None = None
    period_of_report: date | None = None
    accession_number: str | None = None
    form: str = "10-K"
    item_1a: SectionText | None = None
    item_7: SectionText | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_at_least_one_section(self) -> TenKSectionsDocument:
        """Require at least one of Item 1A or Item 7 to be present."""
        if self.item_1a is None and self.item_7 is None:
            raise ValueError("At least one of item_1a or item_7 must be provided.")
        return self

    def get_section(self, section: SectionName) -> SectionText | None:
        """Return the SectionText corresponding to 'Item 1A' or 'Item 7'."""
        if section == "Item 1A":
            return self.item_1a
        if section == "Item 7":
            return self.item_7
        return None


# ---------------------------------------------------------------------------
# LLM Structured Output schema (passed to google-genai response_schema)
# ---------------------------------------------------------------------------


class ExtractedRiskItem(BaseModel):
    """Single risk factor extracted by Gemini with verbatim 10-K citation."""

    model_config = ConfigDict(frozen=True)

    title: str
    category: RiskCategory
    materiality: MaterialityLevel
    summary: str
    section: SectionName
    verbatim_quote: str


class ExtractedGrowthDriver(BaseModel):
    """Growth driver or management commentary extracted by Gemini with verbatim citation."""

    model_config = ConfigDict(frozen=True)

    title: str
    summary: str
    management_tone: ManagementTone
    section: SectionName
    verbatim_quote: str


class ExtractedRedFlag(BaseModel):
    """Potential qualitative red flag grounded in 10-K disclosures."""

    model_config = ConfigDict(frozen=True)

    title: str
    severity: MaterialityLevel
    rationale: str
    section: SectionName
    verbatim_quote: str


class QualitativeExtractionPayload(BaseModel):
    """Structured output contract returned by Gemini (unverified until Python audit)."""

    model_config = ConfigDict(frozen=True)

    executive_summary: str
    risks: list[ExtractedRiskItem] = Field(default_factory=list)
    growth_drivers: list[ExtractedGrowthDriver] = Field(default_factory=list)
    red_flags: list[ExtractedRedFlag] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic Audit Output models (produced solely by Python verifier)
# ---------------------------------------------------------------------------


class AuditedRiskItem(ExtractedRiskItem):
    """Risk item augmented with deterministic Python quote verification status."""

    quote_verified: bool
    verification_note: str | None = None


class AuditedGrowthDriver(ExtractedGrowthDriver):
    """Growth driver augmented with deterministic Python quote verification status."""

    quote_verified: bool
    verification_note: str | None = None


class AuditedRedFlag(ExtractedRedFlag):
    """Red flag augmented with deterministic Python quote verification status."""

    quote_verified: bool
    verification_note: str | None = None


class ComplianceFlag(BaseModel):
    """Deterministic compliance scan hit for prohibited investment advice or targets."""

    model_config = ConfigDict(frozen=True)

    field_path: str
    matched_pattern: str
    excerpt: str


class QualitativeAuditReport(BaseModel):
    """Final auditable qualitative 10-K analysis report with quote verification stats."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    company_name: str
    filing_date: date | None = None
    period_of_report: date | None = None
    accession_number: str | None = None
    model_name: str
    executive_summary: str
    risks: list[AuditedRiskItem] = Field(default_factory=list)
    growth_drivers: list[AuditedGrowthDriver] = Field(default_factory=list)
    red_flags: list[AuditedRedFlag] = Field(default_factory=list)
    total_claims: int = Field(ge=0)
    verified_claims: int = Field(ge=0)
    verified_quote_ratio: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    compliance_flags: list[ComplianceFlag] = Field(default_factory=list)
    section_truncation_status: dict[SectionName, bool] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    educational_disclaimer: str = EDUCATIONAL_DISCLAIMER
