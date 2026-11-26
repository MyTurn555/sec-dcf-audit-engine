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
