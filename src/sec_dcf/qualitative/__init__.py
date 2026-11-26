"""Module 3: Auditable 10-K qualitative analysis powered by google-genai and Python verification."""

from __future__ import annotations

from sec_dcf.models.qualitative import QualitativeAuditReport, TenKSectionsDocument
from sec_dcf.qualitative.extractor import (
    DEFAULT_MAX_CHARS_PER_SECTION,
    TenKTextExtractor,
    build_section_text,
    build_sections_from_texts,
)
from sec_dcf.qualitative.gemini_client import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_TIMEOUT_SECONDS,
    SUPPORTED_RECOMMENDED_MODELS,
    GeminiQualitativeAnalyzer,
    build_qualitative_prompt,
)
from sec_dcf.qualitative.verifier import (
    DEFAULT_MIN_QUOTE_LENGTH,
    audit_qualitative_payload,
    normalize_for_quote_match,
    scan_prohibited_advice,
    verify_quote_in_section,
)


def analyze_10k_qualitative(
    ticker: str | None = None,
    *,
    document: TenKSectionsDocument | None = None,
    model: str = DEFAULT_GEMINI_MODEL,
    strict: bool = False,
    identity: str | None = None,
    api_key: str | None = None,
    max_chars_per_section: int = DEFAULT_MAX_CHARS_PER_SECTION,
    include_amendments: bool = False,
    timeout_seconds: float = DEFAULT_GEMINI_TIMEOUT_SECONDS,
    min_quote_length: int = DEFAULT_MIN_QUOTE_LENGTH,
    extractor: TenKTextExtractor | None = None,
    analyzer: GeminiQualitativeAnalyzer | None = None,
) -> QualitativeAuditReport:
    """
    End-to-end entrypoint for Module 3 qualitative 10-K analysis.

    Accepts either a pre-built `document` (offline mode) or fetches Form 10-K Item 1A
    and Item 7 for `ticker` via `TenKTextExtractor`, runs `GeminiQualitativeAnalyzer`,
    and deterministically verifies every verbatim quote in Python.
    """
    if document is None:
        if not ticker or not ticker.strip():
            raise ValueError("Either `ticker` or `document` must be provided.")
        active_extractor = extractor or TenKTextExtractor(identity=identity)
        document = active_extractor.fetch_10k_sections(
            ticker,
            include_amendments=include_amendments,
            max_chars_per_section=max_chars_per_section,
        )

    active_analyzer = analyzer or GeminiQualitativeAnalyzer(
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    payload = active_analyzer.analyze_sections(document)
    return audit_qualitative_payload(
        document,
        payload,
        model_name=active_analyzer.model,
        strict=strict,
        min_quote_length=min_quote_length,
    )


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_GEMINI_TIMEOUT_SECONDS",
    "DEFAULT_MAX_CHARS_PER_SECTION",
    "DEFAULT_MIN_QUOTE_LENGTH",
    "GeminiQualitativeAnalyzer",
    "SUPPORTED_RECOMMENDED_MODELS",
    "TenKTextExtractor",
    "analyze_10k_qualitative",
    "audit_qualitative_payload",
    "build_qualitative_prompt",
    "build_section_text",
    "build_sections_from_texts",
    "normalize_for_quote_match",
    "scan_prohibited_advice",
    "verify_quote_in_section",
]
