"""Gemini AI qualitative 10-K analyzer built exclusively on google-genai SDK."""

from __future__ import annotations

import os
import re
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import ValidationError

from sec_dcf.exceptions import GeminiApiError
from sec_dcf.models.qualitative import QualitativeExtractionPayload, TenKSectionsDocument

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
SUPPORTED_RECOMMENDED_MODELS = ("gemini-3.8-flash", "gemini-4-hydrogen")
DEFAULT_GEMINI_TIMEOUT_SECONDS = 60.0

_API_KEY_PARAM_RE = re.compile(r"((?:api_key|key)=)[^&\s\"']+", re.IGNORECASE)

SYSTEM_INSTRUCTION = """You are an auditable SEC Form 10-K qualitative research assistant for educational and research purposes only.

STRICT RULES:
1. Ground every finding exclusively in the provided Item 1A (Risk Factors) and Item 7 (MD&A) text.
2. NEVER perform financial calculations, compute ratios, estimate WACC/DCF values, or project numerical targets.
3. NEVER issue investment advice, buy/sell/hold recommendations, or price targets.
4. Every single item in `risks`, `growth_drivers`, and `red_flags` MUST include:
   - `section`: exact section identifier ("Item 1A" or "Item 7") where the quote appears;
   - `verbatim_quote`: an exact, word-for-word substring copied from that section (do NOT paraphrase, merge non-adjacent sentences with ellipses, or invent quotes).
5. In `red_flags`, explicitly check Item 7 (MD&A) for material off-balance-sheet, uncommenced lease commitments (e.g. data center, fulfillment, or build-to-suit "leases not yet commenced" not yet recognized on the balance sheet under ASC 842) and extract them with an exact `verbatim_quote` when disclosed.
6. Prefer concise, concrete disclosures over generic boilerplate."""


def _mask_api_key(message: str, api_key: str | None) -> str:
    """Redact GEMINI_API_KEY values and key query parameters from error messages."""
    masked = _API_KEY_PARAM_RE.sub(r"\1***", message)
    if api_key:
        masked = masked.replace(api_key, "***")
    return masked


def build_qualitative_prompt(document: TenKSectionsDocument) -> str:
    """Build a clearly delimited prompt containing available Item 1A and Item 7 text."""
    parts = [
        f"Company: {document.company_name} ({document.ticker})",
        f"Form: {document.form}",
    ]
    if document.filing_date is not None:
        parts.append(f"Filing Date: {document.filing_date.isoformat()}")
    if document.period_of_report is not None:
        parts.append(f"Period of Report: {document.period_of_report.isoformat()}")

    parts.append("")
    parts.append(
        "Extract structured qualitative disclosures (key risks, management growth drivers/commentary, "
        "and potential red flags) strictly grounded in the sections below."
    )

    for section_label, section_obj in (
        ("Item 1A", document.item_1a),
        ("Item 7", document.item_7),
    ):
        parts.append("")
        if section_obj is None:
            parts.append(f"=== BEGIN SECTION: {section_label} ===")
            parts.append("[UNAVAILABLE IN THIS FILING]")
            parts.append(f"=== END SECTION: {section_label} ===")
        else:
            trunc_note = (
                f" (truncated to {section_obj.sent_char_count} of {section_obj.original_char_count} chars)"
                if section_obj.truncated
                else ""
            )
            parts.append(f"=== BEGIN SECTION: {section_label}{trunc_note} ===")
            parts.append(section_obj.prompt_text)
            parts.append(f"=== END SECTION: {section_label} ===")

    return "\n".join(parts)


class GeminiQualitativeAnalyzer:
    """BYOK Gemini analyzer using google-genai Structured Outputs (Pydantic schema)."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        timeout_seconds: float = DEFAULT_GEMINI_TIMEOUT_SECONDS,
        client: Any | None = None,
        load_env: bool = True,
    ) -> None:
        cleaned_model = model.strip()
        if not cleaned_model:
            raise GeminiApiError("Gemini model name must not be empty.")
        if timeout_seconds <= 0:
            raise GeminiApiError(f"Gemini timeout_seconds must be positive, got {timeout_seconds}.")

        self.model = cleaned_model
        self.timeout_seconds = timeout_seconds
        self._api_key: str | None = None

        if client is not None:
            self._client = client
            return

        if api_key is None:
            if load_env:
                load_dotenv(override=False)
            resolved_key = os.getenv("GEMINI_API_KEY", "").strip()
        else:
            resolved_key = api_key.strip()

        if not resolved_key:
            raise GeminiApiError(
                "GEMINI_API_KEY is missing. Set GEMINI_API_KEY in your environment/.env "
                "or pass api_key explicitly (BYOK mode)."
            )

        self._api_key = resolved_key
        # google-genai HttpOptions.timeout is specified in milliseconds (int).
        timeout_ms = max(1, int(round(timeout_seconds * 1000)))
        self._client = genai.Client(
            api_key=resolved_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )

    def analyze_sections(self, document: TenKSectionsDocument) -> QualitativeExtractionPayload:
        """Send Item 1A / Item 7 text to Gemini and parse the Pydantic structured output."""
        prompt = build_qualitative_prompt(document)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=QualitativeExtractionPayload,
            temperature=0.0,
        )

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            safe_msg = _mask_api_key(str(exc), self._api_key)
            raise GeminiApiError(
                f"Gemini API request failed for model '{self.model}': {safe_msg}"
            ) from None

        if response is None:
            raise GeminiApiError(f"Gemini API returned an empty response for model '{self.model}'.")

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, QualitativeExtractionPayload):
            return parsed
        if isinstance(parsed, dict):
            try:
                return QualitativeExtractionPayload.model_validate(parsed)
            except ValidationError as exc:
                raise GeminiApiError(
                    f"Gemini parsed payload failed schema validation: {exc}"
                ) from None

        raw_text = getattr(response, "text", None)
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise GeminiApiError(
                f"Gemini response for model '{self.model}' contained neither parsed object nor JSON text."
            )

        try:
            return QualitativeExtractionPayload.model_validate_json(raw_text)
        except ValidationError as exc:
            safe_msg = _mask_api_key(str(exc), self._api_key)
            raise GeminiApiError(
                f"Gemini JSON output failed schema validation: {safe_msg}"
            ) from None
