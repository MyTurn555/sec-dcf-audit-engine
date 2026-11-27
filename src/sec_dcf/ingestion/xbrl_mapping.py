"""US-GAAP / DEI XBRL concept mapping tables and deterministic derivation rules."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

PeriodKind = Literal["duration", "instant"]
UnitKind = Literal["USD", "shares"]


@dataclass(frozen=True)
class ConceptSpec:
    """Mapping specification for a canonical financial line item."""

    field_name: str
    concepts: tuple[str, ...]
    period_kind: PeriodKind
    unit: UnitKind = "USD"
    required: bool = True
    default_if_absent: Decimal | None = None
    window_before_days: int = 7
    window_after_days: int = 7


# Per-concept date matching window overrides relative to canonical period_end.
# dei:EntityCommonStockSharesOutstanding appears on the 10-K cover page, often
# 2-6 weeks after fiscal year-end.
CONCEPT_WINDOW_OVERRIDES: dict[str, tuple[int, int]] = {
    "dei:EntityCommonStockSharesOutstanding": (7, 45),
}


DURATION_SPECS: tuple[ConceptSpec, ...] = (
    ConceptSpec(
        field_name="revenue",
        concepts=(
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:Revenues",
            "us-gaap:SalesRevenueNet",
            "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
            "us-gaap:SalesRevenueGoodsNet",
            "us-gaap:SalesRevenueServicesNet",
        ),
        period_kind="duration",
        required=True,
    ),
    ConceptSpec(
        field_name="ebit",
        concepts=("us-gaap:OperatingIncomeLoss",),
        period_kind="duration",
        required=True,
    ),
    ConceptSpec(
        field_name="interest_expense",
        concepts=(
            "us-gaap:InterestExpense",
            "us-gaap:InterestExpenseDebt",
            "us-gaap:InterestAndDebtExpense",
        ),
        period_kind="duration",
        required=False,
        default_if_absent=Decimal("0"),
    ),
    ConceptSpec(
        field_name="pretax_income",
        concepts=(
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
        period_kind="duration",
        required=False,
    ),
    ConceptSpec(
        field_name="tax_expense",
        concepts=("us-gaap:IncomeTaxExpenseBenefit",),
        period_kind="duration",
        required=False,
    ),
    ConceptSpec(
        field_name="net_income",
        concepts=(
            "us-gaap:NetIncomeLoss",
            "us-gaap:ProfitLoss",
        ),
        period_kind="duration",
        required=True,
    ),
    ConceptSpec(
        field_name="diluted_shares",
        concepts=("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",),
        period_kind="duration",
        unit="shares",
        required=False,
    ),
    ConceptSpec(
        field_name="cfo",
        concepts=(
            "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        period_kind="duration",
        required=True,
    ),
    ConceptSpec(
        field_name="capex",
        concepts=(
            "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
            "us-gaap:PaymentsToAcquireProductiveAssets",
        ),
        period_kind="duration",
        required=True,
    ),
    ConceptSpec(
        field_name="sbc",
        concepts=(
            "us-gaap:ShareBasedCompensation",
            "us-gaap:AllocatedShareBasedCompensationExpense",
        ),
        period_kind="duration",
        required=False,
        default_if_absent=Decimal("0"),
    ),
)


INSTANT_SPECS: tuple[ConceptSpec, ...] = (
    ConceptSpec(
        field_name="cash_and_equivalents",
        concepts=(
            "us-gaap:CashAndCashEquivalentsAtCarryingValue",
            "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        period_kind="instant",
        required=True,
    ),
    ConceptSpec(
        field_name="short_term_investments",
        concepts=(
            "us-gaap:MarketableSecuritiesCurrent",
            "us-gaap:ShortTermInvestments",
            "us-gaap:AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        ),
        period_kind="instant",
        required=False,
        default_if_absent=Decimal("0"),
    ),
    ConceptSpec(
        field_name="current_assets",
        concepts=("us-gaap:AssetsCurrent",),
        period_kind="instant",
        required=False,
    ),
    ConceptSpec(
        field_name="total_assets",
        concepts=("us-gaap:Assets",),
        period_kind="instant",
        required=True,
    ),
    ConceptSpec(
        field_name="current_liabilities",
        concepts=("us-gaap:LiabilitiesCurrent",),
        period_kind="instant",
        required=False,
    ),
    ConceptSpec(
        field_name="stockholders_equity",
        concepts=(
            "us-gaap:StockholdersEquity",
            "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
        period_kind="instant",
        required=True,
    ),
    ConceptSpec(
        field_name="noncontrolling_interest",
        concepts=("us-gaap:MinorityInterest",),
        period_kind="instant",
        required=False,
        default_if_absent=Decimal("0"),
    ),
    ConceptSpec(
        field_name="ending_shares",
        concepts=(
            "us-gaap:CommonStockSharesOutstanding",
            "dei:EntityCommonStockSharesOutstanding",
        ),
        period_kind="instant",
        unit="shares",
        required=False,
    ),
)


# Specialized tag groups handled with anti-double-counting & derivation rules
DA_AGGREGATE_CONCEPTS: tuple[str, ...] = (
    "us-gaap:DepreciationDepletionAndAmortization",
    "us-gaap:DepreciationAndAmortization",
    "us-gaap:DepreciationAmortizationAndAccretionNet",
)
DEPRECIATION_CONCEPT = "us-gaap:Depreciation"
AMORTIZATION_INTANGIBLES_CONCEPT = "us-gaap:AmortizationOfIntangibleAssets"

SHORT_TERM_DEBT_AGGREGATE_CONCEPTS: tuple[str, ...] = (
    "us-gaap:DebtCurrent",
    "us-gaap:ShortTermBorrowings",
)
SHORT_TERM_DEBT_COMPONENT_CONCEPTS: tuple[str, ...] = (
    "us-gaap:CommercialPaper",
    "us-gaap:LongTermDebtCurrent",
)

LONG_TERM_DEBT_PRIMARY_CONCEPT = "us-gaap:LongTermDebtNoncurrent"
LONG_TERM_DEBT_AGGREGATE_FALLBACKS: tuple[str, ...] = (
    "us-gaap:LongTermDebt",
    "us-gaap:LongTermDebtAndCapitalLeaseObligations",
)

OPERATING_LEASE_CURRENT_CONCEPT = "us-gaap:OperatingLeaseLiabilityCurrent"
OPERATING_LEASE_NONCURRENT_CONCEPT = "us-gaap:OperatingLeaseLiabilityNoncurrent"
OPERATING_LEASE_TOTAL_FALLBACK = "us-gaap:OperatingLeaseLiability"
FINANCE_LEASE_CURRENT_CONCEPT = "us-gaap:FinanceLeaseLiabilityCurrent"
FINANCE_LEASE_NONCURRENT_CONCEPT = "us-gaap:FinanceLeaseLiabilityNoncurrent"
FINANCE_LEASE_TOTAL_FALLBACK = "us-gaap:FinanceLeaseLiability"

LIABILITIES_DIRECT_CONCEPT = "us-gaap:Liabilities"
LIABILITIES_CURRENT_CONCEPT = "us-gaap:LiabilitiesCurrent"
LIABILITIES_NONCURRENT_CONCEPT = "us-gaap:LiabilitiesNoncurrent"

EQUITY_INCLUDES_NCI_CONCEPT = (
    "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
)
CASH_INCLUDES_RESTRICTED_CONCEPT = (
    "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
)

# Concepts used to discover canonical annual 10-K fiscal period_end dates
ANCHOR_PERIOD_CONCEPTS: tuple[str, ...] = (
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:Revenues",
    "us-gaap:SalesRevenueNet",
    "us-gaap:NetIncomeLoss",
    "us-gaap:NetCashProvidedByUsedInOperatingActivities",
)


def normalize_concept_name(concept: str) -> str:
    """Return canonical taxonomy-prefixed concept name if already prefixed."""
    if ":" in concept:
        return concept
    if concept.startswith("Entity"):
        return f"dei:{concept}"
    return f"us-gaap:{concept}"
