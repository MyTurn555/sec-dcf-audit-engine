"""Offline unit and integration tests for live-formula Excel (.xlsx) export."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest

from sec_dcf.analysis.dcf import (
    build_sensitivity_matrix,
    run_dcf_valuation,
    suggest_dcf_assumptions,
)
from sec_dcf.analysis.multiples import compute_market_multiples
from sec_dcf.analysis.ratios import compute_historical_metrics
from sec_dcf.analysis.wacc import calculate_wacc
from sec_dcf.export.excel_exporter import export_dcf_workbook_bytes
from sec_dcf.models.analysis import WaccInputs
from sec_dcf.ui.demo_loader import DEMO_MARKET_DEFAULTS, DEMO_TICKERS, load_demo_financials


@pytest.mark.parametrize("ticker", DEMO_TICKERS)
@pytest.mark.parametrize("include_leases", [True, False])
def test_excel_export_contains_live_formulas_and_native_bools_for_demo_tickers(
    ticker: str,
    include_leases: bool,
):
    report = load_demo_financials(ticker)
    demo_cfg = DEMO_MARKET_DEFAULTS[ticker]
    metrics = compute_historical_metrics(report, include_leases=include_leases)
    wacc_inputs = WaccInputs(
        risk_free_rate=demo_cfg.risk_free_rate,
        beta=demo_cfg.beta,
        equity_risk_premium=demo_cfg.equity_risk_premium,
        share_price=demo_cfg.share_price,
        include_leases=include_leases,
    )
    wacc_res = calculate_wacc(report, wacc_inputs)
    assumptions = suggest_dcf_assumptions(
        report,
        include_leases=include_leases,
        discounting_convention="mid_year",
    ).model_copy(
        update={
            "exit_ebitda_multiple": Decimal("18.0"),
            "add_back_sbc": (ticker == "AMZN"),
        }
    )
    dcf_res = run_dcf_valuation(report, assumptions, wacc_res)
    sensitivity = build_sensitivity_matrix(
        report,
        assumptions,
        wacc_values=[
            wacc_res.wacc - Decimal("0.01"),
            wacc_res.wacc - Decimal("0.005"),
            wacc_res.wacc,
            wacc_res.wacc + Decimal("0.005"),
            wacc_res.wacc + Decimal("0.01"),
        ],
        terminal_growth_values=[
            Decimal("0.015"),
            Decimal("0.020"),
            Decimal("0.025"),
            Decimal("0.030"),
            Decimal("0.035"),
        ],
        base_wacc=wacc_res,
    )
    multiples = compute_market_multiples(
        report,
        include_leases=include_leases,
        share_price=demo_cfg.share_price,
        dcf_result=dcf_res,
    )

    xlsx_bytes = export_dcf_workbook_bytes(
        report=report,
        metrics_report=metrics,
        wacc_inputs=wacc_inputs,
        wacc_result=wacc_res,
        assumptions=assumptions,
        dcf_result=dcf_res,
        sensitivity=sensitivity,
        multiples=multiples,
    )
    assert xlsx_bytes[:2] == b"PK"

    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), data_only=False)
    assert wb.sheetnames == ["Assumptions & WACC", "Historical 5Y & Audit", "DCF & Sensitivity"]

    ws_assump = wb["Assumptions & WACC"]
    ws_hist = wb["Historical 5Y & Audit"]
    ws_dcf = wb["DCF & Sensitivity"]

    # Native Python booleans for control toggles (never "TRUE"/"FALSE" strings)
    assert ws_assump["B9"].value is include_leases and isinstance(ws_assump["B9"].value, bool)
    assert ws_assump["B11"].value is (ticker == "AMZN") and isinstance(ws_assump["B11"].value, bool)

    # Dynamic ASC 842 debt link in Sheet 1 WACC and Sheet 3 EV Bridge
    assert str(ws_assump["B26"].value).startswith("=IF(B9,")
    assert str(ws_assump["B20"].value) == "=B17+B18*B19"
    assert str(ws_assump["B30"].value) == "=B28*B20+B29*B23"

    # Historical 5Y & Audit contains live formulas and XBRL audit trail tags
    assert str(ws_hist["F9"].value).startswith("=")  # EBITDA or FCF formula
    audit_text = " ".join(
        str(cell.value)
        for row in ws_hist.iter_rows(min_row=35, max_col=5)
        for cell in row
        if cell.value is not None
    )
    assert "ShareBasedCompensation" in audit_text or "AllocatedShareBasedCompensationExpense" in audit_text

    # DCF & Sensitivity: 5Y projections are live formulas (C6..G20), not frozen numbers
    for col in ("C", "D", "E", "F", "G"):
        for row in (6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20):
            val = ws_dcf[f"{col}{row}"].value
            assert isinstance(val, str) and val.startswith("="), f"Expected formula at {col}{row}, got {val}"
        # Ensure FCFF uses IF('Assumptions & WACC'!$B$11, ...)
        assert "$B$11" in ws_dcf[f"{col}15"].value

    # EV bridge live formulas including dynamic ASC 842 Debt IF()
    assert str(ws_dcf["B23"].value) == "=SUM(C20:G20)"
    assert str(ws_dcf["B28"].value).startswith("=IF(")
    assert str(ws_dcf["B31"].value).startswith("=IF('Assumptions & WACC'!$B$9,")
    assert str(ws_dcf["B35"].value).startswith("=IF(ISNUMBER(B34),MAX(0,B34)")
    assert str(ws_dcf["B37"].value).startswith("=IF(AND(ISNUMBER(B35),B36>0),B35/B36")

    # 2D Sensitivity Matrix: every cell (rows 46..50, cols B..F) is a live English formula with $ locks
    for r in range(46, 51):
        for col in ("B", "C", "D", "E", "F"):
            formula = ws_dcf[f"{col}{r}"].value
            assert isinstance(formula, str) and formula.startswith("=IF(")
            assert f"$A{r}<={col}$45" in formula
            assert ";" not in formula  # strictly comma-separated English XML formula syntax
            assert "$B$31" in formula  # locked bridge debt cell

    # Column widths configured cleanly and freeze_panes enabled
    assert ws_assump.freeze_panes is not None
    assert ws_hist.freeze_panes is not None
    assert ws_dcf.freeze_panes is not None
    assert ws_dcf.column_dimensions["A"].width >= 40


def test_excel_export_5y_path_and_book_equity_fallback():
    report = load_demo_financials("MSFT")
    metrics = compute_historical_metrics(report, include_leases=False)
    wacc_inputs = WaccInputs(
        risk_free_rate=Decimal("0.04"),
        beta=Decimal("1.05"),
        equity_risk_premium=Decimal("0.045"),
        allow_book_equity_fallback=True,
        include_leases=False,
    )
    wacc_res = calculate_wacc(report, wacc_inputs)
    assumptions = suggest_dcf_assumptions(
        report,
        include_leases=False,
        discounting_convention="end_of_year",
    ).model_copy(
        update={
            "revenue_growth": (
                Decimal("0.14"),
                Decimal("0.12"),
                Decimal("0.10"),
                Decimal("0.08"),
                Decimal("0.06"),
            )
        }
    )
    dcf_res = run_dcf_valuation(report, assumptions, wacc_res)
    sensitivity = build_sensitivity_matrix(
        report,
        assumptions,
        wacc_values=[Decimal("0.08"), Decimal("0.09")],
        terminal_growth_values=[Decimal("0.02"), Decimal("0.03")],
        base_wacc=wacc_res,
    )
    xlsx_bytes = export_dcf_workbook_bytes(
        report=report,
        metrics_report=metrics,
        wacc_inputs=wacc_inputs,
        wacc_result=wacc_res,
        assumptions=assumptions,
        dcf_result=dcf_res,
        sensitivity=sensitivity,
    )
    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), data_only=False)
    ws_assump = wb["Assumptions & WACC"]
    assert ws_assump["B34"].value == pytest.approx(0.14)
    assert ws_assump["F34"].value == pytest.approx(0.06)
    assert ws_assump["B10"].value == "end_of_year"
    assert ws_assump["B24"].value is None
    assert isinstance(ws_assump["B25"].value, (int, float))
    assert ws_assump["B25"].value == pytest.approx(float(wacc_res.equity_value_for_weights))
