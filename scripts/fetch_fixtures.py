"""One-off helper script to fetch and compact SEC EDGAR 10-K companyfacts fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from sec_dcf.ingestion.edgar_client import EdgarClient
from sec_dcf.ingestion.xbrl_mapping import (
    AMORTIZATION_INTANGIBLES_CONCEPT,
    DA_AGGREGATE_CONCEPTS,
    DEPRECIATION_CONCEPT,
    DURATION_SPECS,
    FINANCE_LEASE_CURRENT_CONCEPT,
    FINANCE_LEASE_NONCURRENT_CONCEPT,
    FINANCE_LEASE_TOTAL_FALLBACK,
    INSTANT_SPECS,
    LIABILITIES_CURRENT_CONCEPT,
    LIABILITIES_DIRECT_CONCEPT,
    LIABILITIES_NONCURRENT_CONCEPT,
    LONG_TERM_DEBT_AGGREGATE_FALLBACKS,
    LONG_TERM_DEBT_PRIMARY_CONCEPT,
    OPERATING_LEASE_CURRENT_CONCEPT,
    OPERATING_LEASE_NONCURRENT_CONCEPT,
    OPERATING_LEASE_TOTAL_FALLBACK,
    SHORT_TERM_DEBT_AGGREGATE_CONCEPTS,
    SHORT_TERM_DEBT_COMPONENT_CONCEPTS,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

RELEVANT_CONCEPTS: set[str] = {
    *(c for spec in (*DURATION_SPECS, *INSTANT_SPECS) for c in spec.concepts),
    *DA_AGGREGATE_CONCEPTS,
    DEPRECIATION_CONCEPT,
    AMORTIZATION_INTANGIBLES_CONCEPT,
    *SHORT_TERM_DEBT_AGGREGATE_CONCEPTS,
    *SHORT_TERM_DEBT_COMPONENT_CONCEPTS,
    LONG_TERM_DEBT_PRIMARY_CONCEPT,
    *LONG_TERM_DEBT_AGGREGATE_FALLBACKS,
    OPERATING_LEASE_CURRENT_CONCEPT,
    OPERATING_LEASE_NONCURRENT_CONCEPT,
    OPERATING_LEASE_TOTAL_FALLBACK,
    FINANCE_LEASE_CURRENT_CONCEPT,
    FINANCE_LEASE_NONCURRENT_CONCEPT,
    FINANCE_LEASE_TOTAL_FALLBACK,
    LIABILITIES_DIRECT_CONCEPT,
    LIABILITIES_CURRENT_CONCEPT,
    LIABILITIES_NONCURRENT_CONCEPT,
}


def export_companyfacts_fixture(client: EdgarClient, ticker: str, out_path: Path) -> None:
    """Fetch EntityFacts via edgartools and serialize relevant 10-K facts to SEC JSON format."""
    company, facts = client.fetch_raw_company_and_facts(ticker)
    facts_by_taxonomy: dict[str, dict[str, dict]] = {}

    for fact in facts.get_all_facts():
        if fact.concept not in RELEVANT_CONCEPTS:
            continue
        if (fact.form_type or "").upper() not in {"10-K", "10-K/A"}:
            continue
        if getattr(fact, "dimensions", None):
            continue

        taxonomy, _, short_concept = fact.concept.partition(":")
        if not short_concept:
            taxonomy = fact.taxonomy or "us-gaap"
            short_concept = fact.concept

        concept_bucket = facts_by_taxonomy.setdefault(taxonomy, {}).setdefault(
            short_concept,
            {
                "label": fact.label or short_concept,
                "description": "",
                "units": {},
            },
        )
        unit = fact.unit or "USD"
        unit_list = concept_bucket["units"].setdefault(unit, [])

        entry: dict = {
            "val": fact.value,
            "end": fact.period_end.isoformat() if fact.period_end else None,
            "filed": fact.filing_date.isoformat() if fact.filing_date else None,
            "form": fact.form_type,
            "accn": fact.accession,
            "fy": fact.fiscal_year,
            "fp": fact.fiscal_period,
        }
        if fact.period_start is not None:
            entry["start"] = fact.period_start.isoformat()
        unit_list.append(entry)

    payload = {
        "cik": int(company.cik),
        "entityName": company.name,
        "sic": str(company.sic) if company.sic is not None else None,
        "fiscalYearEnd": str(company.fiscal_year_end) if company.fiscal_year_end else None,
        "ticker": ticker.upper(),
        "facts": facts_by_taxonomy,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    client = EdgarClient()
    for ticker in ("AAPL", "MSFT", "AMZN"):
        target = FIXTURES_DIR / f"{ticker.lower()}_companyfacts.json"
        export_companyfacts_fixture(client, ticker, target)
        print(f"Saved {ticker} fixture to {target}")


if __name__ == "__main__":
    main()
