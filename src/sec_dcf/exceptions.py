"""Custom domain exceptions for SEC DCF Audit Engine."""


class SecDcfError(Exception):
    """Base exception for all sec_dcf errors."""


class EdgarIdentityError(SecDcfError):
    """Raised when EDGAR_IDENTITY is missing or invalid."""


class UnsupportedCompanyTypeError(SecDcfError):
    """Raised when attempting to ingest a financial institution outside module scope."""


class DataFetchError(SecDcfError):
    """Raised when fetching data from SEC EDGAR fails or returns no facts."""


class MissingCriticalConceptError(SecDcfError):
    """Raised when a required financial line item cannot be resolved or derived."""


class AccountingValidationError(SecDcfError):
    """Raised in strict validation mode when accounting integrity checks fail."""


class FredApiError(SecDcfError):
    """Raised when FRED API configuration, request, or payload parsing fails."""


class WaccCalculationError(SecDcfError):
    """Raised when WACC inputs or capital structure weights are invalid."""


class DcfValuationError(SecDcfError):
    """Raised when DCF assumptions or valuation invariants fail (e.g. WACC <= g)."""


class GeminiApiError(SecDcfError):
    """Raised when GEMINI_API_KEY configuration, Gemini API call, or response parsing fails."""


class QualitativeAuditError(SecDcfError):
    """Raised in strict qualitative audit mode when quote verification or compliance fails."""
