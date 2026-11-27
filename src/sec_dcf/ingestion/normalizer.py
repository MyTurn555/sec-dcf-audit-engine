"""Deterministic normalization of edgartools EntityFacts into canonical annual models."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from edgar.entity import EntityFacts
from edgar.entity.models import FinancialFact

from sec_dcf.exceptions import DataFetchError, MissingCriticalConceptError
from sec_dcf.ingestion.xbrl_mapping import (
    AMORTIZATION_INTANGIBLES_CONCEPT,
    ANCHOR_PERIOD_CONCEPTS,
    CASH_INCLUDES_RESTRICTED_CONCEPT,
    CONCEPT_WINDOW_OVERRIDES,
    DA_AGGREGATE_CONCEPTS,
    DEPRECIATION_CONCEPT,
    DURATION_SPECS,
    EQUITY_INCLUDES_NCI_CONCEPT,
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
    ConceptSpec,
    normalize_concept_name,
)
from sec_dcf.models.financials import (
    AnnualFinancialPeriod,
    AuditSourceKind,
    CompanyFinancialsReport,
    LineItemAudit,
    ValidationIssue,
)

ANNUAL_FORM_TYPES = frozenset({"10-K", "10-K/A"})
MIN_ANNUAL_DURATION_DAYS = 330
MAX_ANNUAL_DURATION_DAYS = 380


def _to_decimal(fact: FinancialFact) -> Decimal | None:
    """Convert FinancialFact value to Decimal without float precision loss where possible."""
    raw = fact.value
    if raw is None:
        raw = fact.numeric_value
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _matches_unit(fact_unit: str | None, expected_unit: str) -> bool:
    """Verify unit compatibility (USD vs shares)."""
    unit = (fact_unit or "").strip().lower()
    if expected_unit == "USD":
        return unit in {"usd", "$"}
    if expected_unit == "shares":
        return unit in {"shares", "share"}
    return unit == expected_unit.lower()


def _is_consolidated_annual_form(fact: FinancialFact) -> bool:
    """Allow only unconsolidated-dimension-free 10-K / 10-K/A facts."""
    if (fact.form_type or "").strip().upper() not in ANNUAL_FORM_TYPES:
        return False
    if getattr(fact, "dimensions", None):
        return False
    return True


def _is_annual_duration(fact: FinancialFact) -> bool:
    """Check whether a fact represents a full fiscal year duration (330-380 days)."""
    if fact.period_start is None or fact.period_end is None:
        return False
    days = (fact.period_end - fact.period_start).days
    return MIN_ANNUAL_DURATION_DAYS <= days <= MAX_ANNUAL_DURATION_DAYS


def _canonical_fiscal_year(period_end: date) -> int:
    """Map a fiscal period_end date to its canonical fiscal year integer."""
    # Early-January 52/53-week closings (e.g. Jan 1-7) belong to the prior calendar year.
    if period_end.month == 1 and period_end.day <= 7:
        return period_end.year - 1
    return period_end.year


def _fact_priority_key(
    fact: FinancialFact,
    canonical_end: date,
) -> tuple[date, int, int, str]:
    """
    Deterministic ranking key for deduplicating comparative / restated facts.

    Latest filing_date wins (restatements / latest comparative 10-K override earlier
    filings). Ties break on 10-K/A over 10-K, closest period_end, then accession.
    """
    filed = fact.filing_date or date.min
    is_amended = 1 if (fact.form_type or "").upper() == "10-K/A" else 0
    date_distance = -abs((fact.period_end - canonical_end).days) if fact.period_end else -999
    accession = fact.accession or ""
    return (filed, is_amended, date_distance, accession)


def _index_facts(facts: Iterable[FinancialFact]) -> dict[str, list[FinancialFact]]:
    """Group consolidated 10-K facts by normalized concept name."""
    indexed: dict[str, list[FinancialFact]] = {}
    for fact in facts:
        if not _is_consolidated_annual_form(fact):
            continue
        if fact.period_end is None:
            continue
        concept_key = normalize_concept_name(fact.concept)
        indexed.setdefault(concept_key, []).append(fact)
    return indexed


def _discover_annual_period_ends(
    indexed_facts: dict[str, list[FinancialFact]],
    years: int,
) -> list[date]:
    """Identify the most recent `years` distinct annual 10-K period_end dates."""
    by_fiscal_year: dict[int, tuple[date, date]] = {}
    for concept in ANCHOR_PERIOD_CONCEPTS:
        for fact in indexed_facts.get(concept, ()):
            if not _is_annual_duration(fact):
                continue
            if not _matches_unit(fact.unit, "USD"):
                continue
            if _to_decimal(fact) is None:
                continue
            fy = _canonical_fiscal_year(fact.period_end)
            filed = fact.filing_date or date.min
            existing = by_fiscal_year.get(fy)
            if existing is None or filed > existing[1]:
                by_fiscal_year[fy] = (fact.period_end, filed)

    if not by_fiscal_year:
        raise DataFetchError(
            "No annual 10-K duration anchor facts (revenue/net income/CFO) found."
        )

    sorted_years = sorted(by_fiscal_year.keys(), reverse=True)[:years]
    selected_ends = [by_fiscal_year[fy][0] for fy in sorted_years]
    selected_ends.sort()
    return selected_ends


def _select_fact_for_concept(
    indexed_facts: dict[str, list[FinancialFact]],
    concept: str,
    canonical_end: date,
    period_kind: str,
    unit: str,
    window_before_days: int = 7,
    window_after_days: int = 7,
) -> FinancialFact | None:
    """Pick the single best fact for a concept and canonical period_end."""
    win_before, win_after = CONCEPT_WINDOW_OVERRIDES.get(
        concept, (window_before_days, window_after_days)
    )
    min_end = canonical_end - timedelta(days=win_before)
    max_end = canonical_end + timedelta(days=win_after)

    candidates: list[FinancialFact] = []
    for fact in indexed_facts.get(concept, ()):
        if fact.period_end is None or not (min_end <= fact.period_end <= max_end):
            continue
        if period_kind == "duration":
            if not _is_annual_duration(fact):
                continue
        else:
            if fact.period_type != "instant" and fact.period_start is not None:
                continue
        if not _matches_unit(fact.unit, unit):
            continue
        if _to_decimal(fact) is None:
            continue
        candidates.append(fact)

    if not candidates:
        return None
    return max(candidates, key=lambda f: _fact_priority_key(f, canonical_end))


def _make_audit(
    field_name: str,
    fact: FinancialFact | None,
    source_kind: AuditSourceKind,
    note: str | None = None,
    raw_value_override: Decimal | None = None,
) -> LineItemAudit:
    """Create a LineItemAudit record from a selected FinancialFact or derivation."""
    if fact is None:
        return LineItemAudit(
            field_name=field_name,
            source_kind=source_kind,
            raw_value=raw_value_override,
            note=note,
        )
    return LineItemAudit(
        field_name=field_name,
        concept=fact.concept,
        label=fact.label,
        raw_value=raw_value_override if raw_value_override is not None else _to_decimal(fact),
        raw_unit=fact.unit,
        period_start=fact.period_start,
        period_end=fact.period_end,
        filing_date=fact.filing_date,
        form_type=fact.form_type,
        accession=fact.accession,
        source_kind=source_kind,
        note=note,
    )


def _resolve_from_spec(
    indexed_facts: dict[str, list[FinancialFact]],
    spec: ConceptSpec,
    canonical_end: date,
) -> tuple[Decimal | None, LineItemAudit, FinancialFact | None]:
    """Resolve a ConceptSpec across its ordered priority concept list."""
    for idx, concept in enumerate(spec.concepts):
        fact = _select_fact_for_concept(
            indexed_facts=indexed_facts,
            concept=concept,
            canonical_end=canonical_end,
            period_kind=spec.period_kind,
            unit=spec.unit,
            window_before_days=spec.window_before_days,
            window_after_days=spec.window_after_days,
        )
        if fact is not None:
            value = _to_decimal(fact)
            source_kind: AuditSourceKind = "tag_exact" if idx == 0 else "tag_fallback"
            return value, _make_audit(spec.field_name, fact, source_kind), fact

    if spec.default_if_absent is not None:
        return (
            spec.default_if_absent,
            _make_audit(
                spec.field_name,
                None,
                "zero_if_absent",
                note="Absent in 10-K facts; defaulted to 0.",
                raw_value_override=spec.default_if_absent,
            ),
            None,
        )

    return (
        None,
        _make_audit(spec.field_name, None, "missing", note="No matching XBRL tag found."),
        None,
    )


def _resolve_da(
    indexed_facts: dict[str, list[FinancialFact]],
    canonical_end: date,
) -> tuple[Decimal | None, LineItemAudit, FinancialFact | None]:
    """
    Resolve D&A preferring aggregate tags, then Depreciation + AmortizationOfIntangibleAssets,
    then Depreciation alone (excluding FinanceLeaseRightOfUseAssetAmortization to avoid
    double counting).
    """
    for idx, concept in enumerate(DA_AGGREGATE_CONCEPTS):
        fact = _select_fact_for_concept(
            indexed_facts=indexed_facts,
            concept=concept,
            canonical_end=canonical_end,
            period_kind="duration",
            unit="USD",
        )
        if fact is not None:
            val = _to_decimal(fact)
            if val is not None:
                source_kind: AuditSourceKind = "tag_exact" if idx == 0 else "tag_fallback"
                return val, _make_audit("da", fact, source_kind), fact

    dep_fact = _select_fact_for_concept(
        indexed_facts, DEPRECIATION_CONCEPT, canonical_end, "duration", "USD"
    )
    amort_fact = _select_fact_for_concept(
        indexed_facts, AMORTIZATION_INTANGIBLES_CONCEPT, canonical_end, "duration", "USD"
    )
    if dep_fact is not None and amort_fact is not None:
        dep_val = _to_decimal(dep_fact)
        amort_val = _to_decimal(amort_fact)
        if dep_val is not None and amort_val is not None:
            total_da = dep_val + amort_val
            return (
                total_da,
                _make_audit(
                    "da",
                    dep_fact,
                    "derived",
                    note=(
                        f"Derived as {DEPRECIATION_CONCEPT} ({dep_val}) + "
                        f"{AMORTIZATION_INTANGIBLES_CONCEPT} ({amort_val})."
                    ),
                    raw_value_override=total_da,
                ),
                dep_fact,
            )

    if dep_fact is not None:
        dep_val = _to_decimal(dep_fact)
        if dep_val is not None:
            return dep_val, _make_audit("da", dep_fact, "tag_fallback"), dep_fact

    return (
        None,
        _make_audit("da", None, "missing", note="No matching D&A XBRL tag found."),
        None,
    )


def _resolve_debt_and_leases(
    indexed_facts: dict[str, list[FinancialFact]],
    canonical_end: date,
    fiscal_year: int,
    issues: list[ValidationIssue],
) -> tuple[Decimal, Decimal, Decimal, Decimal, dict[str, LineItemAudit]]:
    """
    Resolve short-term debt, long-term debt, and current/noncurrent leases
    without double-counting overlapping aggregate XBRL tags.
    """
    audits: dict[str, LineItemAudit] = {}

    # 1. Short-term debt
    st_debt = Decimal("0")
    st_used_aggregate = False
    st_long_term_current_fact = _select_fact_for_concept(
        indexed_facts, "us-gaap:LongTermDebtCurrent", canonical_end, "instant", "USD"
    )

    for idx, concept in enumerate(SHORT_TERM_DEBT_AGGREGATE_CONCEPTS):
        fact = _select_fact_for_concept(indexed_facts, concept, canonical_end, "instant", "USD")
        if fact is not None:
            st_debt = _to_decimal(fact) or Decimal("0")
            st_used_aggregate = True
            audits["short_term_debt"] = _make_audit(
                "short_term_debt",
                fact,
                "tag_exact" if idx == 0 else "tag_fallback",
            )
            break

    if not st_used_aggregate:
        component_facts: list[FinancialFact] = []
        for concept in SHORT_TERM_DEBT_COMPONENT_CONCEPTS:
            fact = _select_fact_for_concept(indexed_facts, concept, canonical_end, "instant", "USD")
            if fact is not None:
                val = _to_decimal(fact)
                if val is not None:
                    st_debt += val
                    component_facts.append(fact)

        if component_facts:
            joined = " + ".join(f.concept for f in component_facts)
            audits["short_term_debt"] = _make_audit(
                "short_term_debt",
                component_facts[0],
                "derived" if len(component_facts) > 1 else "tag_fallback",
                note=f"Resolved from components: {joined}",
                raw_value_override=st_debt,
            )
        else:
            audits["short_term_debt"] = _make_audit(
                "short_term_debt",
                None,
                "zero_if_absent",
                note="No short-term debt tags found; defaulted to 0.",
                raw_value_override=Decimal("0"),
            )

    # 2. Long-term debt (noncurrent preferred; guard against double counting)
    lt_debt = Decimal("0")
    debt_includes_finance_leases = False

    lt_primary = _select_fact_for_concept(
        indexed_facts, LONG_TERM_DEBT_PRIMARY_CONCEPT, canonical_end, "instant", "USD"
    )
    if lt_primary is not None:
        lt_debt = _to_decimal(lt_primary) or Decimal("0")
        audits["long_term_debt"] = _make_audit("long_term_debt", lt_primary, "tag_exact")
    else:
        lt_resolved = False
        for concept in LONG_TERM_DEBT_AGGREGATE_FALLBACKS:
            fact = _select_fact_for_concept(indexed_facts, concept, canonical_end, "instant", "USD")
            if fact is None:
                continue
            raw_lt = _to_decimal(fact) or Decimal("0")
            if concept == "us-gaap:LongTermDebt":
                # Avoid double-counting current portion when LongTermDebtCurrent is known
                current_portion = (
                    _to_decimal(st_long_term_current_fact)
                    if st_long_term_current_fact is not None
                    else None
                )
                if current_portion is not None and raw_lt >= current_portion and st_debt >= current_portion:
                    lt_debt = raw_lt - current_portion
                    note = (
                        "Derived as LongTermDebt - LongTermDebtCurrent to avoid "
                        "double-counting current debt."
                    )
                    source_kind: AuditSourceKind = "derived"
                else:
                    lt_debt = raw_lt
                    note = (
                        "Used aggregate LongTermDebt; current debt could not be cleanly "
                        "subtracted."
                    )
                    source_kind = "tag_fallback"
                issues.append(
                    ValidationIssue(
                        code="AGGREGATE_DEBT_TAG_USED",
                        severity="warning",
                        fiscal_year=fiscal_year,
                        message=(
                            f"FY{fiscal_year}: long_term_debt used aggregate tag "
                            f"{concept}; verify no overlap with short_term_debt."
                        ),
                        details={"concept": concept},
                    )
                )
                audits["long_term_debt"] = _make_audit(
                    "long_term_debt",
                    fact,
                    source_kind,
                    note=note,
                    raw_value_override=raw_lt,
                )
            else:
                # us-gaap:LongTermDebtAndCapitalLeaseObligations
                lt_debt = raw_lt
                debt_includes_finance_leases = True
                issues.append(
                    ValidationIssue(
                        code="DEBT_INCLUDES_FINANCE_LEASES",
                        severity="warning",
                        fiscal_year=fiscal_year,
                        message=(
                            f"FY{fiscal_year}: long_term_debt uses {concept}; "
                            "finance lease liabilities are excluded from lease_liabilities "
                            "to prevent double-counting."
                        ),
                        details={"concept": concept},
                    )
                )
                audits["long_term_debt"] = _make_audit(
                    "long_term_debt",
                    fact,
                    "tag_fallback",
                    note="Includes capital/finance lease obligations.",
                )
            lt_resolved = True
            break

        if not lt_resolved:
            audits["long_term_debt"] = _make_audit(
                "long_term_debt",
                None,
                "zero_if_absent",
                note="No long-term debt tags found; defaulted to 0.",
                raw_value_override=Decimal("0"),
            )

    # 3. Lease liabilities split into current and noncurrent
    curr_lease = Decimal("0")
    noncurr_lease = Decimal("0")

    op_curr = _select_fact_for_concept(
        indexed_facts, OPERATING_LEASE_CURRENT_CONCEPT, canonical_end, "instant", "USD"
    )
    op_noncurr = _select_fact_for_concept(
        indexed_facts, OPERATING_LEASE_NONCURRENT_CONCEPT, canonical_end, "instant", "USD"
    )
    op_total = _select_fact_for_concept(
        indexed_facts, OPERATING_LEASE_TOTAL_FALLBACK, canonical_end, "instant", "USD"
    )

    fin_curr = (
        None
        if debt_includes_finance_leases
        else _select_fact_for_concept(
            indexed_facts, FINANCE_LEASE_CURRENT_CONCEPT, canonical_end, "instant", "USD"
        )
    )
    fin_noncurr = (
        None
        if debt_includes_finance_leases
        else _select_fact_for_concept(
            indexed_facts, FINANCE_LEASE_NONCURRENT_CONCEPT, canonical_end, "instant", "USD"
        )
    )
    fin_total = (
        None
        if (debt_includes_finance_leases or fin_curr is not None or fin_noncurr is not None)
        else _select_fact_for_concept(
            indexed_facts, FINANCE_LEASE_TOTAL_FALLBACK, canonical_end, "instant", "USD"
        )
    )

    # Current lease liabilities: direct OperatingLeaseLiabilityCurrent or derived
    # from OperatingLeaseLiability - OperatingLeaseLiabilityNoncurrent, plus FinanceLeaseCurrent.
    op_curr_derived_val: Decimal | None = None
    if op_curr is None and op_total is not None and op_noncurr is not None:
        total_val = _to_decimal(op_total)
        noncurr_val = _to_decimal(op_noncurr)
        if total_val is not None and noncurr_val is not None and total_val >= noncurr_val:
            op_curr_derived_val = total_val - noncurr_val

    curr_parts: list[str] = []
    curr_anchor_fact: FinancialFact | None = None
    if op_curr is not None:
        curr_lease += _to_decimal(op_curr) or Decimal("0")
        curr_parts.append(op_curr.concept)
        curr_anchor_fact = op_curr
    elif op_curr_derived_val is not None:
        curr_lease += op_curr_derived_val
        curr_parts.append(
            f"({OPERATING_LEASE_TOTAL_FALLBACK} - {OPERATING_LEASE_NONCURRENT_CONCEPT})"
        )
        curr_anchor_fact = op_total

    if fin_curr is not None:
        curr_lease += _to_decimal(fin_curr) or Decimal("0")
        curr_parts.append(fin_curr.concept)
        if curr_anchor_fact is None:
            curr_anchor_fact = fin_curr

    if curr_parts:
        is_exact_curr = op_curr is not None and fin_curr is None
        audits["current_lease_liabilities"] = _make_audit(
            "current_lease_liabilities",
            curr_anchor_fact,
            "tag_exact" if is_exact_curr else "derived",
            note=" + ".join(curr_parts),
            raw_value_override=curr_lease,
        )
    else:
        audits["current_lease_liabilities"] = _make_audit(
            "current_lease_liabilities",
            None,
            "zero_if_absent",
            note="No current lease liability tags found; defaulted to 0.",
            raw_value_override=Decimal("0"),
        )

    # Noncurrent lease liabilities: Operating noncurrent (or total fallback when split missing)
    # + Finance noncurrent (or aggregate FinanceLeaseLiability fallback).
    noncurr_parts: list[str] = []
    noncurr_anchor_fact: FinancialFact | None = None
    noncurr_used_aggregate = False

    if op_noncurr is not None:
        noncurr_lease += _to_decimal(op_noncurr) or Decimal("0")
        noncurr_parts.append(op_noncurr.concept)
        noncurr_anchor_fact = op_noncurr
    elif op_curr is None and op_total is not None:
        noncurr_lease += _to_decimal(op_total) or Decimal("0")
        noncurr_parts.append(op_total.concept)
        noncurr_anchor_fact = op_total
        noncurr_used_aggregate = True
        issues.append(
            ValidationIssue(
                code="AGGREGATE_LEASE_TAG_USED",
                severity="warning",
                fiscal_year=fiscal_year,
                message=(
                    f"FY{fiscal_year}: OperatingLeaseLiabilityCurrent/Noncurrent "
                    "absent; assigned total OperatingLeaseLiability to "
                    "noncurrent_lease_liabilities."
                ),
                details={"concept": op_total.concept},
            )
        )

    if fin_noncurr is not None:
        noncurr_lease += _to_decimal(fin_noncurr) or Decimal("0")
        noncurr_parts.append(fin_noncurr.concept)
        if noncurr_anchor_fact is None:
            noncurr_anchor_fact = fin_noncurr
    elif fin_total is not None:
        noncurr_lease += _to_decimal(fin_total) or Decimal("0")
        noncurr_parts.append(fin_total.concept)
        if noncurr_anchor_fact is None:
            noncurr_anchor_fact = fin_total
        noncurr_used_aggregate = True
        issues.append(
            ValidationIssue(
                code="AGGREGATE_LEASE_TAG_USED",
                severity="warning",
                fiscal_year=fiscal_year,
                message=(
                    f"FY{fiscal_year}: FinanceLeaseLiabilityCurrent/Noncurrent "
                    f"absent; assigned total {fin_total.concept} to "
                    "noncurrent_lease_liabilities (excluded from non_cash_nwc)."
                ),
                details={"concept": fin_total.concept},
            )
        )

    if noncurr_parts:
        if len(noncurr_parts) == 1 and op_noncurr is not None:
            nc_kind: AuditSourceKind = "tag_exact"
        elif len(noncurr_parts) == 1 and noncurr_used_aggregate:
            nc_kind = "tag_fallback"
        else:
            nc_kind = "derived"
        audits["noncurrent_lease_liabilities"] = _make_audit(
            "noncurrent_lease_liabilities",
            noncurr_anchor_fact,
            nc_kind,
            note=" + ".join(noncurr_parts),
            raw_value_override=noncurr_lease,
        )
    else:
        audits["noncurrent_lease_liabilities"] = _make_audit(
            "noncurrent_lease_liabilities",
            None,
            "zero_if_absent",
            note="No noncurrent lease liability tags found; defaulted to 0.",
            raw_value_override=Decimal("0"),
        )

    return st_debt, lt_debt, curr_lease, noncurr_lease, audits


def _resolve_total_liabilities(
    indexed_facts: dict[str, list[FinancialFact]],
    canonical_end: date,
    total_assets: Decimal,
    total_equity: Decimal,
) -> tuple[Decimal, LineItemAudit]:
    """
    Resolve total_liabilities in non-tautological order:
      1. Direct us-gaap:Liabilities
      2. LiabilitiesCurrent + LiabilitiesNoncurrent (independent of Assets)
      3. Last resort: total_assets - total_equity (flagged as derived_from_assets)
    """
    direct = _select_fact_for_concept(
        indexed_facts, LIABILITIES_DIRECT_CONCEPT, canonical_end, "instant", "USD"
    )
    if direct is not None:
        val = _to_decimal(direct)
        if val is not None:
            return val, _make_audit("total_liabilities", direct, "tag_exact")

    curr = _select_fact_for_concept(
        indexed_facts, LIABILITIES_CURRENT_CONCEPT, canonical_end, "instant", "USD"
    )
    noncurr = _select_fact_for_concept(
        indexed_facts, LIABILITIES_NONCURRENT_CONCEPT, canonical_end, "instant", "USD"
    )
    if curr is not None and noncurr is not None:
        curr_val = _to_decimal(curr)
        noncurr_val = _to_decimal(noncurr)
        if curr_val is not None and noncurr_val is not None:
            summed = curr_val + noncurr_val
            return summed, _make_audit(
                "total_liabilities",
                curr,
                "derived",
                note="Derived as LiabilitiesCurrent + LiabilitiesNoncurrent.",
                raw_value_override=summed,
            )

    derived_from_assets = total_assets - total_equity
    return derived_from_assets, _make_audit(
        "total_liabilities",
        None,
        "derived_from_assets",
        note="Derived from Total Assets - Total Equity; balance sheet identity cannot be independently verified.",
        raw_value_override=derived_from_assets,
    )


def normalize_entity_facts(
    facts: EntityFacts,
    ticker: str | None = None,
    sic: str | None = None,
    fiscal_year_end: str | None = None,
    years: int = 5,
    strict: bool = False,
) -> CompanyFinancialsReport:
    """
    Normalize an edgartools EntityFacts object into a CompanyFinancialsReport
    covering up to the most recent `years` annual 10-K fiscal periods.
    """
    if years < 1:
        raise ValueError("years must be >= 1")

    raw_facts = facts.get_all_facts()
    indexed = _index_facts(raw_facts)
    canonical_ends = _discover_annual_period_ends(indexed, years=years)

    periods: list[AnnualFinancialPeriod] = []
    issues: list[ValidationIssue] = []

    for canonical_end in canonical_ends:
        fy = _canonical_fiscal_year(canonical_end)
        values: dict[str, Decimal | None] = {}
        audits: dict[str, LineItemAudit] = {}
        selected_facts: dict[str, FinancialFact | None] = {}

        # 1. Resolve standard duration and instant specs, plus D&A
        for spec in (*DURATION_SPECS, *INSTANT_SPECS):
            val, audit, fact = _resolve_from_spec(indexed, spec, canonical_end)
            values[spec.field_name] = val
            audits[spec.field_name] = audit
            selected_facts[spec.field_name] = fact

        da_val, da_audit, da_fact = _resolve_da(indexed, canonical_end)
        values["da"] = da_val
        audits["da"] = da_audit
        selected_facts["da"] = da_fact

        # 2. Cash restricted-cash warning
        cash_fact = selected_facts.get("cash_and_equivalents")
        if cash_fact is not None and cash_fact.concept == CASH_INCLUDES_RESTRICTED_CONCEPT:
            issues.append(
                ValidationIssue(
                    code="CASH_INCLUDES_RESTRICTED_CASH",
                    severity="warning",
                    fiscal_year=fy,
                    message=(
                        f"FY{fy}: cash_and_equivalents sourced from {cash_fact.concept} "
                        "which includes restricted cash."
                    ),
                    details={"concept": cash_fact.concept},
                )
            )

        # 3. EBIT derived fallback if OperatingIncomeLoss is absent
        if values["ebit"] is None:
            pretax = values.get("pretax_income")
            interest = values.get("interest_expense") or Decimal("0")
            if pretax is not None:
                derived_ebit = pretax + interest
                values["ebit"] = derived_ebit
                audits["ebit"] = _make_audit(
                    "ebit",
                    selected_facts.get("pretax_income"),
                    "derived",
                    note="Fallback EBIT derived as pretax_income + interest_expense.",
                    raw_value_override=derived_ebit,
                )
                issues.append(
                    ValidationIssue(
                        code="EBIT_DERIVED_FALLBACK",
                        severity="warning",
                        fiscal_year=fy,
                        message=(
                            f"FY{fy}: OperatingIncomeLoss missing; EBIT derived as "
                            "pretax_income + interest_expense (may include non-operating items)."
                        ),
                    )
                )

        # 4. Enforce required fields before constructing AnnualFinancialPeriod
        required_missing = [
            spec.field_name
            for spec in (*DURATION_SPECS, *INSTANT_SPECS)
            if spec.required and values.get(spec.field_name) is None
        ]
        if values.get("da") is None:
            required_missing.append("da")
        if required_missing:
            msg = f"FY{fy} ({canonical_end}): missing required XBRL concepts: {', '.join(required_missing)}"
            if strict:
                raise MissingCriticalConceptError(msg)
            issues.append(
                ValidationIssue(
                    code="MISSING_CRITICAL_CONCEPTS",
                    severity="error",
                    fiscal_year=fy,
                    message=msg,
                    details={"missing_fields": ",".join(required_missing)},
                )
            )
            continue

        # 5. Normalize CapEx to non-negative cash outflow
        raw_capex = values["capex"]
        assert raw_capex is not None
        capex_fact = selected_facts["capex"]
        normalized_capex = abs(raw_capex)
        if raw_capex < Decimal("0"):
            audits["capex"] = _make_audit(
                "capex",
                capex_fact,
                audits["capex"].source_kind,
                note=f"Normalized negative raw SEC CapEx ({raw_capex}) to non-negative outflow.",
                raw_value_override=raw_capex,
            )
        values["capex"] = normalized_capex

        # 6. Equity & NCI double-counting guard
        equity_fact = selected_facts.get("stockholders_equity")
        equity_includes_nci = (
            equity_fact is not None and equity_fact.concept == EQUITY_INCLUDES_NCI_CONCEPT
        )
        stockholders_equity = values["stockholders_equity"]
        assert stockholders_equity is not None
        nci = values["noncontrolling_interest"] or Decimal("0")
        total_equity_for_check = (
            stockholders_equity if equity_includes_nci else stockholders_equity + nci
        )

        # 7. Total liabilities (non-tautological hierarchy)
        total_assets = values["total_assets"]
        assert total_assets is not None
        total_liabilities, liab_audit = _resolve_total_liabilities(
            indexed, canonical_end, total_assets, total_equity_for_check
        )
        audits["total_liabilities"] = liab_audit

        # 8. Debt & leases (no double counting)
        st_debt, lt_debt, curr_lease, noncurr_lease, debt_audits = _resolve_debt_and_leases(
            indexed, canonical_end, fy, issues
        )
        audits.update(debt_audits)

        # Period-level provenance metadata from primary anchor fact (revenue / assets)
        anchor_fact = (
            selected_facts.get("revenue")
            or selected_facts.get("cfo")
            or selected_facts.get("total_assets")
        )

        period = AnnualFinancialPeriod(
            fiscal_year=fy,
            period_start=anchor_fact.period_start if anchor_fact else None,
            period_end=canonical_end,
            filing_date=anchor_fact.filing_date if anchor_fact else None,
            accession=anchor_fact.accession if anchor_fact else None,
            form_type=anchor_fact.form_type if anchor_fact else "10-K",
            revenue=values["revenue"],  # type: ignore[arg-type]
            ebit=values["ebit"],  # type: ignore[arg-type]
            da=values["da"],  # type: ignore[arg-type]
            interest_expense=values["interest_expense"] or Decimal("0"),
            pretax_income=values["pretax_income"],
            tax_expense=values["tax_expense"],
            net_income=values["net_income"],  # type: ignore[arg-type]
            diluted_shares=values["diluted_shares"],
            cash_and_equivalents=values["cash_and_equivalents"],  # type: ignore[arg-type]
            short_term_investments=values["short_term_investments"] or Decimal("0"),
            current_assets=values["current_assets"],
            total_assets=total_assets,
            current_liabilities=values["current_liabilities"],
            short_term_debt=st_debt,
            long_term_debt=lt_debt,
            current_lease_liabilities=curr_lease,
            noncurrent_lease_liabilities=noncurr_lease,
            total_liabilities=total_liabilities,
            stockholders_equity=stockholders_equity,
            noncontrolling_interest=nci,
            equity_includes_nci=equity_includes_nci,
            ending_shares=values["ending_shares"],
            cfo=values["cfo"],  # type: ignore[arg-type]
            capex=normalized_capex,
            sbc=values["sbc"] if values["sbc"] is not None else Decimal("0"),
            audit_trail=audits,
        )
        periods.append(period)

    resolved_ticker = (ticker or getattr(facts, "_ticker", None) or "").strip().upper()
    return CompanyFinancialsReport(
        ticker=resolved_ticker,
        cik=str(facts.cik).zfill(10),
        company_name=facts.name,
        sic=sic or getattr(facts, "_sic_code", None),
        fiscal_year_end=fiscal_year_end,
        periods=periods,
        validation_issues=issues,
    )
