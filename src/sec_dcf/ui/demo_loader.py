"""Offline fixture and demo data loaders for the Streamlit portfolio dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from edgar.entity.parser import EntityFactsParser

from sec_dcf.ingestion.normalizer import normalize_entity_facts
from sec_dcf.models.financials import CompanyFinancialsReport
from sec_dcf.models.qualitative import (
    ExtractedGrowthDriver,
    ExtractedRedFlag,
    ExtractedRiskItem,
    QualitativeAuditReport,
    QualitativeExtractionPayload,
    TenKSectionsDocument,
)
from sec_dcf.qualitative.extractor import build_sections_from_texts
from sec_dcf.qualitative.verifier import audit_qualitative_payload
from sec_dcf.validation.accounting import validate_financials

DEMO_TICKERS: tuple[str, ...] = ("AAPL", "MSFT", "AMZN")

_DEMO_FIXTURE_FILES: dict[str, str] = {
    "AAPL": "aapl_companyfacts.json",
    "MSFT": "msft_companyfacts.json",
    "AMZN": "amzn_companyfacts.json",
}


@dataclass(frozen=True)
class DemoMarketDefaults:
    """Illustrative starting inputs for offline WACC exploration (user-editable)."""

    share_price: Decimal
    beta: Decimal
    risk_free_rate: Decimal = Decimal("0.0425")
    equity_risk_premium: Decimal = Decimal("0.0450")


DEMO_MARKET_DEFAULTS: dict[str, DemoMarketDefaults] = {
    "AAPL": DemoMarketDefaults(share_price=Decimal("225.00"), beta=Decimal("1.20")),
    "MSFT": DemoMarketDefaults(share_price=Decimal("425.00"), beta=Decimal("1.10")),
    "AMZN": DemoMarketDefaults(share_price=Decimal("210.00"), beta=Decimal("1.15")),
}


def resolve_fixtures_dir(fixtures_dir: Path | None = None) -> Path:
    """Resolve `tests/fixtures` relative to this module with CWD fallback."""
    if fixtures_dir is not None:
        resolved = Path(fixtures_dir)
        if not resolved.is_dir():
            raise FileNotFoundError(f"Fixtures directory does not exist: {resolved}")
        return resolved

    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        candidate = parent / "tests" / "fixtures"
        if candidate.is_dir():
            return candidate

    cwd_candidate = Path.cwd() / "tests" / "fixtures"
    if cwd_candidate.is_dir():
        return cwd_candidate

    raise FileNotFoundError(
        "Could not locate 'tests/fixtures' directory relative to sec_dcf.ui or current working directory."
    )


def load_demo_financials(
    ticker: str,
    *,
    fixtures_dir: Path | None = None,
) -> CompanyFinancialsReport:
    """Load, normalize, and validate a local 5-year 10-K fixture offline."""
    clean_ticker = ticker.strip().upper()
    filename = _DEMO_FIXTURE_FILES.get(clean_ticker)
    if filename is None:
        supported = ", ".join(DEMO_TICKERS)
        raise ValueError(f"Unsupported demo ticker '{ticker}'. Available demo tickers: {supported}.")

    base_dir = resolve_fixtures_dir(fixtures_dir)
    fixture_path = base_dir / filename
    if not fixture_path.is_file():
        raise FileNotFoundError(f"Demo fixture file not found: {fixture_path}")

    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    facts = EntityFactsParser.parse_company_facts(raw)
    if facts is None:
        raise ValueError(f"Failed to parse EntityFacts from demo fixture: {fixture_path}")

    report = normalize_entity_facts(
        facts=facts,
        ticker=raw["ticker"],
        sic=raw.get("sic"),
        fiscal_year_end=raw.get("fiscalYearEnd"),
        years=5,
        strict=True,
    )
    return validate_financials(report, strict=True, expected_years=5)


def load_demo_qualitative_audit(
    *,
    fixtures_dir: Path | None = None,
) -> tuple[TenKSectionsDocument, QualitativeAuditReport]:
    """
    Build an offline qualitative audit sample from `sample_10k_sections.json`.

    Includes both verified verbatim quotes (resilient to EDGAR whitespace/unicode
    artifacts) and one intentionally unverified claim to showcase deterministic
    citation auditing without any network or Gemini API key.
    """
    base_dir = resolve_fixtures_dir(fixtures_dir)
    sample_path = base_dir / "sample_10k_sections.json"
    if not sample_path.is_file():
        raise FileNotFoundError(f"Qualitative demo fixture not found: {sample_path}")

    fixture_data = json.loads(sample_path.read_text(encoding="utf-8"))
    document = build_sections_from_texts(
        ticker=fixture_data["ticker"],
        company_name=fixture_data["company_name"],
        cik=fixture_data.get("cik"),
        filing_date=fixture_data.get("filing_date"),
        period_of_report=fixture_data.get("period_of_report"),
        accession_number=fixture_data.get("accession_number"),
        item_1a_text=fixture_data.get("item_1a"),
        item_7_text=fixture_data.get("item_7"),
    )

    sample_payload = QualitativeExtractionPayload(
        executive_summary=(
            "Przykładowa próbka offline (ACME Cloud Systems): raport wskazuje na ryzyko koncentracji "
            "dostawców półprzewodników oraz cyberbezpieczeństwa przy jednoczesnym wzroście adopcji "
            "platformy AI i wydłużeniu terminów płatności dla wybranych dystrybutorów."
        ),
        risks=[
            ExtractedRiskItem(
                title="Koncentracja produkcji półprzewodników na Tajwanie",
                category="supply_chain",
                materiality="high",
                summary="Zależność od ograniczonej liczby odlewni wytwarzających akceleratory AI.",
                section="Item 1A",
                verbatim_quote=(
                    "Any geopolitical disruption, export restriction, or natural disaster "
                    "affecting these suppliers' facilities could delay product shipments "
                    "and materially harm our revenue."
                ),
            ),
            ExtractedRiskItem(
                title="Niezweryfikowane roszczenie patentowe (przykład wykrycia halucynacji)",
                category="legal_governance",
                materiality="medium",
                summary="Przykładowy punkt ilustrujący działanie deterministycznego weryfikatora cytatów.",
                section="Item 1A",
                verbatim_quote=(
                    "A federal jury ordered the company to halt all cloud sales immediately."
                ),
            ),
        ],
        growth_drivers=[
            ExtractedGrowthDriver(
                title="Adopcja platformy inferencyjnej w segmencie enterprise",
                summary="Wzrost przychodów napędzany wdrożeniami w dużych przedsiębiorstwach i retencją.",
                management_tone="positive",
                section="Item 7",
                verbatim_quote=(
                    "Consolidated revenue increased 24% year-over-year in fiscal 2025, "
                    'driven primarily by "accelerated enterprise adoption" of our '
                    "multi-region inference platform"
                ),
            )
        ],
        red_flags=[
            ExtractedRedFlag(
                title="Wydłużenie terminów płatności dystrybutorów (45 -> 90 dni)",
                severity="medium",
                rationale="Należności rosły szybciej niż przychody po wydłużeniu kredytu kupieckiego w Q4.",
                section="Item 7",
                verbatim_quote=(
                    "we extended payment terms for select strategic distributors from 45 days "
                    "to 90 days, which contributed to an expansion in accounts receivable faster "
                    "than revenue growth."
                ),
            )
        ],
    )

    report = audit_qualitative_payload(
        document,
        sample_payload,
        model_name="offline-demo-sample",
        strict=False,
    )
    return document, report
