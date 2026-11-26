"""SEC DCF Audit Engine — deterministic financial ingestion and accounting validation."""

from sec_dcf.exceptions import (
    AccountingValidationError,
    DataFetchError,
    EdgarIdentityError,
    MissingCriticalConceptError,
    SecDcfError,
    UnsupportedCompanyTypeError,
)
from sec_dcf.ingestion.edgar_client import EdgarClient, fetch_company_financials
from sec_dcf.ingestion.normalizer import normalize_entity_facts
from sec_dcf.models.financials import (
    AnnualFinancialPeriod,
    AuditSourceKind,
    CompanyFinancialsReport,
    LineItemAudit,
    ValidationIssue,
)
from sec_dcf.validation.accounting import validate_financials

__all__ = [
    "AccountingValidationError",
    "AnnualFinancialPeriod",
    "AuditSourceKind",
    "CompanyFinancialsReport",
    "DataFetchError",
    "EdgarClient",
    "EdgarIdentityError",
    "LineItemAudit",
    "MissingCriticalConceptError",
    "SecDcfError",
    "UnsupportedCompanyTypeError",
    "ValidationIssue",
    "fetch_company_financials",
    "normalize_entity_facts",
    "validate_financials",
]
