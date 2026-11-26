"""Unit tests for XBRL mapping, normalization, deduplication, and client guards."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from sec_dcf.exceptions import (
    EdgarIdentityError,
    MissingCriticalConceptError,
    UnsupportedCompanyTypeError,
)
from sec_dcf.ingestion.edgar_client import EdgarClient
from sec_dcf.ingestion.normalizer import normalize_entity_facts
from sec_dcf.validation.accounting import validate_financials
from tests.conftest import build_synthetic_entity_facts, standard_year_concepts


def test_capex_excludes_noncash_supplemental_tag_and_normalizes_negative_raw():
    # 1. CapitalExpendituresIncurredButNotYetPaid alone must NOT satisfy capex
    concepts_noncash_only = standard_year_concepts(
        omit={"us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"}
    )
    concepts_noncash_only["us-gaap:CapitalExpendituresIncurredButNotYetPaid"] = [
        {
            "val": 12_000_000,
            "start": "2025-01-01",
            "end": "2025-12-31",
            "filed": "2026-02-01",
            "form": "10-K",
            "unit": "USD",
        }
    ]
    ef_missing = build_synthetic_entity_facts(concepts_noncash_only)
    with pytest.raises(MissingCriticalConceptError, match="capex"):
        normalize_entity_facts(ef_missing, years=1, strict=True)

    # 2. Negative raw SEC CapEx is normalized to non-negative with raw value preserved in audit
    concepts_neg = standard_year_concepts(
        overrides={"us-gaap:PaymentsToAcquirePropertyPlantAndEquipment": -60_000_000}
    )
    ef_neg = build_synthetic_entity_facts(concepts_neg)
    report = normalize_entity_facts(ef_neg, years=1, strict=True)
    period = report.periods[0]
    assert period.capex == Decimal("60000000")
    assert period.fcf_simple == Decimal("150000000")  # 210M - 60M
    assert period.audit_trail["capex"].raw_value == Decimal("-60000000")


def test_share_counts_duration_vs_instant_and_dei_cover_window():
    # Omit us-gaap:CommonStockSharesOutstanding and supply dei:EntityCommonStockSharesOutstanding
    # at +20 days (should match) vs +55 days (should be ignored)
    concepts = standard_year_concepts(omit={"us-gaap:CommonStockSharesOutstanding"})
    concepts["dei:EntityCommonStockSharesOutstanding"] = [
        {
            "val": 97_500_000,
            "end": "2026-01-20",  # +20 days after 2025-12-31
            "filed": "2026-02-01",
            "form": "10-K",
            "unit": "shares",
        }
    ]
    ef = build_synthetic_entity_facts(concepts)
    period = normalize_entity_facts(ef, years=1, strict=True).periods[0]
    assert period.diluted_shares == Decimal("100000000")
    assert period.ending_shares == Decimal("97500000")
    assert period.audit_trail["ending_shares"].concept == "dei:EntityCommonStockSharesOutstanding"

    # Outside +45 day window should NOT match
    concepts_late = standard_year_concepts(omit={"us-gaap:CommonStockSharesOutstanding"})
    concepts_late["dei:EntityCommonStockSharesOutstanding"] = [
        {
            "val": 95_000_000,
            "end": "2026-02-25",  # +56 days after 2025-12-31
            "filed": "2026-03-01",
            "form": "10-K",
            "unit": "shares",
        }
    ]
    ef_late = build_synthetic_entity_facts(concepts_late)
    period_late = normalize_entity_facts(ef_late, years=1, strict=True).periods[0]
    assert period_late.ending_shares is None


def test_deduplication_prefers_latest_filing_and_ignores_10q():
    concepts = standard_year_concepts(
        filed="2026-02-01",
        form="10-K",
        accn="0000999999-26-000001",
    )
    # Add a 10-K/A restatement filed later with updated revenue
    concepts["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"].append(
        {
            "val": 1_050_000_000,
            "start": "2025-01-01",
            "end": "2025-12-31",
            "filed": "2026-04-15",
            "form": "10-K/A",
            "accn": "0000999999-26-000099",
            "unit": "USD",
        }
    )
    # Add a 10-Q that must be ignored even if filed later
    concepts["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"].append(
        {
            "val": 999_999,
            "start": "2025-01-01",
            "end": "2025-12-31",
            "filed": "2026-05-01",
            "form": "10-Q",
            "unit": "USD",
        }
    )
    ef = build_synthetic_entity_facts(concepts)
    report = normalize_entity_facts(ef, years=1, strict=True)
    assert len(report.periods) == 1
    assert report.periods[0].revenue == Decimal("1050000000")
    assert report.periods[0].form_type == "10-K/A"


def test_equity_includes_nci_and_aggregate_debt_anti_double_count():
    concepts = standard_year_concepts(
        omit={
            "us-gaap:StockholdersEquity",
            "us-gaap:ShortTermBorrowings",
            "us-gaap:LongTermDebtNoncurrent",
            "us-gaap:OperatingLeaseLiabilityCurrent",
            "us-gaap:OperatingLeaseLiabilityNoncurrent",
        }
    )
    # Equity including NCI + separate MinorityInterest must not double count
    concepts["us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"] = [
        {"val": 550_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]
    concepts["us-gaap:MinorityInterest"] = [
        {"val": 30_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]
    # Aggregate LongTermDebt (220M) + LongTermDebtCurrent (20M) -> LT debt should be 200M
    concepts["us-gaap:LongTermDebtCurrent"] = [
        {"val": 20_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]
    concepts["us-gaap:LongTermDebt"] = [
        {"val": 220_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]
    # Aggregate OperatingLeaseLiability fallback
    concepts["us-gaap:OperatingLeaseLiability"] = [
        {"val": 50_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]

    ef = build_synthetic_entity_facts(concepts)
    report = validate_financials(normalize_entity_facts(ef, years=1), expected_years=1)
    p = report.periods[0]

    assert p.equity_includes_nci is True
    assert p.total_equity_for_balance_check == Decimal("550000000")
    assert p.short_term_debt == Decimal("20000000")
    assert p.long_term_debt == Decimal("200000000")
    assert p.financial_debt == Decimal("220000000")
    assert p.current_lease_liabilities == Decimal("0")
    assert p.noncurrent_lease_liabilities == Decimal("50000000")
    assert report.is_valid is True

    codes = {i.code for i in report.validation_issues}
    assert "AGGREGATE_DEBT_TAG_USED" in codes
    assert "AGGREGATE_LEASE_TAG_USED" in codes


def test_total_liabilities_derivation_hierarchy_and_warnings():
    # 1. LiabilitiesCurrent + LiabilitiesNoncurrent (independent of Assets)
    concepts_components = standard_year_concepts(omit={"us-gaap:Liabilities"})
    concepts_components["us-gaap:LiabilitiesNoncurrent"] = [
        {"val": 270_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]
    ef_comp = build_synthetic_entity_facts(concepts_components)
    rep_comp = validate_financials(normalize_entity_facts(ef_comp, years=1), expected_years=1)
    assert rep_comp.periods[0].total_liabilities == Decimal("450000000")
    assert rep_comp.periods[0].audit_trail["total_liabilities"].source_kind == "derived"
    assert not any(
        i.code == "BALANCE_SHEET_IDENTITY_UNVERIFIED_DERIVED_LIABILITIES"
        for i in rep_comp.validation_issues
    )

    # 2. Last resort: derived_from_assets triggers warning
    concepts_assets_only = standard_year_concepts(omit={"us-gaap:Liabilities"})
    ef_assets = build_synthetic_entity_facts(concepts_assets_only)
    rep_assets = validate_financials(normalize_entity_facts(ef_assets, years=1), expected_years=1)
    assert rep_assets.periods[0].audit_trail["total_liabilities"].source_kind == "derived_from_assets"
    assert any(
        i.code == "BALANCE_SHEET_IDENTITY_UNVERIFIED_DERIVED_LIABILITIES" and i.severity == "warning"
        for i in rep_assets.validation_issues
    )


def test_ebit_fallback_and_restricted_cash_warnings():
    concepts = standard_year_concepts(
        omit={
            "us-gaap:OperatingIncomeLoss",
            "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        }
    )
    concepts["us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"] = [
        {"val": 100_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]
    ef = build_synthetic_entity_facts(concepts)
    rep = normalize_entity_facts(ef, years=1)
    p = rep.periods[0]
    assert p.ebit == Decimal("200000000")  # pretax 190M + interest 10M
    assert p.audit_trail["ebit"].source_kind == "derived"
    codes = {i.code for i in rep.validation_issues}
    assert "EBIT_DERIVED_FALLBACK" in codes
    assert "CASH_INCLUDES_RESTRICTED_CASH" in codes


def test_edgar_client_identity_and_financial_institution_guards(monkeypatch):
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    with pytest.raises(EdgarIdentityError):
        EdgarClient(identity=None, load_env=False)

    monkeypatch.setattr("sec_dcf.ingestion.edgar_client.set_identity", lambda _: None)
    mock_company = MagicMock()
    mock_company.not_found = False
    mock_company.is_financial_institution.return_value = True
    mock_company.name = "JPMorgan Chase & Co."
    mock_company.sic = "6021"
    monkeypatch.setattr("sec_dcf.ingestion.edgar_client.Company", lambda _: mock_company)

    client = EdgarClient(identity="AuditEngine test@example.com", load_env=False)
    with pytest.raises(UnsupportedCompanyTypeError):
        client.fetch_raw_company_and_facts("JPM")


def test_da_derived_from_depreciation_plus_amortization_without_rou_double_count():
    concepts = standard_year_concepts(omit={"us-gaap:DepreciationDepletionAndAmortization"})
    concepts["us-gaap:Depreciation"] = [
        {
            "val": 40_000_000,
            "start": "2025-01-01",
            "end": "2025-12-31",
            "filed": "2026-02-01",
            "form": "10-K",
            "unit": "USD",
        }
    ]
    concepts["us-gaap:AmortizationOfIntangibleAssets"] = [
        {
            "val": 10_000_000,
            "start": "2025-01-01",
            "end": "2025-12-31",
            "filed": "2026-02-01",
            "form": "10-K",
            "unit": "USD",
        }
    ]
    # Must NOT be added to D&A
    concepts["us-gaap:FinanceLeaseRightOfUseAssetAmortization"] = [
        {
            "val": 8_000_000,
            "start": "2025-01-01",
            "end": "2025-12-31",
            "filed": "2026-02-01",
            "form": "10-K",
            "unit": "USD",
        }
    ]

    ef = build_synthetic_entity_facts(concepts)
    period = normalize_entity_facts(ef, years=1, strict=True).periods[0]
    assert period.da == Decimal("50000000")
    assert period.audit_trail["da"].source_kind == "derived"
    assert "Depreciation" in (period.audit_trail["da"].note or "")
    assert "AmortizationOfIntangibleAssets" in (period.audit_trail["da"].note or "")

    # Fallback to Depreciation alone when AmortizationOfIntangibleAssets is absent
    concepts_dep_only = standard_year_concepts(omit={"us-gaap:DepreciationDepletionAndAmortization"})
    concepts_dep_only["us-gaap:Depreciation"] = concepts["us-gaap:Depreciation"]
    ef_dep = build_synthetic_entity_facts(concepts_dep_only)
    period_dep = normalize_entity_facts(ef_dep, years=1, strict=True).periods[0]
    assert period_dep.da == Decimal("40000000")
    assert period_dep.audit_trail["da"].source_kind == "tag_fallback"


def test_operating_lease_current_derived_from_total_minus_noncurrent():
    concepts = standard_year_concepts(omit={"us-gaap:OperatingLeaseLiabilityCurrent"})
    concepts["us-gaap:OperatingLeaseLiability"] = [
        {"val": 50_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]

    ef = build_synthetic_entity_facts(concepts)
    rep = validate_financials(normalize_entity_facts(ef, years=1), expected_years=1)
    p = rep.periods[0]
    # Total 50M - Noncurrent 40M = Current 10M
    assert p.current_lease_liabilities == Decimal("10000000")
    assert p.noncurrent_lease_liabilities == Decimal("40000000")
    assert p.lease_liabilities == Decimal("50000000")
    assert p.audit_trail["current_lease_liabilities"].source_kind == "derived"
    # Operating CA = 300M - 150M = 150M; Operating CL = 180M - 20M - 10M = 150M -> NWC = 0
    assert p.non_cash_nwc == Decimal("0")


def test_aggregate_finance_lease_liability_fallback_and_anti_double_count():
    concepts = standard_year_concepts()
    concepts["us-gaap:FinanceLeaseLiability"] = [
        {"val": 60_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]

    ef = build_synthetic_entity_facts(concepts)
    rep = validate_financials(normalize_entity_facts(ef, years=1), expected_years=1)
    p = rep.periods[0]

    assert p.current_lease_liabilities == Decimal("10000000")
    assert p.noncurrent_lease_liabilities == Decimal("100000000")  # 40M op + 60M fin
    assert p.lease_liabilities == Decimal("110000000")
    assert p.audit_trail["noncurrent_lease_liabilities"].source_kind == "derived"
    # Aggregate FinanceLeaseLiability must NOT be subtracted in non_cash_nwc
    assert p.non_cash_nwc == Decimal("0")
    assert any(
        i.code == "AGGREGATE_LEASE_TAG_USED"
        and i.details.get("concept") == "us-gaap:FinanceLeaseLiability"
        for i in rep.validation_issues
    )

    # Anti-double-count: if LongTermDebtAndCapitalLeaseObligations is used, ignore FinanceLeaseLiability
    concepts_comb = standard_year_concepts(omit={"us-gaap:LongTermDebtNoncurrent"})
    concepts_comb["us-gaap:LongTermDebtAndCapitalLeaseObligations"] = [
        {"val": 260_000_000, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "unit": "USD"}
    ]
    concepts_comb["us-gaap:FinanceLeaseLiability"] = concepts["us-gaap:FinanceLeaseLiability"]
    ef_comb = build_synthetic_entity_facts(concepts_comb)
    rep_comb = validate_financials(normalize_entity_facts(ef_comb, years=1), expected_years=1)
    p_comb = rep_comb.periods[0]
    assert p_comb.noncurrent_lease_liabilities == Decimal("40000000")
    assert any(i.code == "DEBT_INCLUDES_FINANCE_LEASES" for i in rep_comb.validation_issues)
