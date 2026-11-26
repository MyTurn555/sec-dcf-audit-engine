"""Plotly figure builders visualizing sec_dcf models without duplicating financial math."""

from __future__ import annotations

from decimal import Decimal

import plotly.graph_objects as go

from sec_dcf.models.analysis import (
    DcfValuationResult,
    HistoricalMetricsReport,
    SensitivityMatrix,
)

BILLION = Decimal("1000000000")
PERCENT_SCALE = Decimal("100")


def _apply_common_layout(fig: go.Figure, *, title: str, yaxis_title: str = "") -> go.Figure:
    """Apply clean, theme-agnostic portfolio styling to a Plotly figure."""
    fig.update_layout(
        title=dict(text=title, x=0.0, xanchor="left", y=0.96, yanchor="top"),
        yaxis_title=yaxis_title,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=45, r=25, t=60, b=80),
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="center", x=0.5),
        hovermode="x unified",
    )
    return fig


def create_revenue_ebit_fcf_chart(metrics: HistoricalMetricsReport) -> go.Figure:
    """Build a 5-year grouped bar chart for Revenue, EBIT, and Simple FCF (USD billions)."""
    years = [f"FY{p.fiscal_year}" for p in metrics.periods]
    revenues = [float(p.revenue / BILLION) for p in metrics.periods]
    ebits = [float(p.ebit / BILLION) for p in metrics.periods]
    fcfs = [float(p.fcf_simple / BILLION) for p in metrics.periods]

    fig = go.Figure(
        data=[
            go.Bar(name="Przychody", x=years, y=revenues, marker_color="#2563EB"),
            go.Bar(name="EBIT", x=years, y=ebits, marker_color="#0EA5E9"),
            go.Bar(name="Simple FCF", x=years, y=fcfs, marker_color="#10B981"),
        ]
    )
    fig.update_layout(barmode="group")
    return _apply_common_layout(
        fig,
        title=f"{metrics.ticker} — Przychody, EBIT i Simple FCF (5Y)",
        yaxis_title="USD (mld)",
    )


def create_roic_and_margins_chart(metrics: HistoricalMetricsReport) -> go.Figure:
    """Build a 5-year line chart for ROIC and operating/EBITDA/net margins (%)."""
    years = [f"FY{p.fiscal_year}" for p in metrics.periods]
    roic = [float(p.roic * PERCENT_SCALE) if p.roic is not None else None for p in metrics.periods]
    ebit_m = [
        float(p.ebit_margin * PERCENT_SCALE) if p.ebit_margin is not None else None
        for p in metrics.periods
    ]
    ebitda_m = [
        float(p.ebitda_margin * PERCENT_SCALE) if p.ebitda_margin is not None else None
        for p in metrics.periods
    ]
    net_m = [
        float(p.net_margin * PERCENT_SCALE) if p.net_margin is not None else None
        for p in metrics.periods
    ]

    fig = go.Figure(
        data=[
            go.Scatter(name="ROIC (%)", x=years, y=roic, mode="lines+markers", line=dict(width=3, color="#10B981")),
            go.Scatter(name="Marża EBITDA (%)", x=years, y=ebitda_m, mode="lines+markers", line=dict(width=2, color="#2563EB")),
            go.Scatter(name="Marża EBIT (%)", x=years, y=ebit_m, mode="lines+markers", line=dict(width=2, color="#F59E0B")),
            go.Scatter(name="Marża netto (%)", x=years, y=net_m, mode="lines+markers", line=dict(width=2, dash="dot", color="#8B5CF6")),
        ]
    )
    lease_suffix = "z leasingami ASC 842" if metrics.include_leases else "bez leasingów"
    return _apply_common_layout(
        fig,
        title=f"{metrics.ticker} — ROIC ({lease_suffix}) i marże rentowności",
        yaxis_title="%",
    )


def create_cash_conversion_capex_chart(metrics: HistoricalMetricsReport) -> go.Figure:
    """Build a 5-year chart comparing Cash Conversion (FCF/Net Income) and CapEx/Revenue."""
    years = [f"FY{p.fiscal_year}" for p in metrics.periods]
    cash_conv = [
        float(p.cash_conversion * PERCENT_SCALE) if p.cash_conversion is not None else None
        for p in metrics.periods
    ]
    capex_rev = [
        float(p.capex_to_revenue * PERCENT_SCALE) if p.capex_to_revenue is not None else None
        for p in metrics.periods
    ]
    da_rev = [
        float(p.da_to_revenue * PERCENT_SCALE) if p.da_to_revenue is not None else None
        for p in metrics.periods
    ]

    fig = go.Figure(
        data=[
            go.Bar(name="Konwersja gotówki (FCF / Net Income)", x=years, y=cash_conv, marker_color="#14B8A6"),
            go.Scatter(name="CapEx / Przychody", x=years, y=capex_rev, mode="lines+markers", line=dict(width=3, color="#EF4444")),
            go.Scatter(name="D&A / Przychody", x=years, y=da_rev, mode="lines+markers", line=dict(width=2, dash="dash", color="#64748B")),
        ]
    )
    return _apply_common_layout(
        fig,
        title=f"{metrics.ticker} — Konwersja gotówki i kapitałochłonność (CapEx / Revenue)",
        yaxis_title="%",
    )


def create_ev_bridge_waterfall_chart(dcf_result: DcfValuationResult) -> go.Figure:
    """
    Build an Enterprise Value -> Equity Value waterfall chart.

    Preserves exact arithmetic consistency by plotting `raw_implied_equity_value` as
    the bridge endpoint and adding an explicit annotation when equity is floored at $0.
    """
    b = dcf_result.bridge
    pv_fcff = float(dcf_result.pv_projected_fcff / BILLION)
    pv_tv = float(dcf_result.terminal_value.pv_terminal_value / BILLION)
    ev = float(b.enterprise_value / BILLION)
    debt = float(-b.debt_deducted / BILLION)
    nci = float(-b.noncontrolling_interest_deducted / BILLION)
    cash = float(b.total_cash_added / BILLION)
    raw_eq = float(b.raw_implied_equity_value / BILLION)

    debt_label = "(-) Dług + leasingi" if b.include_leases else "(-) Dług fin."
    labels = [
        "PV FCFF (5Y)",
        "PV Terminal Value",
        "Enterprise Value",
        debt_label,
        "(-) NCI",
        "(+) Gotówka",
        "Equity Value (surowe)" if b.equity_floored_at_zero else "Implied Equity Value",
    ]
    measures = ["relative", "relative", "total", "relative", "relative", "relative", "total"]
    values = [pv_fcff, pv_tv, ev, debt, nci, cash, raw_eq]
    text_labels = [f"${v:,.1f}B" for v in values]

    fig = go.Figure(
        go.Waterfall(
            name="EV Bridge",
            orientation="v",
            measure=measures,
            x=labels,
            y=values,
            text=text_labels,
            textposition="outside",
            connector={"line": {"color": "rgba(148, 163, 184, 0.5)"}},
            increasing={"marker": {"color": "#10B981"}},
            decreasing={"marker": {"color": "#EF4444"}},
            totals={"marker": {"color": "#2563EB"}},
        )
    )
    _apply_common_layout(
        fig,
        title=(
            f"{dcf_result.ticker} — Most EV -> Equity Value "
            f"(Implikowana wartość / akcję: ${b.implied_value_per_share:,.2f})"
        ),
        yaxis_title="USD (mld)",
    )
    fig.update_layout(hovermode="closest", showlegend=False)

    if b.equity_floored_at_zero:
        fig.add_annotation(
            x=labels[-1],
            y=raw_eq,
            text="Uwaga: surowe Equity < 0 — wartość dla akcjonariuszy ograniczona do $0.00",
            showarrow=True,
            arrowhead=2,
            ay=-40,
            bgcolor="rgba(239, 68, 68, 0.15)",
            bordercolor="#EF4444",
        )
    return fig


def create_sensitivity_heatmap(matrix: SensitivityMatrix) -> go.Figure:
    """Build a 2D WACC x terminal_growth_rate sensitivity heatmap with safe invalid-cell handling."""
    x_labels = [f"{g * PERCENT_SCALE:.2f}%" for g in matrix.terminal_growth_values]
    y_labels = [f"{w * PERCENT_SCALE:.2f}%" for w in matrix.wacc_values]

    z_values: list[list[float | None]] = []
    text_values: list[list[str]] = []
    has_valid_cell = False

    for row in matrix.cells:
        z_row: list[float | None] = []
        t_row: list[str] = []
        for cell in row:
            if cell.is_valid and cell.implied_value_per_share is not None:
                has_valid_cell = True
                val_float = float(cell.implied_value_per_share)
                z_row.append(val_float)
                t_row.append(f"${val_float:,.2f}")
            else:
                z_row.append(None)
                t_row.append("N/A<br>(WACC ≤ g)")
        z_values.append(z_row)
        text_values.append(t_row)

    fig = go.Figure(
        data=go.Heatmap(
            z=z_values,
            x=x_labels,
            y=y_labels,
            text=text_values,
            texttemplate="%{text}",
            colorscale="Blues",
            showscale=has_valid_cell,
            hoverongaps=False,
            colorbar=dict(title="USD / akcję") if has_valid_cell else None,
        )
    )
    fig.update_layout(
        title=f"{matrix.ticker} — Macierz wrażliwości (WACC × Terminal Growth g)",
        xaxis_title="Stopa wzrostu rezydualnego (g)",
        yaxis_title="WACC",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=60, r=30, t=55, b=50),
    )
    if not has_valid_cell:
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            text="Brak komórek spełniających warunek Gordon Growth (WACC > g).",
            showarrow=False,
            font=dict(size=14, color="#EF4444"),
            bgcolor="rgba(239, 68, 68, 0.10)",
            bordercolor="#EF4444",
        )
    return fig


def create_valuation_football_field_chart(
    dcf_result: DcfValuationResult,
    matrix: SensitivityMatrix,
) -> go.Figure:
    """
    Build a horizontal Football Field range chart showing implied value per share
    for user assumptions (strictly educational, no buy/sell or target prices).
    """
    fig = go.Figure()
    min_val = matrix.min_implied_value_per_share
    max_val = matrix.max_implied_value_per_share
    base_val = dcf_result.bridge.implied_value_per_share

    if min_val is None or max_val is None:
        fig.update_layout(
            title=f"{dcf_result.ticker} — Przedział implikowanej wartości na akcję (założenia użytkownika)",
            xaxis_title="Implikowana wartość na akcję (USD)",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=40, r=30, t=55, b=45),
        )
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            text="Brak ważnego przedziału wrażliwości (wszystkie komórki mają WACC ≤ g).",
            showarrow=False,
            font=dict(size=13, color="#EF4444"),
            bgcolor="rgba(239, 68, 68, 0.10)",
            bordercolor="#EF4444",
        )
        return fig

    min_f = float(min_val)
    max_f = float(max_val)
    span_f = max(0.01, max_f - min_f)
    base_f = float(base_val)

    categories = ["DCF (macierz WACC × g)"]
    bases = [min_f]
    widths = [span_f]
    range_texts = [f"${min_f:,.2f} – ${max_f:,.2f}"]

    fig.add_trace(
        go.Bar(
            name="Przedział wrażliwości (założenia użytkownika)",
            y=categories,
            x=widths,
            base=bases,
            orientation="h",
            marker_color="rgba(37, 99, 235, 0.35)",
            marker_line=dict(color="#2563EB", width=2),
            text=range_texts,
            textposition="inside",
            insidetextanchor="middle",
        )
    )
    fig.add_trace(
        go.Scatter(
            name="Wariant bazowy (Gordon Growth)",
            x=[base_f],
            y=["DCF (macierz WACC × g)"],
            mode="markers+text",
            marker=dict(size=14, color="#10B981", symbol="diamond"),
            text=[f"Bazowy: ${base_f:,.2f}"],
            textposition="top center",
        )
    )

    exit_ps = dcf_result.terminal_value.exit_multiple_crosscheck_value_per_share
    if exit_ps is not None:
        exit_f = float(exit_ps)
        fig.add_trace(
            go.Scatter(
                name="Cross-check EV/EBITDA",
                x=[exit_f],
                y=["Cross-check EV/EBITDA"],
                mode="markers+text",
                marker=dict(size=14, color="#F59E0B", symbol="square"),
                text=[f"Mnożnik wyjścia: ${exit_f:,.2f}"],
                textposition="top center",
            )
        )

    fig.update_layout(
        title=dict(
            text=f"{dcf_result.ticker} — Przedział implikowanej wartości (założenia użytkownika)",
            x=0.0,
            xanchor="left",
            y=0.96,
            yanchor="top",
        ),
        xaxis_title="Implikowana wartość na akcję (USD)",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=40, r=30, t=60, b=85),
        legend=dict(orientation="h", yanchor="top", y=-0.22, xanchor="center", x=0.5),
        barmode="overlay",
    )
    return fig
