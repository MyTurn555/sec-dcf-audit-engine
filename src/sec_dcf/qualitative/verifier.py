"""Deterministic Python quote verifier and educational compliance scanner for 10-K AI outputs."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from sec_dcf.exceptions import QualitativeAuditError
from sec_dcf.models.qualitative import (
    AuditedGrowthDriver,
    AuditedRedFlag,
    AuditedRiskItem,
    ComplianceFlag,
    QualitativeAuditReport,
    QualitativeExtractionPayload,
    SectionName,
    TenKSectionsDocument,
)

DEFAULT_MIN_QUOTE_LENGTH = 15
_RATIO_QUANTIZER = Decimal("0.0001")

_ZERO_WIDTH_OR_SOFT_HYPHEN_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]")
_NBSP_LIKE_RE = re.compile(r"[\xa0\u202f\u2007\u2009]")
_WHITESPACE_RE = re.compile(r"\s+")

_TYPOGRAPHY_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "’": "'",
        "‘": "'",
        "‚": "'",
        "–": "-",
        "—": "-",
        "−": "-",
    }
)

# Strictly full phrases with word boundaries to avoid false positives on ordinary prose.
_PROHIBITED_ADVICE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("strong_buy_sell", re.compile(r"\bstrong\s+(?:buy|sell)\b", re.IGNORECASE)),
    ("buy_sell_hold_rating", re.compile(r"\b(?:buy|sell|hold)\s+rating\b", re.IGNORECASE)),
    (
        "recommend_buy_sell",
        re.compile(r"\brecommend(?:s|ed|ing)?\s+(?:buying|selling|a\s+buy|a\s+sell)\b", re.IGNORECASE),
    ),
    ("price_target", re.compile(r"\b(?:price\s+target|target\s+price)\b", re.IGNORECASE)),
    (
        "polish_buy_sell_rec",
        re.compile(
            r"\b(?:rekomendacj[aęi]\s+(?:kupna|sprzedaży)|cena\s+docelowa|kupuj|sprzedaj)\b",
            re.IGNORECASE,
        ),
    ),
)


def normalize_for_quote_match(text: str) -> str:
    """
    Deterministically normalize whitespace and typography for verbatim quote verification.

    - Zero-width characters and soft hyphens are removed (`""`) so words are not split.
    - Non-breaking / narrow spaces are converted to regular spaces (`" "`).
    - Typographical quotes, apostrophes, and dashes are mapped to ASCII equivalents.
    - Consecutive whitespace is collapsed into a single space.
    """
    cleaned = _ZERO_WIDTH_OR_SOFT_HYPHEN_RE.sub("", text)
    cleaned = _NBSP_LIKE_RE.sub(" ", cleaned)
    cleaned = cleaned.translate(_TYPOGRAPHY_TRANSLATION)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def verify_quote_in_section(
    quote: str,
    section_text: str,
    *,
    min_quote_length: int = DEFAULT_MIN_QUOTE_LENGTH,
) -> tuple[bool, str | None]:
    """
    Deterministically verify that `quote` is an exact substring of `section_text`
    after whitespace and typography normalization.
    """
    norm_quote = normalize_for_quote_match(quote)
    if len(norm_quote) < min_quote_length:
        return (
            False,
            f"Quote length ({len(norm_quote)} chars) is below minimum audit length ({min_quote_length}).",
        )

    norm_section = normalize_for_quote_match(section_text)
    if not norm_section:
        return False, "Target 10-K section text is empty or unavailable."

    if norm_quote in norm_section:
        return True, None

    return False, "Quote was not found verbatim in the claimed 10-K section."


def _verify_item_quote(
    document: TenKSectionsDocument,
    *,
    claimed_section: SectionName,
    quote: str,
    min_quote_length: int,
) -> tuple[bool, str | None]:
    """Verify quote against the claimed section, noting cross-section mismatches."""
    target = document.get_section(claimed_section)
    if target is None:
        return False, f"Claimed section {claimed_section} is not present in this 10-K document."

    verified, note = verify_quote_in_section(
        quote,
        target.raw_text,
        min_quote_length=min_quote_length,
    )
    if verified:
        return True, None

    # Helpful audit diagnostic: check if quote actually came from the other section.
    other_section: SectionName = "Item 7" if claimed_section == "Item 1A" else "Item 1A"
    other_obj = document.get_section(other_section)
    if other_obj is not None:
        in_other, _ = verify_quote_in_section(
            quote,
            other_obj.raw_text,
            min_quote_length=min_quote_length,
        )
        if in_other:
            return (
                False,
                f"Quote not found in {claimed_section} (matched in {other_section} — section mismatch).",
            )

    return False, note


def scan_prohibited_advice(payload: QualitativeExtractionPayload) -> list[ComplianceFlag]:
    """
    Scan AI-generated narrative fields for prohibited buy/sell advice or price targets.

    Deliberately skips `verbatim_quote` fields so direct SEC 10-K citations do not cause
    false-positive compliance flags.
    """
    fields_to_scan: list[tuple[str, str]] = [
        ("executive_summary", payload.executive_summary),
    ]
    for idx, risk in enumerate(payload.risks):
        fields_to_scan.append((f"risks[{idx}].title", risk.title))
        fields_to_scan.append((f"risks[{idx}].summary", risk.summary))
    for idx, driver in enumerate(payload.growth_drivers):
        fields_to_scan.append((f"growth_drivers[{idx}].title", driver.title))
        fields_to_scan.append((f"growth_drivers[{idx}].summary", driver.summary))
    for idx, flag in enumerate(payload.red_flags):
        fields_to_scan.append((f"red_flags[{idx}].title", flag.title))
        fields_to_scan.append((f"red_flags[{idx}].rationale", flag.rationale))

    flags: list[ComplianceFlag] = []
    for field_path, text in fields_to_scan:
        if not text:
            continue
        for pattern_name, regex in _PROHIBITED_ADVICE_PATTERNS:
            match = regex.search(text)
            if match:
                start = max(0, match.start() - 25)
                end = min(len(text), match.end() + 25)
                flags.append(
                    ComplianceFlag(
                        field_path=field_path,
                        matched_pattern=pattern_name,
                        excerpt=text[start:end].strip(),
                    )
                )
    return flags


def audit_qualitative_payload(
    document: TenKSectionsDocument,
    payload: QualitativeExtractionPayload,
    *,
    model_name: str,
    strict: bool = False,
    min_quote_length: int = DEFAULT_MIN_QUOTE_LENGTH,
) -> QualitativeAuditReport:
    """
    Deterministically audit every LLM-extracted claim against the source 10-K text
    and compute verified_quote_ratio and compliance flags.
    """
    audited_risks: list[AuditedRiskItem] = []
    audited_drivers: list[AuditedGrowthDriver] = []
    audited_red_flags: list[AuditedRedFlag] = []

    verified_count = 0

    for item in payload.risks:
        ok, note = _verify_item_quote(
            document,
            claimed_section=item.section,
            quote=item.verbatim_quote,
            min_quote_length=min_quote_length,
        )
        if ok:
            verified_count += 1
        audited_risks.append(
            AuditedRiskItem(**item.model_dump(), quote_verified=ok, verification_note=note)
        )

    for driver in payload.growth_drivers:
        ok, note = _verify_item_quote(
            document,
            claimed_section=driver.section,
            quote=driver.verbatim_quote,
            min_quote_length=min_quote_length,
        )
        if ok:
            verified_count += 1
        audited_drivers.append(
            AuditedGrowthDriver(**driver.model_dump(), quote_verified=ok, verification_note=note)
        )

    for red_flag in payload.red_flags:
        ok, note = _verify_item_quote(
            document,
            claimed_section=red_flag.section,
            quote=red_flag.verbatim_quote,
            min_quote_length=min_quote_length,
        )
        if ok:
            verified_count += 1
        audited_red_flags.append(
            AuditedRedFlag(**red_flag.model_dump(), quote_verified=ok, verification_note=note)
        )

    total_claims = len(audited_risks) + len(audited_drivers) + len(audited_red_flags)
    if total_claims == 0:
        ratio = Decimal("0")
    else:
        ratio = (Decimal(verified_count) / Decimal(total_claims)).quantize(
            _RATIO_QUANTIZER, rounding=ROUND_HALF_UP
        )

    warnings = list(document.warnings)
    if total_claims == 0:
        warnings.append(
            "NO_CLAIMS_EXTRACTED: LLM returned zero risk, growth driver, or red flag items."
        )

    compliance_flags = scan_prohibited_advice(payload)

    truncation_status: dict[SectionName, bool] = {}
    if document.item_1a is not None:
        truncation_status["Item 1A"] = document.item_1a.truncated
    if document.item_7 is not None:
        truncation_status["Item 7"] = document.item_7.truncated

    if strict and (
        total_claims == 0 or verified_count < total_claims or bool(compliance_flags)
    ):
        reasons: list[str] = []
        if total_claims == 0:
            reasons.append("NO_CLAIMS_EXTRACTED (0 claims returned)")
        elif verified_count < total_claims:
            reasons.append(
                f"unverified quotes ({verified_count}/{total_claims} verified, ratio={ratio})"
            )
        if compliance_flags:
            reasons.append(f"{len(compliance_flags)} compliance flag(s) detected")
        raise QualitativeAuditError(
            "Strict qualitative audit failed: " + "; ".join(reasons) + "."
        )

    return QualitativeAuditReport(
        ticker=document.ticker,
        company_name=document.company_name,
        filing_date=document.filing_date,
        period_of_report=document.period_of_report,
        accession_number=document.accession_number,
        model_name=model_name,
        executive_summary=payload.executive_summary,
        risks=audited_risks,
        growth_drivers=audited_drivers,
        red_flags=audited_red_flags,
        total_claims=total_claims,
        verified_claims=verified_count,
        verified_quote_ratio=ratio,
        compliance_flags=compliance_flags,
        section_truncation_status=truncation_status,
        warnings=warnings,
    )
