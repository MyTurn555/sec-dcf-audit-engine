"""Generate auditable multi-sheet Excel (.xlsx) workbooks with live formulas."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from sec_dcf.analysis.dcf import EDUCATIONAL_DISCLAIMER
from sec_dcf.exceptions import DcfValuationError
from sec_dcf.models.analysis import (
    DcfAssumptions,
    DcfValuationResult,
    HistoricalMetricsReport,
    MarketMultiplesComparison,
    SensitivityMatrix,
    WaccInputs,
    WaccResult,
)
from sec_dcf.models.financials import CompanyFinancialsReport

# Wall Street standard color coding
_FONT_INPUT = Font(name="Calibri", size=10, color="0000FF")
_FONT_INPUT_BOLD = Font(name="Calibri", size=10, bold=True, color="0000FF")
_FONT_FORMULA = Font(name="Calibri", size=10, color="000000")
_FONT_FORMULA_BOLD = Font(name="Calibri", size=10, bold=True, color="000000")
_FONT_LINK = Font(name="Calibri", size=10, color="008000")
_FONT_LINK_BOLD = Font(name="Calibri", size=10, bold=True, color="008000")
_FONT_TITLE = Font(name="Calibri", size=14, bold=True, color="1E3A5F")
_FONT_SECTION = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
_FONT_HEADER = Font(name="Calibri", size=10, bold=True, color="1E3A5F")
_FONT_NOTE = Font(name="Calibri", size=9, italic=True, color="475569")

_FILL_SECTION = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
_FILL_SUBHEADER = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
_FILL_ACCENT = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
_FILL_BASE_CELL = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")

_THIN_GRAY = Side(style="thin", color="CBD5E1")
_MEDIUM_NAVY = Side(style="medium", color="1E3A5F")
_DOUBLE_BOTTOM = Side(style="double", color="1E3A5F")

_BORDER_CELL = Border(left=_THIN_GRAY, right=_THIN_GRAY, top=_THIN_GRAY, bottom=_THIN_GRAY)
_BORDER_TOTAL = Border(top=_THIN_GRAY, bottom=_DOUBLE_BOTTOM)
_BORDER_HEADER = Border(bottom=_MEDIUM_NAVY, top=_THIN_GRAY)

_FMT_USD = "$#,##0;($#,##0);\"-\""
_FMT_USD_DEC = "$#,##0.00;($#,##0.00);\"-\""
_FMT_PCT = "0.00%"
_FMT_DEC = "0.00"
_FMT_INT = "#,##0"
_FMT_MULT = "0.00\"x\""


def _num(val: Decimal | None) -> float | None:
    """Convert Decimal to float for Excel cell storage without losing None."""
    return float(val) if val is not None else None


def _style_section_banner(ws: Any, row: int, text: str, max_col: int) -> None:
    """Render a dark-navy section banner across columns A..max_col."""
    ws.cell(row=row, column=1, value=text)
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = _FILL_SECTION
        cell.font = _FONT_SECTION
        cell.alignment = Alignment(vertical="center")


def _auto_fit_columns(ws: Any, min_width: int = 12, max_width: int = 48) -> None:
    """Compute safe column widths so numbers never render as '###'."""
    for col_cells in ws.columns:
        col_idx = col_cells[0].column
        col_letter = get_column_letter(col_idx)
        longest = min_width
        for cell in col_cells:
            val = cell.value
            if val is None:
                continue
            text = str(val)
            if text.startswith("="):
                # Formulas evaluate to formatted numbers (e.g. $1,234,567,890,123)
                longest = max(longest, 18)
            else:
                # Skip long banner/disclaimer strings in column A/B when sizing numeric columns
                if cell.row <= 2 and col_idx <= 2:
                    continue
                longest = max(longest, min(max_width, len(text) + 3))
        ws.column_dimensions[col_letter].width = longest


def _detect_uniform_axis(
    axis: list[Decimal],
    base_val: Decimal,
) -> list[Decimal] | None:
    """Return offsets from `base_val` if `base_val` is close to a grid point."""
    offsets = [val - base_val for val in axis]
    if any(abs(off) < Decimal("1e-9") for off in offsets):
        return offsets
    return None


def export_dcf_workbook_bytes(
    *,
    report: CompanyFinancialsReport,
    metrics_report: HistoricalMetricsReport,
    wacc_inputs: WaccInputs,
    wacc_result: WaccResult,
    assumptions: DcfAssumptions,
    dcf_result: DcfValuationResult,
    sensitivity: SensitivityMatrix,
    multiples: MarketMultiplesComparison | None = None,
) -> bytes:
    """
    Build an auditable 3-sheet Excel (.xlsx) workbook with live formulas.

    Sheets:
      1. `Assumptions & WACC` — Blue inputs, black live CAPM/WACC formulas, 5Y drivers.
      2. `Historical 5Y & Audit` — 5Y statements, SBC/True FCF, multiples, and XBRL `audit_trail`.
      3. `DCF & Sensitivity` — Live 5Y FCFF formulas, TV, EV bridge, and 2D sensitivity grid.
    """
    if not report.periods:
        raise DcfValuationError(f"{report.ticker}: cannot export workbook without periods.")
    if (
        metrics_report.include_leases != assumptions.include_leases
        or wacc_result.include_leases != assumptions.include_leases
        or dcf_result.include_leases != assumptions.include_leases
    ):
        raise DcfValuationError(
            "include_leases mismatch across report/WACC/DCF inputs during Excel export."
        )

    wb = Workbook()
    ws_assump = wb.active
    ws_assump.title = "Assumptions & WACC"
    ws_hist = wb.create_sheet("Historical 5Y & Audit")
    ws_dcf = wb.create_sheet("DCF & Sensitivity")

    sorted_periods = sorted(report.periods, key=lambda p: p.period_end)
    sorted_metrics = sorted(metrics_report.periods, key=lambda p: p.period_end)
    n_hist = len(sorted_periods)
    latest = sorted_periods[-1]
    latest_col_idx = 1 + n_hist
    latest_col = get_column_letter(latest_col_idx)

    # =========================================================================
    # SHEET 2 FIRST (so exact row coordinates are known for cross-sheet links)
    # =========================================================================
    ws_hist.views.sheetView[0].showGridLines = True
    ws_hist.freeze_panes = "B6"

    ws_hist["A1"] = f"SEC DCF Audit Engine — {report.company_name} ({report.ticker}) | Historical 5Y & XBRL Audit"
    ws_hist["A1"].font = _FONT_TITLE
    ws_hist["A2"] = EDUCATIONAL_DISCLAIMER
    ws_hist["A2"].font = _FONT_NOTE

    _style_section_banner(ws_hist, 4, "1. Historical 5-Year Financial Statements & Ratios (USD / Shares)", max(6, latest_col_idx + 1))

    ws_hist.cell(row=5, column=1, value="Line Item / Metric").font = _FONT_HEADER
    ws_hist.cell(row=5, column=1).fill = _FILL_SUBHEADER
    ws_hist.cell(row=5, column=1).border = _BORDER_HEADER
    for i, period in enumerate(sorted_periods):
        col = 2 + i
        c = ws_hist.cell(row=5, column=col, value=f"FY{period.fiscal_year}")
        c.font = _FONT_HEADER
        c.fill = _FILL_SUBHEADER
        c.border = _BORDER_HEADER
        c.alignment = Alignment(horizontal="right")

    hist_rows: dict[str, int] = {}

    def _add_hist_input_row(key: str, label: str, values: list[float | None], num_fmt: str) -> int:
        r = ws_hist.max_row + 1
        hist_rows[key] = r
        lbl = ws_hist.cell(row=r, column=1, value=label)
        lbl.font = _FONT_FORMULA
        lbl.border = _BORDER_CELL
        for i, val in enumerate(values):
            c = ws_hist.cell(row=r, column=2 + i, value=val)
            c.font = _FONT_INPUT
            c.number_format = num_fmt
            c.border = _BORDER_CELL
        return r

    def _add_hist_formula_row(
        key: str,
        label: str,
        formula_fn: Any,
        num_fmt: str,
        *,
        bold: bool = False,
    ) -> int:
        r = ws_hist.max_row + 1
        hist_rows[key] = r
        lbl = ws_hist.cell(row=r, column=1, value=label)
        lbl.font = _FONT_FORMULA_BOLD if bold else _FONT_FORMULA
        lbl.border = _BORDER_TOTAL if bold else _BORDER_CELL
        for i in range(n_hist):
            col_ltr = get_column_letter(2 + i)
            c = ws_hist.cell(row=r, column=2 + i, value=formula_fn(col_ltr))
            c.font = _FONT_FORMULA_BOLD if bold else _FONT_FORMULA
            c.number_format = num_fmt
            c.border = _BORDER_TOTAL if bold else _BORDER_CELL
        return r

    _add_hist_input_row("revenue", "Revenue", [_num(p.revenue) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("ebit", "EBIT (Operating Income)", [_num(p.ebit) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("da", "Depreciation & Amortization (D&A)", [_num(p.da) for p in sorted_periods], _FMT_USD)
    _add_hist_formula_row(
        "ebitda",
        "EBITDA (= EBIT + D&A)",
        lambda c: f"={c}{hist_rows['ebit']}+{c}{hist_rows['da']}",
        _FMT_USD,
        bold=True,
    )
    _add_hist_input_row("net_income", "Net Income", [_num(p.net_income) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("cfo", "Operating Cash Flow (CFO)", [_num(p.cfo) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("capex", "Capital Expenditures (CapEx)", [_num(p.capex) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("sbc", "Stock-Based Compensation (SBC)", [_num(p.sbc) for p in sorted_periods], _FMT_USD)
    _add_hist_formula_row(
        "fcf_simple",
        "Simple FCF (= CFO - CapEx)",
        lambda c: f"={c}{hist_rows['cfo']}-{c}{hist_rows['capex']}",
        _FMT_USD,
        bold=True,
    )
    _add_hist_formula_row(
        "fcf_ex_sbc",
        "True FCF ex-SBC (= CFO - CapEx - SBC)",
        lambda c: f"={c}{hist_rows['cfo']}-{c}{hist_rows['capex']}-{c}{hist_rows['sbc']}",
        _FMT_USD,
        bold=True,
    )
    _add_hist_input_row("cash", "Cash & Cash Equivalents", [_num(p.cash_and_equivalents) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("st_inv", "Short-Term Investments", [_num(p.short_term_investments) for p in sorted_periods], _FMT_USD)
    _add_hist_formula_row(
        "total_cash",
        "Total Cash & Short-Term Investments",
        lambda c: f"={c}{hist_rows['cash']}+{c}{hist_rows['st_inv']}",
        _FMT_USD,
    )
    _add_hist_input_row("st_debt", "Short-Term Debt", [_num(p.short_term_debt) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("lt_debt", "Long-Term Debt", [_num(p.long_term_debt) for p in sorted_periods], _FMT_USD)
    _add_hist_formula_row(
        "financial_debt",
        "Financial Debt (excl. Leases)",
        lambda c: f"={c}{hist_rows['st_debt']}+{c}{hist_rows['lt_debt']}",
        _FMT_USD,
    )
    _add_hist_input_row("curr_lease", "Current Lease Liabilities (ASC 842)", [_num(p.current_lease_liabilities) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("noncurr_lease", "Noncurrent Lease Liabilities (ASC 842)", [_num(p.noncurrent_lease_liabilities) for p in sorted_periods], _FMT_USD)
    _add_hist_formula_row(
        "lease_liabilities",
        "Total Lease Liabilities (ASC 842)",
        lambda c: f"={c}{hist_rows['curr_lease']}+{c}{hist_rows['noncurr_lease']}",
        _FMT_USD,
    )
    _add_hist_formula_row(
        "total_debt_incl_leases",
        "Total Debt (incl. Leases)",
        lambda c: f"={c}{hist_rows['financial_debt']}+{c}{hist_rows['lease_liabilities']}",
        _FMT_USD,
        bold=True,
    )
    _add_hist_formula_row(
        "net_debt_excl",
        "Net Debt (excl. Leases)",
        lambda c: f"={c}{hist_rows['financial_debt']}-{c}{hist_rows['total_cash']}",
        _FMT_USD,
    )
    _add_hist_formula_row(
        "net_debt_incl",
        "Net Debt (incl. Leases)",
        lambda c: f"={c}{hist_rows['total_debt_incl_leases']}-{c}{hist_rows['total_cash']}",
        _FMT_USD,
    )
    _add_hist_input_row("nci", "Noncontrolling Interest (NCI)", [_num(p.noncontrolling_interest) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("equity", "Stockholders Equity", [_num(p.stockholders_equity) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row("non_cash_nwc", "Non-Cash Net Working Capital (NWC)", [_num(p.non_cash_nwc) for p in sorted_periods], _FMT_USD)
    _add_hist_input_row(
        "shares",
        "Diluted Shares (or Ending Fallback)",
        [
            _num(
                p.diluted_shares
                if (p.diluted_shares is not None and p.diluted_shares > Decimal("0"))
                else p.ending_shares
            )
            for p in sorted_periods
        ],
        _FMT_INT,
    )
    _add_hist_formula_row(
        "ebit_margin",
        "EBIT Margin (%)",
        lambda c: f'=IF({c}{hist_rows["revenue"]}>0,{c}{hist_rows["ebit"]}/{c}{hist_rows["revenue"]},"N/A")',
        _FMT_PCT,
    )
    _add_hist_formula_row(
        "ebitda_margin",
        "EBITDA Margin (%)",
        lambda c: f'=IF({c}{hist_rows["revenue"]}>0,{c}{hist_rows["ebitda"]}/{c}{hist_rows["revenue"]},"N/A")',
        _FMT_PCT,
    )
    _add_hist_formula_row(
        "sbc_to_rev",
        "SBC / Revenue (%)",
        lambda c: f'=IF({c}{hist_rows["revenue"]}>0,{c}{hist_rows["sbc"]}/{c}{hist_rows["revenue"]},"N/A")',
        _FMT_PCT,
    )
    _add_hist_formula_row(
        "sbc_to_cfo",
        "SBC / CFO (%)",
        lambda c: f'=IF({c}{hist_rows["cfo"]}>0,{c}{hist_rows["sbc"]}/{c}{hist_rows["cfo"]},"N/A")',
        _FMT_PCT,
    )
    _add_hist_input_row("roic", "ROIC (Engine, Average IC)", [_num(m.roic) for m in sorted_metrics], _FMT_PCT)

    # Market Multiples summary on Sheet 2 (if supplied)
    if multiples is not None:
        m_start = ws_hist.max_row + 2
        _style_section_banner(ws_hist, m_start, f"2. Market Multiples Confrontation (FY{multiples.fiscal_year})", 4)
        m_hdr = m_start + 1
        for idx, title in enumerate(("Multiple / Yield", "Current Market", "Implied DCF", "Basis"), start=1):
            c = ws_hist.cell(row=m_hdr, column=idx, value=title)
            c.font = _FONT_HEADER
            c.fill = _FILL_SUBHEADER
            c.border = _BORDER_HEADER
        mult_specs = (
            ("EV / Revenue", multiples.current.ev_to_revenue, multiples.implied_dcf.ev_to_revenue, _FMT_MULT, "Latest FY Revenue"),
            ("EV / EBITDA", multiples.current.ev_to_ebitda, multiples.implied_dcf.ev_to_ebitda, _FMT_MULT, "Latest FY EBITDA"),
            ("EV / EBIT", multiples.current.ev_to_ebit, multiples.implied_dcf.ev_to_ebit, _FMT_MULT, "Latest FY EBIT"),
            ("P / E", multiples.current.pe_ratio, multiples.implied_dcf.pe_ratio, _FMT_MULT, "Market Cap / Net Income"),
            ("Reported FCF Yield", multiples.current.reported_fcf_yield, multiples.implied_dcf.reported_fcf_yield, _FMT_PCT, "Simple FCF / Equity"),
            ("True FCF Yield (ex-SBC)", multiples.current.true_fcf_yield_ex_sbc, multiples.implied_dcf.true_fcf_yield_ex_sbc, _FMT_PCT, "True FCF ex-SBC / Equity"),
        )
        for idx, (m_name, cur_v, dcf_v, fmt, basis) in enumerate(mult_specs, start=m_hdr + 1):
            ws_hist.cell(row=idx, column=1, value=m_name).font = _FONT_FORMULA
            c_cur = ws_hist.cell(row=idx, column=2, value=_num(cur_v) if cur_v is not None else "N/A")
            c_cur.font = _FONT_INPUT
            c_cur.number_format = fmt
            c_dcf = ws_hist.cell(row=idx, column=3, value=_num(dcf_v) if dcf_v is not None else "N/A")
            c_dcf.font = _FONT_INPUT
            c_dcf.number_format = fmt
            ws_hist.cell(row=idx, column=4, value=basis).font = _FONT_NOTE

    # XBRL Audit Trail table on Sheet 2
    audit_start = ws_hist.max_row + 2
    _style_section_banner(ws_hist, audit_start, "3. XBRL Lineage & Audit Trail (All Periods)", 9)
    audit_hdr = audit_start + 1
    audit_headers = (
        "Fiscal Year",
        "Canonical Field",
        "Source Kind",
        "XBRL Concept",
        "Raw Value",
        "Unit",
        "Period End",
        "Filing Date",
        "Accession / Note",
    )
    for col, title in enumerate(audit_headers, start=1):
        c = ws_hist.cell(row=audit_hdr, column=col, value=title)
        c.font = _FONT_HEADER
        c.fill = _FILL_SUBHEADER
        c.border = _BORDER_HEADER

    r_aud = audit_hdr + 1
    for period in sorted(sorted_periods, key=lambda p: p.fiscal_year, reverse=True):
        for audit in period.audit_trail.values():
            ws_hist.cell(row=r_aud, column=1, value=period.fiscal_year).font = _FONT_INPUT
            ws_hist.cell(row=r_aud, column=2, value=audit.field_name).font = _FONT_FORMULA
            ws_hist.cell(row=r_aud, column=3, value=audit.source_kind).font = _FONT_FORMULA
            ws_hist.cell(row=r_aud, column=4, value=audit.concept or "-").font = _FONT_FORMULA
            raw_cell = ws_hist.cell(
                row=r_aud,
                column=5,
                value=_num(audit.raw_value) if audit.raw_value is not None else "-",
            )
            raw_cell.font = _FONT_INPUT
            if audit.raw_value is not None:
                raw_cell.number_format = _FMT_INT
            ws_hist.cell(row=r_aud, column=6, value=audit.raw_unit or "-").font = _FONT_FORMULA
            ws_hist.cell(
                row=r_aud,
                column=7,
                value=audit.period_end.isoformat() if audit.period_end else "-",
            ).font = _FONT_FORMULA
            ws_hist.cell(
                row=r_aud,
                column=8,
                value=audit.filing_date.isoformat() if audit.filing_date else "-",
            ).font = _FONT_FORMULA
            acc_note = f"{audit.accession or '-'} | {audit.note or ''}".strip(" |")
            ws_hist.cell(row=r_aud, column=9, value=acc_note).font = _FONT_NOTE
            r_aud += 1

    # =========================================================================
    # SHEET 1: ASSUMPTIONS & WACC
    # =========================================================================
    ws_assump.views.sheetView[0].showGridLines = True
    ws_assump.freeze_panes = "B5"

    ws_assump["A1"] = f"SEC DCF Audit Engine — {report.company_name} ({report.ticker}) | Assumptions & WACC"
    ws_assump["A1"].font = _FONT_TITLE
    ws_assump["A2"] = EDUCATIONAL_DISCLAIMER
    ws_assump["A2"].font = _FONT_NOTE
    ws_assump["A3"] = "Color Legend: Blue = User / Historical Input | Black = Same-Sheet Formula | Green = Cross-Sheet Link"
    ws_assump["A3"].font = _FONT_NOTE

    _style_section_banner(ws_assump, 4, "1. Model Controls & Base Period", 7)

    # Exact cell references in Sheet 1
    ROW_TICKER = 5
    ROW_BASE_FY = 6
    ROW_BASE_REV = 7
    ROW_INIT_NWC = 8
    ROW_INCL_LEASES = 9
    ROW_CONVENTION = 10
    ROW_ADD_BACK_SBC = 11
    ROW_TERM_G = 12
    ROW_EXIT_MULT = 13
    ROW_SHARES = 14

    ROW_RF = 17
    ROW_BETA = 18
    ROW_ERP = 19
    ROW_KE = 20
    ROW_KD_PRE = 21
    ROW_WACC_TAX = 22
    ROW_KD_AFTER = 23
    ROW_SHARE_PRICE = 24
    ROW_EQ_VAL = 25
    ROW_DEBT_VAL = 26
    ROW_TOT_CAP = 27
    ROW_EQ_WT = 28
    ROW_DEBT_WT = 29
    ROW_WACC = 30

    ROW_DRV_HDR = 33
    ROW_DRV_REV_G = 34
    ROW_DRV_EBIT_M = 35
    ROW_DRV_TAX = 36
    ROW_DRV_DA = 37
    ROW_DRV_CAPEX = 38
    ROW_DRV_NWC = 39
    ROW_DRV_SBC = 40

    def _put_ctrl_row(
        row: int,
        label: str,
        value: Any,
        *,
        font: Font,
        num_fmt: str | None = None,
        note: str = "",
    ) -> None:
        lbl = ws_assump.cell(row=row, column=1, value=label)
        lbl.font = _FONT_FORMULA_BOLD if font in (_FONT_FORMULA_BOLD, _FONT_INPUT_BOLD) else _FONT_FORMULA
        lbl.border = _BORDER_CELL
        c = ws_assump.cell(row=row, column=2, value=value)
        c.font = font
        if num_fmt:
            c.number_format = num_fmt
        c.border = _BORDER_CELL
        if note:
            ws_assump.cell(row=row, column=7, value=note).font = _FONT_NOTE

    _put_ctrl_row(ROW_TICKER, "Ticker", report.ticker, font=_FONT_INPUT_BOLD, note=f"CIK: {report.cik}")
    _put_ctrl_row(ROW_BASE_FY, "Base Fiscal Year", latest.fiscal_year, font=_FONT_INPUT, num_fmt=_FMT_INT)
    _put_ctrl_row(
        ROW_BASE_REV,
        "Base Period Revenue (USD)",
        f"='Historical 5Y & Audit'!{latest_col}{hist_rows['revenue']}",
        font=_FONT_LINK,
        num_fmt=_FMT_USD,
        note="Linked to latest historical 10-K period",
    )

    if dcf_result.initial_nwc_source == "historical_balance_sheet":
        init_nwc_val: Any = f"='Historical 5Y & Audit'!{latest_col}{hist_rows['non_cash_nwc']}"
        init_nwc_font = _FONT_LINK
    elif dcf_result.initial_nwc_source == "model_pct_fallback":
        init_nwc_val = f"=B{ROW_BASE_REV}*B{ROW_DRV_NWC}"
        init_nwc_font = _FONT_FORMULA
    else:
        init_nwc_val = _num(dcf_result.initial_nwc)
        init_nwc_font = _FONT_INPUT
    _put_ctrl_row(
        ROW_INIT_NWC,
        "Initial Non-Cash NWC (t=0, USD)",
        init_nwc_val,
        font=init_nwc_font,
        num_fmt=_FMT_USD,
        note=f"Source: {dcf_result.initial_nwc_source}",
    )

    # Native Python bools (True / False) so Excel IF() evaluates natively without #VALUE!
    _put_ctrl_row(
        ROW_INCL_LEASES,
        "Include Leases (ASC 842)",
        bool(assumptions.include_leases),
        font=_FONT_INPUT_BOLD,
        note="Native boolean TRUE/FALSE: dynamically toggles leases in WACC Debt & EV Bridge",
    )
    _put_ctrl_row(
        ROW_CONVENTION,
        "Discounting Convention",
        assumptions.discounting_convention,
        font=_FONT_INPUT,
        note='"mid_year" or "end_of_year"',
    )
    _put_ctrl_row(
        ROW_ADD_BACK_SBC,
        "Add-back SBC to FCFF",
        bool(assumptions.add_back_sbc),
        font=_FONT_INPUT_BOLD,
        note="FALSE = economic cost (GAAP EBIT already deducts SBC); TRUE = CFO-style add-back",
    )
    _put_ctrl_row(
        ROW_TERM_G,
        "Terminal Growth Rate (g)",
        _num(assumptions.terminal_growth_rate),
        font=_FONT_INPUT,
        num_fmt=_FMT_PCT,
    )
    _put_ctrl_row(
        ROW_EXIT_MULT,
        "Exit EV / EBITDA Multiple (Optional)",
        _num(assumptions.exit_ebitda_multiple) if assumptions.exit_ebitda_multiple is not None else None,
        font=_FONT_INPUT,
        num_fmt=_FMT_MULT,
        note="Leave blank if unused",
    )

    if dcf_result.bridge.shares_source == "override":
        shares_val: Any = _num(dcf_result.bridge.diluted_shares_used)
        shares_font = _FONT_INPUT
    else:
        shares_val = f"='Historical 5Y & Audit'!{latest_col}{hist_rows['shares']}"
        shares_font = _FONT_LINK
    _put_ctrl_row(
        ROW_SHARES,
        "Diluted Shares Used",
        shares_val,
        font=shares_font,
        num_fmt=_FMT_INT,
        note=f"Source: {dcf_result.bridge.shares_source}",
    )

    _style_section_banner(ws_assump, 16, "2. WACC Calculation (CAPM + Capital Structure)", 7)
    _put_ctrl_row(
        ROW_RF,
        "Risk-Free Rate (Rf)",
        _num(wacc_result.risk_free_rate.rate),
        font=_FONT_INPUT,
        num_fmt=_FMT_PCT,
        note=f"Source: {wacc_result.risk_free_rate.series_id} ({wacc_result.risk_free_rate.source})",
    )
    _put_ctrl_row(ROW_BETA, "Equity Beta", _num(wacc_inputs.beta), font=_FONT_INPUT, num_fmt=_FMT_DEC)
    _put_ctrl_row(ROW_ERP, "Equity Risk Premium (ERP)", _num(wacc_inputs.equity_risk_premium), font=_FONT_INPUT, num_fmt=_FMT_PCT)
    _put_ctrl_row(
        ROW_KE,
        "Cost of Equity (Ke = Rf + Beta * ERP)",
        f"=B{ROW_RF}+B{ROW_BETA}*B{ROW_ERP}",
        font=_FONT_FORMULA_BOLD,
        num_fmt=_FMT_PCT,
    )
    _put_ctrl_row(
        ROW_KD_PRE,
        "Pre-tax Cost of Debt (Kd)",
        _num(wacc_result.pretax_cost_of_debt),
        font=_FONT_INPUT,
        num_fmt=_FMT_PCT,
        note=f"Source: {wacc_result.cost_of_debt_source}",
    )
    _put_ctrl_row(
        ROW_WACC_TAX,
        "WACC Effective Tax Rate",
        _num(wacc_result.tax_rate),
        font=_FONT_INPUT,
        num_fmt=_FMT_PCT,
        note=f"Source: {wacc_result.tax_rate_source}",
    )
    _put_ctrl_row(
        ROW_KD_AFTER,
        "After-tax Cost of Debt (= Kd * (1 - t))",
        f"=B{ROW_KD_PRE}*(1-B{ROW_WACC_TAX})",
        font=_FONT_FORMULA_BOLD,
        num_fmt=_FMT_PCT,
    )

    _put_ctrl_row(
        ROW_SHARE_PRICE,
        "Reference Share Price (USD, if used)",
        _num(wacc_inputs.share_price) if wacc_inputs.share_price is not None else None,
        font=_FONT_INPUT,
        num_fmt=_FMT_USD_DEC,
    )
    if wacc_result.equity_weight_source == "share_price_x_shares":
        eq_val: Any = f"=B{ROW_SHARE_PRICE}*B{ROW_SHARES}"
        eq_font = _FONT_FORMULA
    else:
        eq_val = _num(wacc_result.equity_value_for_weights)
        eq_font = _FONT_INPUT
    _put_ctrl_row(
        ROW_EQ_VAL,
        "Equity Value for Weights (E, USD)",
        eq_val,
        font=eq_font,
        num_fmt=_FMT_USD,
        note=f"Source: {wacc_result.equity_weight_source}",
    )

    debt_link_formula = (
        f"=IF(B{ROW_INCL_LEASES},"
        f"'Historical 5Y & Audit'!{latest_col}{hist_rows['total_debt_incl_leases']},"
        f"'Historical 5Y & Audit'!{latest_col}{hist_rows['financial_debt']})"
    )
    _put_ctrl_row(
        ROW_DEBT_VAL,
        "Debt Value for Weights (D, USD — Dynamic ASC 842)",
        debt_link_formula,
        font=_FONT_LINK,
        num_fmt=_FMT_USD,
        note="Automatically switches between financial debt and debt incl. leases",
    )
    _put_ctrl_row(
        ROW_TOT_CAP,
        "Total Capital (E + D, USD)",
        f"=B{ROW_EQ_VAL}+B{ROW_DEBT_VAL}",
        font=_FONT_FORMULA,
        num_fmt=_FMT_USD,
    )
    _put_ctrl_row(
        ROW_EQ_WT,
        "Equity Weight (E / (E + D))",
        f"=IF(B{ROW_TOT_CAP}>0,B{ROW_EQ_VAL}/B{ROW_TOT_CAP},0)",
        font=_FONT_FORMULA,
        num_fmt=_FMT_PCT,
    )
    _put_ctrl_row(
        ROW_DEBT_WT,
        "Debt Weight (D / (E + D))",
        f"=IF(B{ROW_TOT_CAP}>0,B{ROW_DEBT_VAL}/B{ROW_TOT_CAP},0)",
        font=_FONT_FORMULA,
        num_fmt=_FMT_PCT,
    )
    _put_ctrl_row(
        ROW_WACC,
        "Weighted Average Cost of Capital (WACC)",
        f"=B{ROW_EQ_WT}*B{ROW_KE}+B{ROW_DEBT_WT}*B{ROW_KD_AFTER}",
        font=_FONT_FORMULA_BOLD,
        num_fmt=_FMT_PCT,
    )
    ws_assump.cell(row=ROW_WACC, column=2).fill = _FILL_ACCENT
    ws_assump.cell(row=ROW_WACC, column=2).border = _BORDER_TOTAL

    _style_section_banner(ws_assump, 32, "3. 5-Year Projection Drivers (Unified Scalar / Year-by-Year Path)", 7)
    ws_assump.cell(row=ROW_DRV_HDR, column=1, value="Projection Driver").font = _FONT_HEADER
    ws_assump.cell(row=ROW_DRV_HDR, column=1).fill = _FILL_SUBHEADER
    ws_assump.cell(row=ROW_DRV_HDR, column=1).border = _BORDER_HEADER
    for idx in range(5):
        c = ws_assump.cell(
            row=ROW_DRV_HDR,
            column=2 + idx,
            value=f"Y{idx + 1} (FY{latest.fiscal_year + idx + 1})",
        )
        c.font = _FONT_HEADER
        c.fill = _FILL_SUBHEADER
        c.border = _BORDER_HEADER
        c.alignment = Alignment(horizontal="right")
    c_note_hdr = ws_assump.cell(row=ROW_DRV_HDR, column=7, value="Notes")
    c_note_hdr.font = _FONT_HEADER
    c_note_hdr.fill = _FILL_SUBHEADER
    c_note_hdr.border = _BORDER_HEADER

    driver_specs = (
        (ROW_DRV_REV_G, "Revenue Growth (%)", assumptions.revenue_growth, "YoY top-line growth"),
        (ROW_DRV_EBIT_M, "EBIT Margin (%)", assumptions.ebit_margin, "GAAP EBIT / Revenue (includes SBC expense)"),
        (ROW_DRV_TAX, "Effective Tax Rate (%)", assumptions.tax_rate, "NOPAT tax rate"),
        (ROW_DRV_DA, "D&A % of Revenue", assumptions.da_pct_revenue, "Non-cash depreciation & amortization"),
        (ROW_DRV_CAPEX, "CapEx % of Revenue", assumptions.capex_pct_revenue, "Capital expenditures outflow"),
        (ROW_DRV_NWC, "Non-cash NWC % of Revenue", assumptions.nwc_pct_revenue, "Ending NWC target % of revenue"),
        (ROW_DRV_SBC, "SBC % of Revenue", assumptions.sbc_pct_revenue, "Used only when Add-back SBC = TRUE"),
    )
    for row_idx, label, series, note_txt in driver_specs:
        lbl = ws_assump.cell(row=row_idx, column=1, value=label)
        lbl.font = _FONT_FORMULA
        lbl.border = _BORDER_CELL
        for i in range(5):
            c = ws_assump.cell(row=row_idx, column=2 + i, value=_num(series[i]))
            c.font = _FONT_INPUT
            c.number_format = _FMT_PCT
            c.border = _BORDER_CELL
        ws_assump.cell(row=row_idx, column=7, value=note_txt).font = _FONT_NOTE

    # =========================================================================
    # SHEET 3: DCF & SENSITIVITY (100% LIVE FORMULAS)
    # =========================================================================
    ws_dcf.views.sheetView[0].showGridLines = True
    ws_dcf.freeze_panes = "C6"

    ws_dcf["A1"] = f"SEC DCF Audit Engine — {report.company_name} ({report.ticker}) | 5Y DCF & Sensitivity"
    ws_dcf["A1"].font = _FONT_TITLE
    ws_dcf["A2"] = EDUCATIONAL_DISCLAIMER
    ws_dcf["A2"].font = _FONT_NOTE

    _style_section_banner(ws_dcf, 4, "1. 5-Year FCFF Projection (Live Formulas)", 7)

    ws_dcf.cell(row=5, column=1, value="Line Item (USD)").font = _FONT_HEADER
    ws_dcf.cell(row=5, column=1).fill = _FILL_SUBHEADER
    ws_dcf.cell(row=5, column=1).border = _BORDER_HEADER

    c_base_hdr = ws_dcf.cell(row=5, column=2, value=f"Base (FY{latest.fiscal_year})")
    c_base_hdr.font = _FONT_HEADER
    c_base_hdr.fill = _FILL_SUBHEADER
    c_base_hdr.border = _BORDER_HEADER
    c_base_hdr.alignment = Alignment(horizontal="right")

    for idx in range(5):
        c = ws_dcf.cell(
            row=5,
            column=3 + idx,
            value=f"Y{idx + 1} (FY{latest.fiscal_year + idx + 1})",
        )
        c.font = _FONT_HEADER
        c.fill = _FILL_SUBHEADER
        c.border = _BORDER_HEADER
        c.alignment = Alignment(horizontal="right")

    # Projection rows
    P_REV = 6
    P_EBIT = 7
    P_TAX = 8
    P_NOPAT = 9
    P_DA = 10
    P_SBC = 11
    P_CAPEX = 12
    P_NWC = 13
    P_DNWC = 14
    P_FCFF = 15
    P_EBITDA = 16
    P_YEAR_IDX = 17
    P_DISC_PER = 18
    P_DISC_FAC = 19
    P_PV_FCFF = 20

    proj_labels = (
        (P_REV, "Revenue", _FMT_USD, True),
        (P_EBIT, "EBIT", _FMT_USD, False),
        (P_TAX, "Tax Rate", _FMT_PCT, False),
        (P_NOPAT, "NOPAT (= EBIT * (1 - t))", _FMT_USD, False),
        (P_DA, "(+) D&A", _FMT_USD, False),
        (P_SBC, "(+) SBC (added back iff Add-back SBC = TRUE)", _FMT_USD, False),
        (P_CAPEX, "(-) CapEx", _FMT_USD, False),
        (P_NWC, "Ending Non-Cash NWC", _FMT_USD, False),
        (P_DNWC, "(-) Change in NWC (ΔNWC)", _FMT_USD, False),
        (P_FCFF, "Free Cash Flow to Firm (FCFF)", _FMT_USD, True),
        (P_EBITDA, "EBITDA (= EBIT + D&A)", _FMT_USD, False),
        (P_YEAR_IDX, "Forecast Year Index (t)", _FMT_INT, False),
        (P_DISC_PER, "Discount Period (mid_year vs end_of_year)", _FMT_DEC, False),
        (P_DISC_FAC, "Discount Factor (1 / (1 + WACC)^t)", "0.0000", False),
        (P_PV_FCFF, "Present Value of FCFF", _FMT_USD, True),
    )
    for row_idx, label, num_fmt, bold in proj_labels:
        lbl = ws_dcf.cell(row=row_idx, column=1, value=label)
        lbl.font = _FONT_FORMULA_BOLD if bold else _FONT_FORMULA
        lbl.border = _BORDER_TOTAL if bold else _BORDER_CELL
        for col_i in range(2, 8):
            c = ws_dcf.cell(row=row_idx, column=col_i)
            c.border = _BORDER_TOTAL if bold else _BORDER_CELL
            c.number_format = num_fmt
            c.font = _FONT_FORMULA_BOLD if bold else _FONT_FORMULA

    # Base column (B)
    ws_dcf.cell(row=P_REV, column=2, value=f"='Assumptions & WACC'!$B${ROW_BASE_REV}").font = _FONT_LINK_BOLD
    ws_dcf.cell(row=P_NWC, column=2, value=f"='Assumptions & WACC'!$B${ROW_INIT_NWC}").font = _FONT_LINK
    ws_dcf.cell(row=P_YEAR_IDX, column=2, value=0).font = _FONT_INPUT

    # Forecast columns C..G (Y1..Y5), referencing Sheet 1 B..F
    for idx in range(5):
        proj_col = get_column_letter(3 + idx)  # C..G
        prev_col = get_column_letter(2 + idx)  # B..F
        assump_col = get_column_letter(2 + idx)  # B..F on Assumptions & WACC

        ws_dcf[f"{proj_col}{P_REV}"] = f"={prev_col}{P_REV}*(1+'Assumptions & WACC'!{assump_col}${ROW_DRV_REV_G})"
        ws_dcf[f"{proj_col}{P_EBIT}"] = f"={proj_col}{P_REV}*'Assumptions & WACC'!{assump_col}${ROW_DRV_EBIT_M}"
        ws_dcf[f"{proj_col}{P_TAX}"] = f"='Assumptions & WACC'!{assump_col}${ROW_DRV_TAX}"
        ws_dcf[f"{proj_col}{P_TAX}"].font = _FONT_LINK
        ws_dcf[f"{proj_col}{P_NOPAT}"] = f"={proj_col}{P_EBIT}*(1-{proj_col}{P_TAX})"
        ws_dcf[f"{proj_col}{P_DA}"] = f"={proj_col}{P_REV}*'Assumptions & WACC'!{assump_col}${ROW_DRV_DA}"
        ws_dcf[f"{proj_col}{P_SBC}"] = f"={proj_col}{P_REV}*'Assumptions & WACC'!{assump_col}${ROW_DRV_SBC}"
        ws_dcf[f"{proj_col}{P_CAPEX}"] = f"={proj_col}{P_REV}*'Assumptions & WACC'!{assump_col}${ROW_DRV_CAPEX}"
        ws_dcf[f"{proj_col}{P_NWC}"] = f"={proj_col}{P_REV}*'Assumptions & WACC'!{assump_col}${ROW_DRV_NWC}"
        ws_dcf[f"{proj_col}{P_DNWC}"] = f"={proj_col}{P_NWC}-{prev_col}{P_NWC}"
        ws_dcf[f"{proj_col}{P_FCFF}"] = (
            f"={proj_col}{P_NOPAT}+{proj_col}{P_DA}"
            f"+IF('Assumptions & WACC'!$B${ROW_ADD_BACK_SBC},{proj_col}{P_SBC},0)"
            f"-{proj_col}{P_CAPEX}-{proj_col}{P_DNWC}"
        )
        ws_dcf[f"{proj_col}{P_EBITDA}"] = f"={proj_col}{P_EBIT}+{proj_col}{P_DA}"
        ws_dcf[f"{proj_col}{P_YEAR_IDX}"] = idx + 1
        ws_dcf[f"{proj_col}{P_YEAR_IDX}"].font = _FONT_INPUT
        ws_dcf[f"{proj_col}{P_DISC_PER}"] = (
            f'=IF(\'Assumptions & WACC\'!$B${ROW_CONVENTION}="mid_year",{proj_col}{P_YEAR_IDX}-0.5,{proj_col}{P_YEAR_IDX})'
        )
        ws_dcf[f"{proj_col}{P_DISC_FAC}"] = (
            f"=1/((1+'Assumptions & WACC'!$B${ROW_WACC})^{proj_col}{P_DISC_PER})"
        )
        ws_dcf[f"{proj_col}{P_PV_FCFF}"] = f"={proj_col}{P_FCFF}*{proj_col}{P_DISC_FAC}"

    # Section 2: Terminal Value & EV Bridge
    _style_section_banner(ws_dcf, 22, "2. Terminal Value & Enterprise-to-Equity Value Bridge (Live Formulas)", 7)

    B_PV_5Y = 23
    B_TERM_FCFF = 24
    B_UNDISC_TV = 25
    B_TV_DF = 26
    B_PV_TV = 27
    B_EV = 28
    B_TV_SHARE = 29
    B_IMP_MULT = 30
    B_DEBT = 31
    B_NCI = 32
    B_CASH = 33
    B_RAW_EQ = 34
    B_IMP_EQ = 35
    B_SHARES = 36
    B_PER_SHARE = 37

    B_EXIT_TV = 39
    B_EXIT_PV_TV = 40
    B_EXIT_EV = 41
    B_EXIT_PS = 42

    bridge_debt_formula = (
        f"=IF('Assumptions & WACC'!$B${ROW_INCL_LEASES},"
        f"'Historical 5Y & Audit'!${latest_col}${hist_rows['total_debt_incl_leases']},"
        f"'Historical 5Y & Audit'!${latest_col}${hist_rows['financial_debt']})"
    )

    bridge_specs = (
        (B_PV_5Y, "PV of 5Y Projected FCFF", f"=SUM(C{P_PV_FCFF}:G{P_PV_FCFF})", _FMT_USD, _FONT_FORMULA_BOLD, False),
        (B_TERM_FCFF, "Terminal Year FCFF (FCFF_5 * (1 + g))", f"=G{P_FCFF}*(1+'Assumptions & WACC'!$B${ROW_TERM_G})", _FMT_USD, _FONT_FORMULA, False),
        (
            B_UNDISC_TV,
            "Undiscounted Terminal Value (Gordon Growth)",
            f'=IF(\'Assumptions & WACC\'!$B${ROW_WACC}>\'Assumptions & WACC\'!$B${ROW_TERM_G},B{B_TERM_FCFF}/(\'Assumptions & WACC\'!$B${ROW_WACC}-\'Assumptions & WACC\'!$B${ROW_TERM_G}),"N/A")',
            _FMT_USD,
            _FONT_FORMULA,
            False,
        ),
        (B_TV_DF, "Terminal Value Discount Factor (t = 5)", f"=1/((1+'Assumptions & WACC'!$B${ROW_WACC})^5)", "0.0000", _FONT_FORMULA, False),
        (B_PV_TV, "PV of Terminal Value", f'=IF(ISNUMBER(B{B_UNDISC_TV}),B{B_UNDISC_TV}*B{B_TV_DF},"N/A")', _FMT_USD, _FONT_FORMULA_BOLD, False),
        (B_EV, "Enterprise Value (EV = PV FCFF + PV TV)", f'=IF(ISNUMBER(B{B_PV_TV}),B{B_PV_5Y}+B{B_PV_TV},"N/A")', _FMT_USD, _FONT_FORMULA_BOLD, True),
        (B_TV_SHARE, "Terminal Value % of EV", f'=IF(AND(ISNUMBER(B{B_EV}),B{B_EV}>0),B{B_PV_TV}/B{B_EV},"N/A")', _FMT_PCT, _FONT_FORMULA, False),
        (B_IMP_MULT, "Implied Exit EV / Year-5 EBITDA", f'=IF(AND(ISNUMBER(B{B_UNDISC_TV}),G{P_EBITDA}>0),B{B_UNDISC_TV}/G{P_EBITDA},"N/A")', _FMT_MULT, _FONT_FORMULA, False),
        (B_DEBT, "(-) Debt Deducted (Dynamic ASC 842)", bridge_debt_formula, _FMT_USD, _FONT_LINK, False),
        (B_NCI, "(-) Noncontrolling Interest (NCI)", f"='Historical 5Y & Audit'!${latest_col}${hist_rows['nci']}", _FMT_USD, _FONT_LINK, False),
        (B_CASH, "(+) Total Cash & ST Investments", f"='Historical 5Y & Audit'!${latest_col}${hist_rows['total_cash']}", _FMT_USD, _FONT_LINK, False),
        (B_RAW_EQ, "Raw Implied Equity Value (= EV - Debt - NCI + Cash)", f'=IF(ISNUMBER(B{B_EV}),B{B_EV}-B{B_DEBT}-B{B_NCI}+B{B_CASH},"N/A")', _FMT_USD, _FONT_FORMULA, False),
        (B_IMP_EQ, "Implied Equity Value (Floored at $0)", f'=IF(ISNUMBER(B{B_RAW_EQ}),MAX(0,B{B_RAW_EQ}),"N/A")', _FMT_USD, _FONT_FORMULA_BOLD, True),
        (B_SHARES, "Diluted Shares Used", f"='Assumptions & WACC'!$B${ROW_SHARES}", _FMT_INT, _FONT_LINK, False),
        (B_PER_SHARE, "Implied Value per Share (Gordon Growth)", f'=IF(AND(ISNUMBER(B{B_IMP_EQ}),B{B_SHARES}>0),B{B_IMP_EQ}/B{B_SHARES},"N/A")', _FMT_USD_DEC, _FONT_FORMULA_BOLD, True),
        (B_EXIT_TV, "Exit Multiple Cross-Check: Undiscounted TV", f'=IF(AND(ISNUMBER(\'Assumptions & WACC\'!$B${ROW_EXIT_MULT}),G{P_EBITDA}>0),G{P_EBITDA}*\'Assumptions & WACC\'!$B${ROW_EXIT_MULT},"N/A")', _FMT_USD, _FONT_FORMULA, False),
        (B_EXIT_PV_TV, "Exit Multiple Cross-Check: PV of TV", f'=IF(ISNUMBER(B{B_EXIT_TV}),B{B_EXIT_TV}*B{B_TV_DF},"N/A")', _FMT_USD, _FONT_FORMULA, False),
        (B_EXIT_EV, "Exit Multiple Cross-Check: Enterprise Value", f'=IF(ISNUMBER(B{B_EXIT_PV_TV}),B{B_PV_5Y}+B{B_EXIT_PV_TV},"N/A")', _FMT_USD, _FONT_FORMULA, False),
        (B_EXIT_PS, "Exit Multiple Cross-Check: Value per Share", f'=IF(AND(ISNUMBER(B{B_EXIT_EV}),B{B_SHARES}>0),MAX(0,B{B_EXIT_EV}-B{B_DEBT}-B{B_NCI}+B{B_CASH})/B{B_SHARES},"N/A")', _FMT_USD_DEC, _FONT_FORMULA_BOLD, True),
    )

    for row_idx, label, formula, num_fmt, font, highlight in bridge_specs:
        lbl = ws_dcf.cell(row=row_idx, column=1, value=label)
        lbl.font = _FONT_FORMULA_BOLD if font == _FONT_FORMULA_BOLD else _FONT_FORMULA
        lbl.border = _BORDER_TOTAL if highlight else _BORDER_CELL
        val_c = ws_dcf.cell(row=row_idx, column=2, value=formula)
        val_c.font = font
        val_c.number_format = num_fmt
        val_c.border = _BORDER_TOTAL if highlight else _BORDER_CELL
        if highlight:
            val_c.fill = _FILL_ACCENT

    # Section 3: 2D Sensitivity Matrix (WACC x g) — 100% live formulas with strict $ locking
    SENS_BANNER = 44
    SENS_HDR = 45
    SENS_ROW_START = 46

    n_g = len(sensitivity.terminal_growth_values)
    _style_section_banner(ws_dcf, SENS_BANNER, "3. 2D Sensitivity Matrix: Implied Value per Share (WACC x Terminal Growth g)", max(7, 2 + n_g))

    ws_dcf.cell(row=SENS_HDR, column=1, value="WACC \\ Terminal g").font = _FONT_HEADER
    ws_dcf.cell(row=SENS_HDR, column=1).fill = _FILL_SUBHEADER
    ws_dcf.cell(row=SENS_HDR, column=1).border = _BORDER_HEADER

    g_offsets = _detect_uniform_axis(sensitivity.terminal_growth_values, sensitivity.base_terminal_growth_rate)
    wacc_offsets = _detect_uniform_axis(sensitivity.wacc_values, sensitivity.base_wacc)

    for c_idx, g_val in enumerate(sensitivity.terminal_growth_values):
        col = 2 + c_idx
        if g_offsets is not None:
            off = g_offsets[c_idx]
            cell_val: Any = (
                f"='Assumptions & WACC'!$B${ROW_TERM_G}"
                if abs(off) < Decimal("1e-9")
                else f"='Assumptions & WACC'!$B${ROW_TERM_G}+({float(off):.6f})"
            )
            g_font = _FONT_LINK_BOLD
        else:
            cell_val = float(g_val)
            g_font = _FONT_INPUT_BOLD
        hc = ws_dcf.cell(row=SENS_HDR, column=col, value=cell_val)
        hc.font = g_font
        hc.number_format = _FMT_PCT
        hc.fill = _FILL_SUBHEADER
        hc.border = _BORDER_HEADER
        hc.alignment = Alignment(horizontal="right")

    for r_idx, w_val in enumerate(sensitivity.wacc_values):
        row = SENS_ROW_START + r_idx
        if wacc_offsets is not None:
            off = wacc_offsets[r_idx]
            w_cell_val: Any = (
                f"='Assumptions & WACC'!$B${ROW_WACC}"
                if abs(off) < Decimal("1e-9")
                else f"='Assumptions & WACC'!$B${ROW_WACC}+({float(off):.6f})"
            )
            w_font = _FONT_LINK_BOLD
        else:
            w_cell_val = float(w_val)
            w_font = _FONT_INPUT_BOLD

        wc = ws_dcf.cell(row=row, column=1, value=w_cell_val)
        wc.font = w_font
        wc.number_format = _FMT_PCT
        wc.fill = _FILL_SUBHEADER
        wc.border = _BORDER_CELL

        for c_idx in range(n_g):
            col = 2 + c_idx
            col_ltr = get_column_letter(col)
            # Explicit 5-term non-array PV + Gordon TV + EV->Equity/Share with strict $ locking:
            # $A{row} locks WACC_r, {col_ltr}${SENS_HDR} locks g_c, $C..$G lock 5Y vectors & bridge.
            pv_5y_expr = "+".join(
                f"(${get_column_letter(3 + k)}${P_FCFF}/((1+$A{row})^${get_column_letter(3 + k)}${P_DISC_PER}))"
                for k in range(5)
            )
            tv_expr = f"(($G${P_FCFF}*(1+{col_ltr}${SENS_HDR})/($A{row}-{col_ltr}${SENS_HDR}))/((1+$A{row})^5))"
            eq_expr = f"({pv_5y_expr}+{tv_expr}-$B${B_DEBT}-$B${B_NCI}+$B${B_CASH})"
            formula = (
                f'=IF($A{row}<={col_ltr}${SENS_HDR},"N/A",'
                f'IF($B${B_SHARES}>0,MAX(0,{eq_expr})/$B${B_SHARES},"N/A"))'
            )
            sc = ws_dcf.cell(row=row, column=col, value=formula)
            sc.font = _FONT_FORMULA
            sc.number_format = _FMT_USD_DEC
            sc.border = _BORDER_CELL

            is_center_w = abs(w_val - sensitivity.base_wacc) < Decimal("1e-9")
            is_center_g = abs(sensitivity.terminal_growth_values[c_idx] - sensitivity.base_terminal_growth_rate) < Decimal("1e-9")
            if is_center_w and is_center_g:
                sc.fill = _FILL_BASE_CELL
                sc.font = _FONT_FORMULA_BOLD

    for ws in (ws_assump, ws_hist, ws_dcf):
        _auto_fit_columns(ws)
    # Ensure Column A fits descriptive labels comfortably
    ws_assump.column_dimensions["A"].width = 46
    ws_hist.column_dimensions["A"].width = 42
    ws_dcf.column_dimensions["A"].width = 50

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
