"""Plotly figure builders visualizing sec_dcf models without duplicating financial math."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import plotly.graph_objects as go

from sec_dcf.models.analysis import (
    DcfValuationResult,
    HistoricalMetricsReport,
    MonteCarloResult,
    SensitivityMatrix,
)

BILLION = Decimal("1000000000")
PERCENT_SCALE = Decimal("100")

# Institutional Equity Research / Valuation Workbench palette
PALETTE_NAVY = "#1E3A5F"
PALETTE_STEEL = "#2563EB"
PALETTE_SLATE = "#64748B"
PALETTE_EMERALD = "#059669"
PALETTE_RED = "#DC2626"

TEXT_DARK = "#0F172A"
TEXT_MUTED = "#475569"
GRID_COLOR = "#E2E8F0"
AXIS_LINE_COLOR = "#CBD5E1"
FONT_FAMILY = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"


def _apply_common_layout(
    fig: go.Figure,
    *,
    title: str,
    yaxis_title: str = "",
    xaxis_title: str = "",
    show_legend: bool = True,
    height: int = 380,
) -> go.Figure:
    """Apply unified Equity Research styling to a Plotly figure."""
    fig.update_layout(
        title=dict(
            text=title,
            x=0.0,
            xanchor="left",
            y=0.97,
            yanchor="top",
            font=dict(family=FONT_FAMILY, size=13.5, color=PALETTE_NAVY),
        ),
        font=dict(family=FONT_FAMILY, size=12, color=TEXT_DARK),
        yaxis_title=yaxis_title,
        xaxis_title=xaxis_title,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=48, r=24, t=88 if show_legend else 64, b=48),
        showlegend=show_legend,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0.0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=11, color=TEXT_MUTED),
        ),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#FFFFFF",
            bordercolor=AXIS_LINE_COLOR,
            font=dict(family=FONT_FAMILY, size=12, color=TEXT_DARK),
        ),
    )
    fig.update_xaxes(
        showgrid=False,
        showline=True,
        linecolor=AXIS_LINE_COLOR,
        tickfont=dict(color=TEXT_MUTED),
        title_font=dict(color=TEXT_MUTED, size=12),
        automargin=True,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=GRID_COLOR,
        gridwidth=1,
        zeroline=True,
        zerolinecolor=AXIS_LINE_COLOR,
        zerolinewidth=1,
        tickfont=dict(color=TEXT_MUTED),
        title_font=dict(color=TEXT_MUTED, size=12),
        automargin=True,
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
            go.Bar(
                name="Przychody",
                x=years,
                y=revenues,
                marker_color=PALETTE_NAVY,
                hovertemplate="%{y:,.2f} mld USD",
            ),
            go.Bar(
                name="EBIT",
                x=years,
                y=ebits,
                marker_color=PALETTE_STEEL,
                hovertemplate="%{y:,.2f} mld USD",
            ),
            go.Bar(
                name="Simple FCF",
                x=years,
                y=fcfs,
                marker_color=PALETTE_EMERALD,
                hovertemplate="%{y:,.2f} mld USD",
            ),
        ]
    )
    fig.update_layout(barmode="group", bargap=0.22, bargroupgap=0.08)
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
            go.Scatter(
                name="ROIC (%)",
                x=years,
                y=roic,
                mode="lines+markers",
                line=dict(width=3, color=PALETTE_EMERALD),
                marker=dict(size=7),
                hovertemplate="%{y:.1f}%",
            ),
            go.Scatter(
                name="Marża EBITDA (%)",
                x=years,
                y=ebitda_m,
                mode="lines+markers",
                line=dict(width=2.2, color=PALETTE_NAVY),
                marker=dict(size=6),
                hovertemplate="%{y:.1f}%",
            ),
            go.Scatter(
                name="Marża EBIT (%)",
                x=years,
                y=ebit_m,
                mode="lines+markers",
                line=dict(width=2.2, color=PALETTE_STEEL),
                marker=dict(size=6),
                hovertemplate="%{y:.1f}%",
            ),
            go.Scatter(
                name="Marża netto (%)",
                x=years,
                y=net_m,
                mode="lines+markers",
                line=dict(width=2, dash="dot", color=PALETTE_SLATE),
                marker=dict(size=5),
                hovertemplate="%{y:.1f}%",
            ),
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
            go.Bar(
                name="Konwersja gotówki (FCF / Net Income)",
                x=years,
                y=cash_conv,
                marker_color="rgba(37, 99, 235, 0.78)",
                hovertemplate="%{y:.1f}%",
            ),
            go.Scatter(
                name="CapEx / Przychody",
                x=years,
                y=capex_rev,
                mode="lines+markers",
                line=dict(width=3, color=PALETTE_RED),
                marker=dict(size=7),
                hovertemplate="%{y:.1f}%",
            ),
            go.Scatter(
                name="D&A / Przychody",
                x=years,
                y=da_rev,
                mode="lines+markers",
                line=dict(width=2, dash="dash", color=PALETTE_SLATE),
                marker=dict(size=6),
                hovertemplate="%{y:.1f}%",
            ),
        ]
    )
    return _apply_common_layout(
        fig,
        title=f"{metrics.ticker} — Konwersja gotówki i CapEx / Revenue",
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
        "Equity (surowe)" if b.equity_floored_at_zero else "Implied Equity",
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
            cliponaxis=False,
            textfont=dict(size=11, color=TEXT_DARK),
            connector={"line": {"color": "rgba(100, 116, 139, 0.45)", "width": 1.2}},
            increasing={"marker": {"color": PALETTE_EMERALD}},
            decreasing={"marker": {"color": PALETTE_RED}},
            totals={"marker": {"color": PALETTE_NAVY}},
            hovertemplate="%{x}: $%{y:,.2f} mld<extra></extra>",
        )
    )
    _apply_common_layout(
        fig,
        title=(
            f"{dcf_result.ticker} — Most EV → Equity Value "
            f"(${b.implied_value_per_share:,.2f} / akcję)"
        ),
        yaxis_title="USD (mld)",
        show_legend=False,
        height=400,
    )
    fig.update_layout(hovermode="closest", margin=dict(l=48, r=24, t=72, b=56))

    if b.equity_floored_at_zero:
        fig.add_annotation(
            x=labels[-1],
            y=raw_eq,
            text="Uwaga: surowe Equity < 0 — wartość dla akcjonariuszy ograniczona do $0.00",
            showarrow=True,
            arrowhead=2,
            ay=-40,
            font=dict(size=11, color=PALETTE_RED),
            bgcolor="rgba(220, 38, 38, 0.10)",
            bordercolor=PALETTE_RED,
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
            textfont=dict(size=11),
            colorscale="Blues",
            showscale=has_valid_cell,
            hoverongaps=False,
            hovertemplate="WACC: %{y}<br>g: %{x}<br>Wartość: %{text}<extra></extra>",
            colorbar=dict(title="USD / akcję", thickness=14) if has_valid_cell else None,
        )
    )
    _apply_common_layout(
        fig,
        title=f"{matrix.ticker} — Macierz wrażliwości (WACC × Terminal Growth g)",
        xaxis_title="Stopa wzrostu rezydualnego (g)",
        yaxis_title="WACC",
        show_legend=False,
        height=390,
    )
    fig.update_layout(hovermode="closest", margin=dict(l=64, r=28, t=64, b=52))
    fig.update_yaxes(showgrid=False)

    if not has_valid_cell:
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            text="Brak komórek spełniających warunek Gordon Growth (WACC > g).",
            showarrow=False,
            font=dict(size=13, color=PALETTE_RED),
            bgcolor="rgba(220, 38, 38, 0.10)",
            bordercolor=PALETTE_RED,
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
        _apply_common_layout(
            fig,
            title=f"{dcf_result.ticker} — Przedział implikowanej wartości (założenia użytkownika)",
            xaxis_title="Implikowana wartość na akcję (USD)",
            show_legend=False,
            height=390,
        )
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            text="Brak ważnego przedziału wrażliwości (wszystkie komórki mają WACC ≤ g).",
            showarrow=False,
            font=dict(size=13, color=PALETTE_RED),
            bgcolor="rgba(220, 38, 38, 0.10)",
            bordercolor=PALETTE_RED,
        )
        return fig

    min_f = float(min_val)
    max_f = float(max_val)
    span_f = max(0.01, max_f - min_f)
    base_f = float(base_val)

    categories = ["DCF (WACC × g)"]
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
            marker_color="rgba(37, 99, 235, 0.25)",
            marker_line=dict(color=PALETTE_STEEL, width=2),
            hovertemplate=f"Przedział WACC × g: ${min_f:,.2f} – ${max_f:,.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            name="Wariant bazowy (Gordon Growth)",
            x=[base_f],
            y=["DCF (WACC × g)"],
            mode="markers+text",
            marker=dict(size=13, color=PALETTE_EMERALD, symbol="diamond"),
            text=[f"Bazowy: ${base_f:,.2f}"],
            textposition="top center",
            textfont=dict(color=PALETTE_EMERALD, size=12),
            cliponaxis=False,
            hovertemplate=f"Wariant bazowy: ${base_f:,.2f}<extra></extra>",
        )
    )
    fig.add_annotation(
        x=(min_f + max_f) / 2.0,
        y="DCF (WACC × g)",
        text=f"Przedział WACC × g: ${min_f:,.2f} – ${max_f:,.2f}",
        showarrow=False,
        yshift=-42,
        font=dict(size=11.5, color=PALETTE_NAVY),
        bgcolor="rgba(255, 255, 255, 0.88)",
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
                marker=dict(size=13, color=PALETTE_NAVY, symbol="square"),
                text=[f"Mnożnik wyjścia: ${exit_f:,.2f}"],
                textposition="top center",
                textfont=dict(color=PALETTE_NAVY, size=12),
                cliponaxis=False,
                hovertemplate=f"Cross-check EV/EBITDA: ${exit_f:,.2f}<extra></extra>",
            )
        )

    _apply_common_layout(
        fig,
        title=f"{dcf_result.ticker} — Przedział implikowanej wartości (DCF)",
        xaxis_title="Implikowana wartość na akcję (USD)",
        show_legend=True,
        height=390,
    )
    fig.update_layout(barmode="overlay", hovermode="closest")
    fig.update_xaxes(showgrid=True, gridcolor=GRID_COLOR)
    fig.update_yaxes(showgrid=False)
    return fig


def create_monte_carlo_histogram(mc_result: MonteCarloResult) -> go.Figure:
    """
    Build a Plotly histogram of Monte Carlo simulated implied equity value per share,
    highlighting P10-P90, P50 median, baseline DCF, and optional reference share price.
    """
    fig = go.Figure()
    if mc_result.valid_simulations == 0 or not mc_result.simulated_values_per_share:
        _apply_common_layout(
            fig,
            title=f"{mc_result.ticker} — Symulacja Monte Carlo (brak ważnych scenariuszy)",
            xaxis_title="Implikowana wartość na akcję (USD)",
            yaxis_title="Liczba scenariuszy",
            show_legend=False,
            height=410,
        )
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            text=(
                "Wszystkie wylosowane ścieżki odrzucone przez warunek "
                f"(WACC - g ≥ {mc_result.min_wacc_g_spread:.2%})."
            ),
            showarrow=False,
            font=dict(size=13, color=PALETTE_RED),
            bgcolor="rgba(220, 38, 38, 0.10)",
            bordercolor=PALETTE_RED,
        )
        return fig

    values = np.asarray(mc_result.simulated_values_per_share, dtype=np.float64)
    p99 = float(np.percentile(values, 99.0))
    max_val = float(np.max(values))

    anchors = [p99]
    if mc_result.base_implied_value_per_share is not None:
        anchors.append(float(mc_result.base_implied_value_per_share))
    if mc_result.reference_share_price is not None:
        anchors.append(float(mc_result.reference_share_price))

    upper_anchor = max(anchors)
    upper_cap = upper_anchor * 1.08 if upper_anchor > 0.0 else p99 + 1.0

    clipped_tail_count = 0
    plot_values = values
    if values.size >= 20 and max_val > upper_cap:
        filtered = values[values <= upper_cap]
        if filtered.size > 0:
            clipped_tail_count = int(values.size - filtered.size)
            plot_values = filtered

    fig.add_trace(
        go.Histogram(
            x=plot_values.tolist(),
            nbinsx=55,
            name="Rozkład wartości / akcję",
            marker_color="rgba(37, 99, 235, 0.65)",
            marker_line=dict(color=PALETTE_NAVY, width=0.6),
            hovertemplate="Przedział: $%{x:,.2f}<br>Scenariusze: %{y}<extra></extra>",
        )
    )

    p10_f = float(mc_result.p10_value_per_share) if mc_result.p10_value_per_share is not None else None
    p50_f = float(mc_result.p50_value_per_share) if mc_result.p50_value_per_share is not None else None
    p90_f = float(mc_result.p90_value_per_share) if mc_result.p90_value_per_share is not None else None

    if p10_f is not None and p90_f is not None and p10_f < p90_f:
        fig.add_vrect(
            x0=p10_f,
            x1=p90_f,
            fillcolor="rgba(5, 150, 105, 0.10)",
            line_width=0,
        )

    if p10_f is not None:
        fig.add_vline(
            x=p10_f,
            line_dash="dot",
            line_width=1.6,
            line_color=PALETTE_SLATE,
            annotation_text=f"P10: ${p10_f:,.2f}",
            annotation_position="bottom right",
            annotation_font=dict(size=11, color=PALETTE_SLATE),
            annotation_bgcolor="rgba(255, 255, 255, 0.88)",
        )

    if p90_f is not None:
        fig.add_vline(
            x=p90_f,
            line_dash="dot",
            line_width=1.6,
            line_color=PALETTE_SLATE,
            annotation_text=f"P90: ${p90_f:,.2f}",
            annotation_position="bottom right",
            annotation_font=dict(size=11, color=PALETTE_SLATE),
            annotation_bgcolor="rgba(255, 255, 255, 0.88)",
        )

    if p50_f is not None:
        fig.add_vline(
            x=p50_f,
            line_dash="solid",
            line_width=2.5,
            line_color=PALETTE_EMERALD,
            annotation_text=f"Mediana P50: ${p50_f:,.2f}",
            annotation_position="top left",
            annotation_font=dict(size=11, color=PALETTE_EMERALD),
            annotation_bgcolor="rgba(255, 255, 255, 0.88)",
        )

    if mc_result.base_implied_value_per_share is not None:
        base_f = float(mc_result.base_implied_value_per_share)
        fig.add_vline(
            x=base_f,
            line_dash="dash",
            line_width=2.0,
            line_color=PALETTE_NAVY,
            annotation_text=f"Bazowy DCF: ${base_f:,.2f}",
            annotation_position="top right",
            annotation_yshift=-24,
            annotation_font=dict(size=11, color=PALETTE_NAVY),
            annotation_bgcolor="rgba(255, 255, 255, 0.88)",
        )

    if mc_result.reference_share_price is not None:
        ref_f = float(mc_result.reference_share_price)
        fig.add_vline(
            x=ref_f,
            line_dash="dot",
            line_width=2.4,
            line_color=PALETTE_RED,
            annotation_text=f"Cena ref.: ${ref_f:,.2f}",
            annotation_position="top left",
            annotation_font=dict(size=11, color=PALETTE_RED),
            annotation_bgcolor="rgba(255, 255, 255, 0.88)",
        )

    dist_label = "Normalny" if mc_result.distribution == "normal" else "Trójkątny"
    _apply_common_layout(
        fig,
        title=(
            f"{mc_result.ticker} — Monte Carlo DCF "
            f"({mc_result.valid_simulations:,} ważnych scenariuszy, rozkład: {dist_label}, seed={mc_result.seed})"
        ),
        xaxis_title="Implikowana wartość na akcję (USD)",
        yaxis_title="Liczba scenariuszy",
        show_legend=False,
        height=430,
    )
    fig.update_layout(
        hovermode="x",
        margin=dict(l=50, r=30, t=78, b=72),
    )

    if clipped_tail_count > 0:
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=1.0,
            y=-0.20,
            xanchor="right",
            yanchor="top",
            text=(
                f"Uwaga: przycięto prawy ogon wykresu powyżej ${upper_cap:,.2f} "
                f"(pominięto {clipped_tail_count} skrajnych obserwacji; pełne statystyki w kartach)."
            ),
            showarrow=False,
            font=dict(size=11, color=PALETTE_SLATE),
        )
    return fig
