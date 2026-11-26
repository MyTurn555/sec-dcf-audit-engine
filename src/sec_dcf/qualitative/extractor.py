"""SEC 10-K section text extraction (Item 1A & Item 7) via edgartools or offline fixtures."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from edgar import Company, set_identity

from sec_dcf.exceptions import DataFetchError
from sec_dcf.ingestion.edgar_client import (
    RateLimiter,
    ensure_non_financial_company,
    get_shared_rate_limiter,
    resolve_edgar_identity,
)
from sec_dcf.models.qualitative import SectionName, SectionText, TenKSectionsDocument

DEFAULT_MAX_CHARS_PER_SECTION = 60_000


def _coerce_optional_date(value: Any) -> date | None:
    """Safely convert date/datetime/ISO string from edgartools metadata into date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return date.fromisoformat(cleaned[:10])
        except ValueError:
            return None
    return None


def _extract_tenk_item_text(tenk: Any, *, attr_name: str, item_key: str) -> str | None:
    """
    Safely extract Item 1A or Item 7 text from an edgartools TenK object.

    Tries friendly property first (`risk_factors` / `management_discussion`) and falls
    back to indexing (`tenk["Item 1A"]` / `tenk["Item 7"]`), normalizing to `str`.
    """
    candidate: Any = None
    try:
        candidate = getattr(tenk, attr_name, None)
    except Exception:  # noqa: BLE001
        candidate = None

    if candidate is None:
        try:
            candidate = tenk[item_key]
        except Exception:  # noqa: BLE001
            candidate = None

    if candidate is None:
        return None

    text_attr = getattr(candidate, "text", None)
    if callable(text_attr):
        try:
            candidate = text_attr()
        except Exception:  # noqa: BLE001
            return None

    if candidate is None:
        return None

    text = candidate if isinstance(candidate, str) else str(candidate)
    return text if text.strip() else None


def build_section_text(
    section: SectionName,
    raw_text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS_PER_SECTION,
) -> SectionText:
    """
    Build a SectionText object preserving full raw_text while truncating prompt_text.

    Truncation happens on a whitespace boundary when possible so LLM prompts stay within
    cost/token budgets without losing the full source text for quote verification.
    """
    if max_chars <= 0:
        raise ValueError(f"max_chars must be positive, got {max_chars}.")

    original_len = len(raw_text)
    if original_len <= max_chars:
        return SectionText(
            section=section,
            raw_text=raw_text,
            prompt_text=raw_text,
            original_char_count=original_len,
            sent_char_count=original_len,
            truncated=False,
        )

    slice_candidate = raw_text[:max_chars]
    last_space = slice_candidate.rfind(" ")
    last_newline = slice_candidate.rfind("\n")
    boundary = max(last_space, last_newline)
    if boundary >= max_chars // 2:
        prompt_text = slice_candidate[:boundary].rstrip()
    else:
        prompt_text = slice_candidate.rstrip()

    return SectionText(
        section=section,
        raw_text=raw_text,
        prompt_text=prompt_text,
        original_char_count=original_len,
        sent_char_count=len(prompt_text),
        truncated=True,
    )


def build_sections_from_texts(
    *,
    ticker: str,
    company_name: str,
    item_1a_text: str | None = None,
    item_7_text: str | None = None,
    cik: str | None = None,
    filing_date: date | str | None = None,
    period_of_report: date | str | None = None,
    accession_number: str | None = None,
    form: str = "10-K",
    max_chars_per_section: int = DEFAULT_MAX_CHARS_PER_SECTION,
) -> TenKSectionsDocument:
    """Construct a TenKSectionsDocument from raw Item 1A / Item 7 strings (offline-safe)."""
    clean_ticker = ticker.strip().upper()
    if not clean_ticker:
        raise DataFetchError("Ticker symbol must not be empty.")

    clean_1a = item_1a_text if item_1a_text and item_1a_text.strip() else None
    clean_7 = item_7_text if item_7_text and item_7_text.strip() else None

    if clean_1a is None and clean_7 is None:
        raise DataFetchError(
            f"Neither Item 1A (Risk Factors) nor Item 7 (MD&A) contained text for {clean_ticker}."
        )

    warnings: list[str] = []
    section_1a: SectionText | None = None
    section_7: SectionText | None = None

    if clean_1a is not None:
        section_1a = build_section_text("Item 1A", clean_1a, max_chars=max_chars_per_section)
        if section_1a.truncated:
            warnings.append(
                f"SECTION_TRUNCATED: Item 1A truncated from {section_1a.original_char_count} "
                f"to {section_1a.sent_char_count} characters for LLM prompt."
            )
    else:
        warnings.append("MISSING_SECTION: Item 1A (Risk Factors) is missing or empty.")

    if clean_7 is not None:
        section_7 = build_section_text("Item 7", clean_7, max_chars=max_chars_per_section)
        if section_7.truncated:
            warnings.append(
                f"SECTION_TRUNCATED: Item 7 truncated from {section_7.original_char_count} "
                f"to {section_7.sent_char_count} characters for LLM prompt."
            )
    else:
        warnings.append("MISSING_SECTION: Item 7 (MD&A) is missing or empty.")

    return TenKSectionsDocument(
        ticker=clean_ticker,
        company_name=company_name.strip() or clean_ticker,
        cik=str(cik).strip() if cik is not None else None,
        filing_date=_coerce_optional_date(filing_date),
        period_of_report=_coerce_optional_date(period_of_report),
        accession_number=str(accession_number).strip() if accession_number else None,
        form=form,
        item_1a=section_1a,
        item_7=section_7,
        warnings=warnings,
    )


class TenKTextExtractor:
    """Rate-limited SEC EDGAR 10-K section extractor backed by edgartools."""

    def __init__(
        self,
        identity: str | None = None,
        max_requests_per_second: float = 8.0,
        load_env: bool = True,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        resolved_identity = resolve_edgar_identity(identity=identity, load_env=load_env)
        set_identity(resolved_identity)
        if rate_limiter is not None:
            self._rate_limiter = rate_limiter
        elif max_requests_per_second == 8.0:
            self._rate_limiter = get_shared_rate_limiter()
        else:
            self._rate_limiter = RateLimiter(max_requests_per_second=max_requests_per_second)

    def fetch_10k_sections(
        self,
        ticker: str,
        *,
        include_amendments: bool = False,
        max_chars_per_section: int = DEFAULT_MAX_CHARS_PER_SECTION,
    ) -> TenKSectionsDocument:
        """Fetch latest Form 10-K and extract Item 1A (Risk Factors) and Item 7 (MD&A)."""
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

        ensure_non_financial_company(company, clean_ticker)

        self._rate_limiter.wait()
        try:
            filings = company.get_filings(form="10-K", amendments=include_amendments)
            filing = filings.latest() if filings is not None else None
        except Exception as exc:
            raise DataFetchError(
                f"Failed to query 10-K filings for {clean_ticker}: {exc}"
            ) from exc

        if filing is None:
            raise DataFetchError(f"No Form 10-K filings found in SEC EDGAR for {clean_ticker}.")

        self._rate_limiter.wait()
        try:
            tenk = filing.obj()
        except Exception as exc:
            raise DataFetchError(
                f"Failed to parse Form 10-K document for {clean_ticker}: {exc}"
            ) from exc

        if tenk is None:
            raise DataFetchError(
                f"SEC Form 10-K parser returned no report object for {clean_ticker}."
            )

        item_1a_text = _extract_tenk_item_text(
            tenk, attr_name="risk_factors", item_key="Item 1A"
        )
        item_7_text = _extract_tenk_item_text(
            tenk, attr_name="management_discussion", item_key="Item 7"
        )

        company_name = getattr(company, "name", None) or clean_ticker
        cik_raw = getattr(company, "cik", None)
        filing_date = getattr(filing, "filing_date", None) or getattr(tenk, "filing_date", None)
        period_of_report = getattr(filing, "period_of_report", None) or getattr(
            tenk, "period_of_report", None
        )
        accession_no = getattr(filing, "accession_no", None) or getattr(
            filing, "accession_number", None
        )
        form_name = str(getattr(filing, "form", "10-K") or "10-K")

        return build_sections_from_texts(
            ticker=clean_ticker,
            company_name=str(company_name),
            item_1a_text=item_1a_text,
            item_7_text=item_7_text,
            cik=str(cik_raw) if cik_raw is not None else None,
            filing_date=filing_date,
            period_of_report=period_of_report,
            accession_number=str(accession_no) if accession_no else None,
            form=form_name,
            max_chars_per_section=max_chars_per_section,
        )
