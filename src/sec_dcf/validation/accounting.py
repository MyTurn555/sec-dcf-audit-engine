"""Accounting identity, sign, continuity, and cross-statement validators."""

from __future__ import annotations

from decimal import Decimal

from sec_dcf.exceptions import AccountingValidationError
from sec_dcf.models.financials import (
    AnnualFinancialPeriod,
    CompanyFinancialsReport,
    ValidationIssue,
)

MIN_BALANCE_TOLERANCE_USD = Decimal("1000000")
BALANCE_TOLERANCE_RATIO = Decimal("0.001")  # 0.1% of Total Assets

MIN_INCOME_TAX_TOLERANCE_USD = Decimal("5000000")
INCOME_TAX_TOLERANCE_RATIO = Decimal("0.01")  # 1% of |pretax_income|


def balance_sheet_tolerance(total_assets: Decimal) -> Decimal:
    """Compute allowed rounding tolerance: max($1,000,000, 0.1% * |Total Assets|)."""
    return max(MIN_BALANCE_TOLERANCE_USD, BALANCE_TOLERANCE_RATIO * abs(total_assets))


def _validate_single_period(period: AnnualFinancialPeriod) -> list[ValidationIssue]:
    """Run all per-period deterministic accounting checks."""
    issues: list[ValidationIssue] = []
    fy = period.fiscal_year

    # 1. Balance sheet identity (non-tautological)
    liab_audit = period.audit_trail.get("total_liabilities")
    if liab_audit is not None and liab_audit.source_kind == "derived_from_assets":
        issues.append(
            ValidationIssue(
                code="BALANCE_SHEET_IDENTITY_UNVERIFIED_DERIVED_LIABILITIES",
                severity="warning",
                fiscal_year=fy,
                message=(
                    f"FY{fy}: total_liabilities was derived from total_assets - equity; "
                    "balance sheet identity cannot be treated as independently verified."
                ),
            )
        )
    else:
        rhs = period.total_liabilities + period.total_equity_for_balance_check
        diff = abs(period.total_assets - rhs)
        tol = balance_sheet_tolerance(period.total_assets)
        if diff > tol:
            issues.append(
                ValidationIssue(
                    code="BALANCE_SHEET_IMBALANCE",
                    severity="error",
                    fiscal_year=fy,
                    message=(
                        f"FY{fy}: Assets ({period.total_assets}) != Liabilities + Equity "
                        f"({rhs}); diff={diff} exceeds tolerance={tol}."
                    ),
                    details={
                        "total_assets": str(period.total_assets),
                        "liabilities_plus_equity": str(rhs),
                        "diff": str(diff),
                        "tolerance": str(tol),
                    },
                )
            )

    # 2. Income statement consistency: pretax_income - tax_expense vs net_income
    if period.pretax_income is not None and period.tax_expense is not None:
        implied_ni = period.pretax_income - period.tax_expense
        ni_diff = abs(implied_ni - period.net_income)
        ni_tol = max(
            MIN_INCOME_TAX_TOLERANCE_USD,
            INCOME_TAX_TOLERANCE_RATIO * max(abs(period.pretax_income), Decimal("1")),
        )
        if ni_diff > ni_tol:
            issues.append(
                ValidationIssue(
                    code="PRETAX_TAX_NET_INCOME_MISMATCH",
                    severity="warning",
                    fiscal_year=fy,
                    message=(
                        f"FY{fy}: pretax_income - tax_expense ({implied_ni}) differs from "
                        f"net_income ({period.net_income}) by {ni_diff} (tol={ni_tol})."
                    ),
                    details={
                        "implied_net_income": str(implied_ni),
                        "net_income": str(period.net_income),
                        "diff": str(ni_diff),
                        "tolerance": str(ni_tol),
                    },
                )
            )

    # 3. Sign & sanity checks
    if period.total_assets <= Decimal("0"):
        issues.append(
            ValidationIssue(
                code="NON_POSITIVE_TOTAL_ASSETS",
                severity="error",
                fiscal_year=fy,
                message=f"FY{fy}: total_assets must be positive, got {period.total_assets}.",
            )
        )

    non_negative_fields = (
        ("revenue", period.revenue),
        ("cash_and_equivalents", period.cash_and_equivalents),
        ("short_term_investments", period.short_term_investments),
        ("short_term_debt", period.short_term_debt),
        ("long_term_debt", period.long_term_debt),
        ("current_lease_liabilities", period.current_lease_liabilities),
        ("noncurrent_lease_liabilities", period.noncurrent_lease_liabilities),
        ("capex", period.capex),
    )
    for field_name, val in non_negative_fields:
        if val < Decimal("0"):
            issues.append(
                ValidationIssue(
                    code="NEGATIVE_MAGNITUDE",
                    severity="error",
                    fiscal_year=fy,
                    message=f"FY{fy}: {field_name} must be non-negative, got {val}.",
                    details={"field": field_name, "value": str(val)},
                )
            )

    # 4. Structural balance sheet subset bounds
    if period.current_assets is not None:
        if period.current_assets > period.total_assets + MIN_BALANCE_TOLERANCE_USD:
            issues.append(
                ValidationIssue(
                    code="CURRENT_ASSETS_EXCEED_TOTAL_ASSETS",
                    severity="error",
                    fiscal_year=fy,
                    message=(
                        f"FY{fy}: current_assets ({period.current_assets}) exceeds "
                        f"total_assets ({period.total_assets})."
                    ),
                )
            )
        if period.total_cash > period.current_assets + MIN_BALANCE_TOLERANCE_USD:
            issues.append(
                ValidationIssue(
                    code="CASH_EXCEEDS_CURRENT_ASSETS",
                    severity="warning",
                    fiscal_year=fy,
                    message=(
                        f"FY{fy}: total_cash ({period.total_cash}) exceeds "
                        f"current_assets ({period.current_assets})."
                    ),
                )
            )

    if (
        period.current_liabilities is not None
        and period.current_liabilities > period.total_liabilities + MIN_BALANCE_TOLERANCE_USD
    ):
        issues.append(
            ValidationIssue(
                code="CURRENT_LIABILITIES_EXCEED_TOTAL_LIABILITIES",
                severity="error",
                fiscal_year=fy,
                message=(
                    f"FY{fy}: current_liabilities ({period.current_liabilities}) exceeds "
                    f"total_liabilities ({period.total_liabilities})."
                ),
            )
        )

    return issues


def validate_financials(
    report: CompanyFinancialsReport,
    strict: bool = False,
    expected_years: int = 5,
) -> CompanyFinancialsReport:
    """
    Validate a CompanyFinancialsReport for accounting consistency, period uniqueness,
    and chronological integrity. Returns a new report with all ValidationIssues attached.
    """
    issues: list[ValidationIssue] = list(report.validation_issues)
    sorted_periods = sorted(report.periods, key=lambda p: p.period_end)

    if not sorted_periods:
        issues.append(
            ValidationIssue(
                code="NO_ANNUAL_PERIODS",
                severity="error",
                message=f"{report.ticker}: no valid annual 10-K periods available.",
            )
        )
    else:
        if len(sorted_periods) < expected_years:
            issues.append(
                ValidationIssue(
                    code="INSUFFICIENT_HISTORY",
                    severity="warning",
                    message=(
                        f"{report.ticker}: expected {expected_years} fiscal years, "
                        f"resolved {len(sorted_periods)}."
                    ),
                )
            )

        seen_fiscal_years: set[int] = set()
        seen_period_ends: set[object] = set()
        prev_period: AnnualFinancialPeriod | None = None

        for period in sorted_periods:
            if period.fiscal_year in seen_fiscal_years or period.period_end in seen_period_ends:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_REPORTING_PERIOD",
                        severity="error",
                        fiscal_year=period.fiscal_year,
                        message=(
                            f"Duplicate fiscal period detected: FY{period.fiscal_year} "
                            f"({period.period_end})."
                        ),
                    )
                )
            seen_fiscal_years.add(period.fiscal_year)
            seen_period_ends.add(period.period_end)

            if prev_period is not None:
                gap_days = (period.period_end - prev_period.period_end).days
                if gap_days < 300 or gap_days > 400:
                    issues.append(
                        ValidationIssue(
                            code="IRREGULAR_PERIOD_GAP",
                            severity="error" if gap_days < 300 else "warning",
                            fiscal_year=period.fiscal_year,
                            message=(
                                f"Irregular gap of {gap_days} days between "
                                f"{prev_period.period_end} and {period.period_end}."
                            ),
                            details={"gap_days": str(gap_days)},
                        )
                    )

            issues.extend(_validate_single_period(period))
            prev_period = period

    validated = report.model_copy(
        update={
            "periods": sorted_periods,
            "validation_issues": issues,
        }
    )

    if strict and not validated.is_valid:
        errors = [i for i in validated.validation_issues if i.severity == "error"]
        summary = "; ".join(f"[{e.code}] {e.message}" for e in errors)
        raise AccountingValidationError(
            f"Accounting validation failed for {validated.ticker}: {summary}"
        )

    return validated
