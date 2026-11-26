"""Pure presentation helpers formatting sec_dcf models for Streamlit and tables."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import (
    DcfAssumptions,
    DcfValuationResult,
    DiscountingConvention,
    HistoricalMetricsReport,
    SensitivityMatrix,
)
from sec_dcf.models.financials import CompanyFinancialsReport

BILLION = Decimal("1000000000")
PERCENT_SCALE = Decimal("100")

LEGAL_DISCLAIMER_PL = (
    "Wyłącznie do celów edukacyjnych, badawczych i inżynierskich. Generowane wskaźniki, "
    "przedziały implikowanej wartości oraz audyt jakościowy nie stanowią rekomendacji "
    "inwestycyjnej, porady inwestycyjnej ani ceny docelowej w rozumieniu ustawy o obrocie "
    "instrumentami finansowymi (u.o.i.f.) oraz rozporządzenia MAR (UE nr 596/2014). "
    "Wszystkie założenia i decyzje analityczne należą wyłącznie do użytkownika."
)

LEGAL_DISCLAIMER_EN = (
    "Strictly for educational, research, and engineering purposes only. Outputs and implied "
    "valuation ranges do not constitute investment advice, a Buy/Sell recommendation, or a "
    "target price under u.o.i.f. or Regulation (EU) No 596/2014 (MAR)."
)

FRED_ATTRIBUTION_TEXT = (
    "Dane makroekonomiczne (DGS10) mogą pochodzić z FRED® API (Federal Reserve Bank of St. Louis) "
    "wyłącznie do celów badawczych/edukacyjnych; produkt nie jest wspierany ani certyfikowany przez St. Louis Fed."
)

# (DcfAssumptions attribute, human-readable DataFrame column label, stored as percentage in editor)
FIVE_YEAR_FIELD_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("revenue_growth", "Wzrost przychodów (%)", True),
    ("ebit_margin", "Marża EBIT (%)", True),
    ("tax_rate", "Stopa podatkowa (%)", True),
    ("da_pct_revenue", "D&A / Przychody (%)", True),
    ("capex_pct_revenue", "CapEx / Przychody (%)", True),
    ("nwc_pct_revenue", "Non-cash NWC / Przychody (%)", True),
)


def _to_billions_float(value: Decimal | None) -> float | None:
    """Convert a USD Decimal amount to USD billions as float for display tables."""
    if value is None:
        return None
    return float(value / BILLION)


def _to_pct_float(value: Decimal | None) -> float | None:
    """Convert a fractional Decimal rate (e.g. 0.25) to percentage points (25.0)."""
    if value is None:
        return None
    return float(value * PERCENT_SCALE)


def build_validation_summary(report: CompanyFinancialsReport) -> dict[str, Any]:
    """Summarize accounting validation status and issue counts for Tab 1."""
    error_count = sum(1 for issue in report.validation_issues if issue.severity == "error")
    warning_count = sum(1 for issue in report.validation_issues if issue.severity == "warning")
    return {
        "ticker": report.ticker,
        "company_name": report.company_name,
        "cik": report.cik,
        "sic": report.sic or "N/A",
        "is_valid": report.is_valid,
        "periods_count": len(report.periods),
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": [
            {
                "Poziom": issue.severity.upper(),
                "Kod": issue.code,
                "Rok": issue.fiscal_year if issue.fiscal_year is not None else "-",
                "Komunikat": issue.message,
            }
            for issue in report.validation_issues
        ],
    }


def build_audit_trail_dataframe(
    report: CompanyFinancialsReport,
    fiscal_year: int,
) -> pd.DataFrame:
    """Return a DataFrame inspecting the XBRL lineage (`audit_trail`) for one fiscal year."""
    target = next((p for p in report.periods if p.fiscal_year == fiscal_year), None)
    if target is None:
        raise ValueError(f"Fiscal year {fiscal_year} not found in report for {report.ticker}.")

    rows = [
        {
            "Pozycja kanoniczna": audit.field_name,
            "Źródło": audit.source_kind,
            "Tag XBRL / Koncept": audit.concept or "-",
            "Wartość surowa": str(audit.raw_value) if audit.raw_value is not None else "-",
            "Jednostka": audit.raw_unit or "-",
            "Okres do": audit.period_end.isoformat() if audit.period_end else "-",
            "Data złożenia": audit.filing_date.isoformat() if audit.filing_date else "-",
            "Accession": audit.accession or "-",
            "Notatka": audit.note or "",
        }
        for audit in target.audit_trail.values()
    ]
    return pd.DataFrame(rows)


def build_historical_metrics_dataframe(metrics: HistoricalMetricsReport) -> pd.DataFrame:
    """Format 5-year deterministic HistoricalMetricsReport into a display DataFrame."""
    rows: list[dict[str, Any]] = []
    for p in metrics.periods:
        net_debt = p.net_debt_incl_leases if metrics.include_leases else p.net_debt_excl_leases
        net_debt_ebitda = (
            p.net_debt_to_ebitda_incl_leases
            if metrics.include_leases
            else p.net_debt_to_ebitda_excl_leases
        )
        rows.append(
            {
                "Rok": f"FY{p.fiscal_year}",
                "Przychody ($ mld)": _to_billions_float(p.revenue),
                "EBIT ($ mld)": _to_billions_float(p.ebit),
                "EBITDA ($ mld)": _to_billions_float(p.ebitda),
                "Zysk netto ($ mld)": _to_billions_float(p.net_income),
                "Simple FCF ($ mld)": _to_billions_float(p.fcf_simple),
                "Marża EBIT (%)": _to_pct_float(p.ebit_margin),
                "Marża EBITDA (%)": _to_pct_float(p.ebitda_margin),
                "Stopa podatkowa NOPAT (%)": _to_pct_float(p.nopat_tax_rate),
                "ROIC (%)": _to_pct_float(p.roic),
                "Konwersja gotówki (%)": _to_pct_float(p.cash_conversion),
                "CapEx / Przychody (%)": _to_pct_float(p.capex_to_revenue),
                "D&A / Przychody (%)": _to_pct_float(p.da_to_revenue),
                "Net Debt ($ mld)": _to_billions_float(net_debt),
                "Net Debt / EBITDA": float(net_debt_ebitda) if net_debt_ebitda is not None else None,
            }
        )
    return pd.DataFrame(rows)


def assumptions_to_5y_dataframe(
    assumptions: DcfAssumptions,
    base_fiscal_year: int,
) -> pd.DataFrame:
    """Convert `DcfAssumptions` 5-year vectors into a percentage DataFrame for `st.data_editor`."""
    rows: list[dict[str, Any]] = []
    for idx in range(5):
        row: dict[str, Any] = {"Rok": f"Y{idx + 1} (FY{base_fiscal_year + idx + 1})"}
        for attr_name, col_label, is_pct in FIVE_YEAR_FIELD_SPECS:
            series = getattr(assumptions, attr_name)
            val = series[idx]
            row[col_label] = float(val * PERCENT_SCALE) if is_pct else float(val)
        rows.append(row)
    return pd.DataFrame(rows)


def _parse_numeric_cell(
    raw_value: Any,
    *,
    col_label: str,
    year_label: str,
    is_pct: bool,
) -> Decimal:
    """Safely parse a `st.data_editor` cell via `Decimal(str(...))` rejecting NaN/empty."""
    if raw_value is None or isinstance(raw_value, bool) or pd.isna(raw_value):
        raise DcfValuationError(
            f"Pusta lub nieprawidłowa wartość w kolumnie '{col_label}' dla roku {year_label}."
        )
    if isinstance(raw_value, float) and (math.isnan(raw_value) or math.isinf(raw_value)):
        raise DcfValuationError(
            f"Wartość nieliczbowa (NaN/Inf) w kolumnie '{col_label}' dla roku {year_label}."
        )
    text = str(raw_value).strip()
    if not text:
        raise DcfValuationError(
            f"Pusta komórka w kolumnie '{col_label}' dla roku {year_label}."
        )
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise DcfValuationError(
            f"Nie udało się sparsować liczby '{text}' w kolumnie '{col_label}' ({year_label})."
        ) from None

    return (parsed / PERCENT_SCALE) if is_pct else parsed


def dataframe_to_dcf_assumptions(
    df: pd.DataFrame,
    *,
    terminal_growth_rate: Decimal,
    discounting_convention: DiscountingConvention,
    include_leases: bool,
    exit_ebitda_multiple: Decimal | None = None,
    initial_nwc_override: Decimal | None = None,
    shares_override: Decimal | None = None,
) -> DcfAssumptions:
    """Parse an edited 5-row percentage DataFrame into validated `DcfAssumptions`."""
    if len(df) != 5:
        raise DcfValuationError(
            f"Tabela założeń 5Y musi zawierać dokładnie 5 wierszy (otrzymano {len(df)})."
        )

    parsed_vectors: dict[str, tuple[Decimal, Decimal, Decimal, Decimal, Decimal]] = {}
    year_labels = [
        str(df.iloc[i]["Rok"]) if "Rok" in df.columns else f"Y{i + 1}"
        for i in range(5)
    ]

    for attr_name, col_label, is_pct in FIVE_YEAR_FIELD_SPECS:
        if col_label not in df.columns:
            raise DcfValuationError(f"Brak wymaganej kolumny '{col_label}' w tabeli założeń 5Y.")
        values = tuple(
            _parse_numeric_cell(
                df.iloc[i][col_label],
                col_label=col_label,
                year_label=year_labels[i],
                is_pct=is_pct,
            )
            for i in range(5)
        )
        parsed_vectors[attr_name] = values  # type: ignore[assignment]

    try:
        return DcfAssumptions(
            revenue_growth=parsed_vectors["revenue_growth"],
            ebit_margin=parsed_vectors["ebit_margin"],
            tax_rate=parsed_vectors["tax_rate"],
            da_pct_revenue=parsed_vectors["da_pct_revenue"],
            capex_pct_revenue=parsed_vectors["capex_pct_revenue"],
            nwc_pct_revenue=parsed_vectors["nwc_pct_revenue"],
            terminal_growth_rate=terminal_growth_rate,
            discounting_convention=discounting_convention,
            include_leases=include_leases,
            exit_ebitda_multiple=exit_ebitda_multiple,
            initial_nwc_override=initial_nwc_override,
            shares_override=shares_override,
        )
    except Exception as exc:  # noqa: BLE001
        raise DcfValuationError(f"Nieprawidłowe założenia 5Y: {exc}") from None


def build_dcf_projections_dataframe(result: DcfValuationResult) -> pd.DataFrame:
    """Format 5-year DCF `ProjectedYearCashFlow` projections for display."""
    rows = [
        {
            "Rok": f"Y{p.year_index} (FY{p.fiscal_year})",
            "Przychody ($ mld)": _to_billions_float(p.revenue),
            "EBIT ($ mld)": _to_billions_float(p.ebit),
            "NOPAT ($ mld)": _to_billions_float(p.nopat),
            "D&A ($ mld)": _to_billions_float(p.da),
            "CapEx ($ mld)": _to_billions_float(p.capex),
            "ΔNWC ($ mld)": _to_billions_float(p.delta_nwc),
            "FCFF ($ mld)": _to_billions_float(p.fcff),
            "Okres dysk. (t)": float(p.discount_period),
            "PV FCFF ($ mld)": _to_billions_float(p.pv_fcff),
        }
        for p in result.projections
    ]
    return pd.DataFrame(rows)


def build_ev_bridge_rows(result: DcfValuationResult) -> list[dict[str, Any]]:
    """Format the deterministic Enterprise Value -> Equity Value -> Per Share bridge."""
    b = result.bridge
    debt_label = (
        "Dług finansowy + leasingi (ASC 842)"
        if b.include_leases
        else "Dług finansowy (bez leasingów)"
    )
    return [
        {"Pozycja": "PV prognozowanych FCFF (5Y)", "Wartość ($ mld)": _to_billions_float(result.pv_projected_fcff)},
        {"Pozycja": "PV Terminal Value", "Wartość ($ mld)": _to_billions_float(result.terminal_value.pv_terminal_value)},
        {"Pozycja": "Enterprise Value (EV)", "Wartość ($ mld)": _to_billions_float(b.enterprise_value)},
        {"Pozycja": f"(-) {debt_label}", "Wartość ($ mld)": _to_billions_float(-b.debt_deducted)},
        {"Pozycja": "(-) Udziały niekontrolujące (NCI)", "Wartość ($ mld)": _to_billions_float(-b.noncontrolling_interest_deducted)},
        {"Pozycja": "(+) Gotówka i inwestycje krótkoterminowe", "Wartość ($ mld)": _to_billions_float(b.total_cash_added)},
        {"Pozycja": "Implied Equity Value", "Wartość ($ mld)": _to_billions_float(b.implied_equity_value)},
    ]


def build_sensitivity_grid_params(
    base_wacc: Decimal,
    base_g: Decimal,
    *,
    wacc_step: Decimal = Decimal("0.005"),
    g_step: Decimal = Decimal("0.005"),
    steps_each_side: int = 2,
) -> tuple[list[Decimal], list[Decimal]]:
    """Generate sorted WACC and terminal growth axes centered around base assumptions."""
    offsets = range(-steps_each_side, steps_each_side + 1)
    wacc_values = [
        base_wacc + Decimal(offset) * wacc_step
        for offset in offsets
        if (base_wacc + Decimal(offset) * wacc_step) > Decimal("0")
    ]
    if not wacc_values:
        wacc_values = [base_wacc]
    g_values = [base_g + Decimal(offset) * g_step for offset in offsets]
    return wacc_values, g_values


def build_sensitivity_dataframe(matrix: SensitivityMatrix) -> pd.DataFrame:
    """Format a 2D `SensitivityMatrix` into a DataFrame indexed by WACC % vs g %."""
    col_labels = [f"g = {g * PERCENT_SCALE:.2f}%" for g in matrix.terminal_growth_values]
    row_labels = [f"WACC = {w * PERCENT_SCALE:.2f}%" for w in matrix.wacc_values]
    data: list[list[float | None]] = []
    for row in matrix.cells:
        data.append(
            [
                float(cell.implied_value_per_share)
                if cell.is_valid and cell.implied_value_per_share is not None
                else None
                for cell in row
            ]
        )
    return pd.DataFrame(data, index=row_labels, columns=col_labels)
