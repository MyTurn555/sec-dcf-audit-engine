"""SEC EDGAR client wrapper using edgartools with identity & rate-limit guards."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from dotenv import load_dotenv
from edgar import Company, set_identity

from sec_dcf.exceptions import (
    DataFetchError,
    EdgarIdentityError,
    UnsupportedCompanyTypeError,
)
from sec_dcf.ingestion.normalizer import normalize_entity_facts
from sec_dcf.models.financials import CompanyFinancialsReport
from sec_dcf.validation.accounting import validate_financials


class _RateLimiter:
    """Thread-safe minimum-interval limiter staying safely below SEC's 10 req/s cap."""

    def __init__(self, max_requests_per_second: float = 8.0) -> None:
        if max_requests_per_second <= 0 or max_requests_per_second > 10.0:
            raise ValueError("max_requests_per_second must be in (0, 10.0]")
        self._min_interval = 1.0 / max_requests_per_second
        self._lock = threading.Lock()
        self._last_call_ts = 0.0

    def wait(self) -> None:
        """Sleep if needed to respect the configured SEC request interval."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_ts
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call_ts = time.monotonic()


def _is_financial_sic(sic: str | int | None) -> bool:
    """Check whether SIC code falls in Finance, Insurance, and Real Estate (6000-6999)."""
    if sic is None:
        return False
    try:
        sic_num = int(str(sic).strip())
    except ValueError:
        return False
    return 6000 <= sic_num <= 6999


class EdgarClient:
    """Deterministic SEC EDGAR ingestion client backed by edgartools."""

    def __init__(
        self,
        identity: str | None = None,
        max_requests_per_second: float = 8.0,
        load_env: bool = True,
    ) -> None:
        if load_env:
            load_dotenv(override=False)

        resolved_identity = (identity or os.getenv("EDGAR_IDENTITY") or "").strip()
        if not resolved_identity or "@" not in resolved_identity:
            raise EdgarIdentityError(
                "EDGAR_IDENTITY must be set (e.g. 'AppName contact@domain.com') "
                "before querying SEC EDGAR."
            )
        set_identity(resolved_identity)
        self._rate_limiter = _RateLimiter(max_requests_per_second=max_requests_per_second)

    def fetch_raw_company_and_facts(self, ticker: str) -> tuple[Company, Any]:
        """Fetch Company metadata and EntityFacts from SEC EDGAR, blocking financial firms."""
        clean_ticker = ticker.strip().upper()
        if not clean_ticker:
            raise DataFetchError("Ticker symbol must not be empty.")

        self._rate_limiter.wait()
        try:
            company = Company(clean_ticker)
        except Exception as exc:
            raise DataFetchError(f"Failed to load SEC company for {clean_ticker}: {exc}") from exc

        if getattr(company, "not_found", False):
            raise DataFetchError(f"Ticker {clean_ticker} was not found in SEC EDGAR.")

        if company.is_financial_institution() or _is_financial_sic(company.sic):
            raise UnsupportedCompanyTypeError(
                f"{clean_ticker} ({company.name}, SIC={company.sic}) is a financial "
                "institution; this module supports non-financial operating companies only."
            )

        self._rate_limiter.wait()
        try:
            facts = company.get_facts()
        except Exception as exc:
            raise DataFetchError(f"Failed to fetch facts for {clean_ticker}: {exc}") from exc

        if facts is None:
            raise DataFetchError(f"No XBRL facts returned by SEC EDGAR for {clean_ticker}.")

        return company, facts

    def fetch_company_financials(
        self,
        ticker: str,
        years: int = 5,
        strict: bool = False,
    ) -> CompanyFinancialsReport:
        """Fetch, normalize, and validate annual 10-K financials for a non-financial company."""
        company, facts = self.fetch_raw_company_and_facts(ticker)
        report = normalize_entity_facts(
            facts=facts,
            ticker=ticker.strip().upper(),
            sic=str(company.sic) if company.sic is not None else None,
            fiscal_year_end=str(company.fiscal_year_end)
            if company.fiscal_year_end is not None
            else None,
            years=years,
            strict=strict,
        )
        return validate_financials(report, strict=strict, expected_years=years)


def fetch_company_financials(
    ticker: str,
    years: int = 5,
    strict: bool = False,
    identity: str | None = None,
) -> CompanyFinancialsReport:
    """Convenience entrypoint to fetch and validate annual 10-K financials."""
    client = EdgarClient(identity=identity)
    return client.fetch_company_financials(ticker=ticker, years=years, strict=strict)
