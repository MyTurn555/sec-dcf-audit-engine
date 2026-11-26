"""Model selection and 503 UNAVAILABLE fallback runner for Tab 3 qualitative audit."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from sec_dcf.exceptions import GeminiApiError
from sec_dcf.models.qualitative import QualitativeAuditReport, TenKSectionsDocument
from sec_dcf.qualitative import (
    DEFAULT_GEMINI_TIMEOUT_SECONDS,
    GeminiQualitativeAnalyzer,
    TenKTextExtractor,
    analyze_10k_qualitative,
)

AVAILABLE_GEMINI_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-4-hydrogen",
)
FALLBACK_GEMINI_MODEL = "gemini-3.5-flash-lite"


class QualitativeRunOutcome(BaseModel):
    """Result envelope tracking requested vs executed Gemini model and fallback state."""

    model_config = ConfigDict(frozen=True)

    report: QualitativeAuditReport
    requested_model: str
    executed_model: str
    fallback_triggered: bool = False
    fallback_reason: str | None = None


def is_503_unavailable_error(exc: Exception) -> bool:
    """Return True when an exception represents a Gemini 503 / UNAVAILABLE capacity error."""
    message = str(exc).lower()
    return "503" in message or "unavailable" in message


def run_qualitative_audit_with_fallback(
    *,
    ticker: str | None = None,
    document: TenKSectionsDocument | None = None,
    selected_model: str = AVAILABLE_GEMINI_MODELS[0],
    enable_503_fallback: bool = True,
    api_key: str | None = None,
    identity: str | None = None,
    timeout_seconds: float = DEFAULT_GEMINI_TIMEOUT_SECONDS,
    strict: bool = False,
    extractor: TenKTextExtractor | None = None,
    primary_analyzer: GeminiQualitativeAnalyzer | None = None,
    fallback_analyzer: GeminiQualitativeAnalyzer | None = None,
) -> QualitativeRunOutcome:
    """
    Execute Module 3 qualitative 10-K audit with optional 503 UNAVAILABLE fallback.

    When `enable_503_fallback` is True and `selected_model` differs from
    `FALLBACK_GEMINI_MODEL` (`gemini-3.5-flash-lite`), a `GeminiApiError` indicating
    503 / UNAVAILABLE triggers a single automatic retry on `gemini-3.5-flash-lite`.
    """
    active_document = document
    if active_document is None:
        if not ticker or not ticker.strip():
            raise ValueError("Either `ticker` or `document` must be provided.")
        active_extractor = extractor or TenKTextExtractor(identity=identity)
        active_document = active_extractor.fetch_10k_sections(ticker)

    try:
        report = analyze_10k_qualitative(
            document=active_document,
            model=selected_model,
            strict=strict,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            analyzer=primary_analyzer,
        )
        return QualitativeRunOutcome(
            report=report,
            requested_model=selected_model,
            executed_model=report.model_name,
            fallback_triggered=False,
        )
    except GeminiApiError as exc:
        should_fallback = (
            enable_503_fallback
            and selected_model != FALLBACK_GEMINI_MODEL
            and is_503_unavailable_error(exc)
        )
        if not should_fallback:
            raise

        fallback_report = analyze_10k_qualitative(
            document=active_document,
            model=FALLBACK_GEMINI_MODEL,
            strict=strict,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            analyzer=fallback_analyzer,
        )
        return QualitativeRunOutcome(
            report=fallback_report,
            requested_model=selected_model,
            executed_model=fallback_report.model_name,
            fallback_triggered=True,
            fallback_reason=str(exc),
        )
