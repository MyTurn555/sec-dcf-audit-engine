"""Shared pytest fixtures and synthetic SEC companyfacts builders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from edgar.entity import EntityFacts
from edgar.entity.parser import EntityFactsParser

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_companyfacts_fixture(filename: str) -> tuple[dict[str, Any], EntityFacts]:
    """Load a local SEC companyfacts JSON fixture and parse into EntityFacts."""
    raw = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    facts = EntityFactsParser.parse_company_facts(raw)
    assert facts is not None, f"Failed to parse fixture {filename}"
    return raw, facts


def build_synthetic_entity_facts(
    concepts: dict[str, list[dict[str, Any]]],
    cik: int = 9999999,
    entity_name: str = "Synthetic Test Corp",
) -> EntityFacts:
    """
    Build a genuine edgartools EntityFacts object from taxonomy-prefixed concept entries.

    Each value in `concepts` is a list of SEC-style fact dicts:
      {"val": ..., "end": "YYYY-MM-DD", "start": "YYYY-MM-DD" (optional),
       "filed": "YYYY-MM-DD", "form": "10-K", "unit": "USD", "accn": "...", "fy": 2025, "fp": "FY"}
    """
    facts_tree: dict[str, dict[str, Any]] = {}
    for full_concept, entries in concepts.items():
        taxonomy, _, short_name = full_concept.partition(":")
        if not short_name:
            taxonomy = "us-gaap"
            short_name = full_concept
        concept_node = facts_tree.setdefault(taxonomy, {}).setdefault(
            short_name,
            {"label": short_name, "description": "", "units": {}},
        )
        for entry in entries:
            unit = entry.get("unit", "USD")
            fact_payload = {
                "val": entry["val"],
                "end": entry["end"],
                "filed": entry.get("filed", "2026-02-01"),
                "form": entry.get("form", "10-K"),
                "accn": entry.get("accn", "0000999999-26-000001"),
                "fy": entry.get("fy", 2025),
                "fp": entry.get("fp", "FY"),
            }
            if "start" in entry and entry["start"] is not None:
                fact_payload["start"] = entry["start"]
            concept_node["units"].setdefault(unit, []).append(fact_payload)

    parsed = EntityFactsParser.parse_company_facts(
        {"cik": cik, "entityName": entity_name, "facts": facts_tree}
    )
    assert parsed is not None
    return parsed


def standard_year_concepts(
    year: int = 2025,
    filed: str = "2026-02-01",
    form: str = "10-K",
    accn: str = "0000999999-26-000001",
    overrides: dict[str, Any] | None = None,
    omit: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Generate a complete, balanced 1-year set of US-GAAP facts."""
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    omit = omit or set()
    overrides = overrides or {}

    defaults: dict[str, tuple[Any, str, bool]] = {
        # concept: (value, unit, is_duration)
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": (1_000_000_000, "USD", True),
        "us-gaap:OperatingIncomeLoss": (200_000_000, "USD", True),
        "us-gaap:DepreciationDepletionAndAmortization": (50_000_000, "USD", True),
        "us-gaap:InterestExpense": (10_000_000, "USD", True),
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": (
            190_000_000,
            "USD",
            True,
        ),
        "us-gaap:IncomeTaxExpenseBenefit": (40_000_000, "USD", True),
        "us-gaap:NetIncomeLoss": (150_000_000, "USD", True),
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding": (100_000_000, "shares", True),
        "us-gaap:NetCashProvidedByUsedInOperatingActivities": (210_000_000, "USD", True),
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment": (60_000_000, "USD", True),
        "us-gaap:CashAndCashEquivalentsAtCarryingValue": (100_000_000, "USD", False),
        "us-gaap:MarketableSecuritiesCurrent": (50_000_000, "USD", False),
        "us-gaap:AssetsCurrent": (300_000_000, "USD", False),
        "us-gaap:Assets": (1_000_000_000, "USD", False),
        "us-gaap:LiabilitiesCurrent": (180_000_000, "USD", False),
        "us-gaap:ShortTermBorrowings": (20_000_000, "USD", False),
        "us-gaap:LongTermDebtNoncurrent": (200_000_000, "USD", False),
        "us-gaap:OperatingLeaseLiabilityCurrent": (10_000_000, "USD", False),
        "us-gaap:OperatingLeaseLiabilityNoncurrent": (40_000_000, "USD", False),
        "us-gaap:Liabilities": (450_000_000, "USD", False),
        "us-gaap:StockholdersEquity": (550_000_000, "USD", False),
        "us-gaap:CommonStockSharesOutstanding": (98_000_000, "shares", False),
    }

    result: dict[str, list[dict[str, Any]]] = {}
    for concept, (val, unit, is_dur) in defaults.items():
        if concept in omit:
            continue
        actual_val = overrides.get(concept, val)
        entry: dict[str, Any] = {
            "val": actual_val,
            "end": end,
            "filed": filed,
            "form": form,
            "accn": accn,
            "fy": year,
            "fp": "FY",
            "unit": unit,
        }
        if is_dur:
            entry["start"] = start
        result[concept] = [entry]
    return result


@pytest.fixture(scope="session")
def aapl_fixture() -> tuple[dict[str, Any], EntityFacts]:
    """Offline AAPL SEC companyfacts fixture."""
    return load_companyfacts_fixture("aapl_companyfacts.json")


@pytest.fixture(scope="session")
def msft_fixture() -> tuple[dict[str, Any], EntityFacts]:
    """Offline MSFT SEC companyfacts fixture."""
    return load_companyfacts_fixture("msft_companyfacts.json")


@pytest.fixture(scope="session")
def amzn_fixture() -> tuple[dict[str, Any], EntityFacts]:
    """Offline AMZN SEC companyfacts fixture."""
    return load_companyfacts_fixture("amzn_companyfacts.json")
