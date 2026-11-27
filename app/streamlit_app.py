"""Interactive Streamlit + Plotly portfolio dashboard for SEC DCF Audit Engine."""

from __future__ import annotations

import html
import os
from decimal import Decimal
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from sec_dcf import (
    DcfAssumptions,
    DcfValuationError,
    DcfValuationResult,
    FredApiError,
    FredClient,
    GeminiApiError,
    HistoricalMetricsReport,
    MonteCarloConfig,
    MonteCarloResult,
    QualitativeAuditReport,
    RiskFreeRateQuote,
    SecDcfError,
    SensitivityMatrix,
    TenKSectionsDocument,
    TenKTextExtractor,
    WaccCalculationError,
    WaccInputs,
    WaccResult,
    build_sensitivity_matrix,
    calculate_wacc,
    compute_historical_metrics,
    compute_market_multiples,
    export_dcf_workbook_bytes,
    fetch_company_financials,
    run_dcf_valuation,
    run_monte_carlo_dcf,
    suggest_dcf_assumptions,
)
from sec_dcf.ui import (
    AVAILABLE_GEMINI_MODELS,
    DEMO_MARKET_DEFAULTS,
    DEMO_TICKERS,
    FIVE_YEAR_FIELD_SPECS,
    FRED_ATTRIBUTION_TEXT,
    LEGAL_DISCLAIMER_PL,
    QualitativeRunOutcome,
    assumptions_to_5y_dataframe,
    build_audit_trail_dataframe,
    build_dcf_projections_dataframe,
    build_ev_bridge_rows,
    build_historical_metrics_dataframe,
    build_multiples_comparison_dataframe,
    build_sensitivity_dataframe,
    build_sensitivity_grid_params,
    build_validation_summary,
    create_cash_conversion_capex_chart,
    create_ev_bridge_waterfall_chart,
    create_monte_carlo_histogram,
    create_revenue_ebit_fcf_chart,
    create_roic_and_margins_chart,
    create_sensitivity_heatmap,
    create_valuation_football_field_chart,
    dataframe_to_dcf_assumptions,
    load_demo_financials,
    load_demo_qualitative_audit,
    run_qualitative_audit_with_fallback,
)

load_dotenv(override=False)

BILLION = Decimal("1000000000")
PERCENT_SCALE = Decimal("100")

st.set_page_config(
    page_title="SEC DCF Audit Engine",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

_INSTITUTIONAL_CSS = """
<style>
    .block-container {
        padding-top: 1.4rem;
        padding-bottom: 2.5rem;
    }
    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.2rem;
    }
    div[data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 0.65rem 0.8rem;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
    }
    div[data-testid="stMetric"] label {
        color: #475569;
        font-size: 0.78rem;
        font-weight: 600;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        color: #0F172A;
        font-size: 1.42rem !important;
        line-height: 1.25 !important;
        font-weight: 700;
    }
    button[data-baseweb="tab"] {
        font-weight: 600;
        color: #475569;
        padding-top: 0.55rem;
        padding-bottom: 0.55rem;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #1E3A5F;
    }
    .context-bar-header {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
    }
    .context-title-block h2 {
        margin: 0;
        font-size: 1.35rem;
        font-weight: 700;
        color: #0F172A;
        line-height: 1.25;
    }
    .context-title-block p {
        margin: 0.2rem 0 0 0;
        font-size: 0.84rem;
        color: #475569;
    }
    .context-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 0.45rem;
        align-items: center;
    }
    .status-pill {
        display: inline-block;
        padding: 0.24rem 0.65rem;
        border-radius: 9999px;
        font-size: 0.76rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        border: 1px solid transparent;
        white-space: nowrap;
    }
    .pill-pass {
        background-color: #ECFDF5;
        color: #065F46;
        border-color: #A7F3D0;
    }
    .pill-warn {
        background-color: #FFFBEB;
        color: #92400E;
        border-color: #FDE68A;
    }
    .pill-fail {
        background-color: #FEF2F2;
        color: #991B1B;
        border-color: #FECACA;
    }
    .pill-navy {
        background-color: #EFF6FF;
        color: #1E3A5F;
        border-color: #BFDBFE;
    }
    .pill-slate {
        background-color: #F1F5F9;
        color: #334155;
        border-color: #CBD5E1;
    }
    blockquote {
        border-left: 3px solid #1E3A5F;
        background-color: #F8FAFC;
        padding: 0.55rem 0.9rem;
        margin: 0.5rem 0;
        border-radius: 0 6px 6px 0;
        color: #334155;
    }
</style>
"""


def _dec_from_pct(pct_value: float) -> Decimal:
    """Safely convert a float percentage from a Streamlit widget into a Decimal fraction."""
    return Decimal(str(pct_value)) / PERCENT_SCALE


def _dec_from_float(value: float) -> Decimal:
    """Safely convert a float from a Streamlit widget into Decimal without binary artifacts."""
    return Decimal(str(value))


# ---------------------------------------------------------------------------
# Cached deterministic data loaders (never cache GEMINI_API_KEY or AI outputs)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_demo_financials(ticker: str):
    return load_demo_financials(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_demo_qualitative() -> tuple[TenKSectionsDocument, QualitativeAuditReport]:
    return load_demo_qualitative_audit()


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_live_financials(ticker: str, _identity: str | None):
    return fetch_company_financials(ticker, years=5, strict=False, identity=_identity)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_live_10k_sections(ticker: str, _identity: str | None) -> TenKSectionsDocument:
    extractor = TenKTextExtractor(identity=_identity)
    return extractor.fetch_10k_sections(ticker)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_fred_dgs10(_api_key: str | None) -> RiskFreeRateQuote:
    client = FredClient(api_key=_api_key)
    return client.get_latest_rate("DGS10")


# ---------------------------------------------------------------------------
# Session state management (automatic reset on ticker/mode switch)
# ---------------------------------------------------------------------------


def _seed_valuation_session_state(
    *,
    mode: str,
    ticker: str,
    report: Any,
    include_leases: bool,
) -> None:
    """Populate WACC & DCF widget defaults for the active (mode, ticker) context."""
    suggested = suggest_dcf_assumptions(report, include_leases=include_leases)
    latest = sorted(report.periods, key=lambda p: p.period_end)[-1]
    base_fy = latest.fiscal_year

    if mode == "Offline Demo" and ticker in DEMO_MARKET_DEFAULTS:
        demo_cfg = DEMO_MARKET_DEFAULTS[ticker]
        default_rf_pct = float(demo_cfg.risk_free_rate * PERCENT_SCALE)
        default_beta = float(demo_cfg.beta)
        default_erp_pct = float(demo_cfg.equity_risk_premium * PERCENT_SCALE)
        default_share_price = float(demo_cfg.share_price)
        default_equity_mode = "Cena akcji × akcje"
    else:
        default_rf_pct = 4.25
        default_beta = 1.10
        default_erp_pct = 4.50
        default_share_price = 100.00
        default_equity_mode = "Cena akcji × akcje"

    st.session_state["active_valuation_context"] = (mode, ticker)
    st.session_state["wacc_rf_source"] = "Ręczna stopa Rf"
    st.session_state["wacc_rf_pct"] = default_rf_pct
    st.session_state["wacc_beta"] = default_beta
    st.session_state["wacc_erp_pct"] = default_erp_pct
    st.session_state["wacc_equity_mode"] = default_equity_mode
    st.session_state["wacc_share_price"] = default_share_price
    st.session_state["wacc_market_cap_b"] = 100.0
    st.session_state["wacc_kd_override_on"] = False
    st.session_state["wacc_kd_override_pct"] = 5.0
    st.session_state["wacc_credit_spread_pct"] = 1.50
    st.session_state["wacc_tax_override_on"] = False
    st.session_state["wacc_tax_override_pct"] = 21.0
    st.session_state.pop("mc_simulation_entry", None)

    _apply_dcf_assumptions_to_state(suggested, base_fy)


def _apply_dcf_assumptions_to_state(assumptions: DcfAssumptions, base_fy: int) -> None:
    """Write a `DcfAssumptions` object into scalar widget keys and the 5Y DataFrame."""
    st.session_state["dcf_discount_conv"] = assumptions.discounting_convention
    st.session_state["dcf_terminal_g_pct"] = float(assumptions.terminal_growth_rate * PERCENT_SCALE)
    st.session_state["dcf_exit_mult_on"] = assumptions.exit_ebitda_multiple is not None
    st.session_state["dcf_exit_mult"] = (
        float(assumptions.exit_ebitda_multiple)
        if assumptions.exit_ebitda_multiple is not None
        else 15.0
    )
    st.session_state["dcf_rev_growth_pct"] = float(assumptions.revenue_growth[0] * PERCENT_SCALE)
    st.session_state["dcf_ebit_margin_pct"] = float(assumptions.ebit_margin[0] * PERCENT_SCALE)
    st.session_state["dcf_tax_rate_pct"] = float(assumptions.tax_rate[0] * PERCENT_SCALE)
    st.session_state["dcf_da_pct"] = float(assumptions.da_pct_revenue[0] * PERCENT_SCALE)
    st.session_state["dcf_capex_pct"] = float(assumptions.capex_pct_revenue[0] * PERCENT_SCALE)
    st.session_state["dcf_nwc_pct"] = float(assumptions.nwc_pct_revenue[0] * PERCENT_SCALE)
    st.session_state["dcf_sbc_pct"] = float(assumptions.sbc_pct_revenue[0] * PERCENT_SCALE)
    st.session_state["dcf_add_back_sbc"] = assumptions.add_back_sbc
    st.session_state["dcf_5y_df"] = assumptions_to_5y_dataframe(assumptions, base_fy)
    st.session_state.pop("dcf_5y_editor_widget", None)


def _scalar_state_to_assumptions(
    *,
    include_leases: bool,
    exit_ebitda_multiple: Decimal | None,
) -> DcfAssumptions:
    """Construct `DcfAssumptions` from current scalar Streamlit widget state."""
    return DcfAssumptions(
        revenue_growth=_dec_from_pct(st.session_state["dcf_rev_growth_pct"]),
        ebit_margin=_dec_from_pct(st.session_state["dcf_ebit_margin_pct"]),
        tax_rate=_dec_from_pct(st.session_state["dcf_tax_rate_pct"]),
        da_pct_revenue=_dec_from_pct(st.session_state["dcf_da_pct"]),
        capex_pct_revenue=_dec_from_pct(st.session_state["dcf_capex_pct"]),
        nwc_pct_revenue=_dec_from_pct(st.session_state["dcf_nwc_pct"]),
        sbc_pct_revenue=_dec_from_pct(st.session_state["dcf_sbc_pct"]),
        add_back_sbc=bool(st.session_state["dcf_add_back_sbc"]),
        terminal_growth_rate=_dec_from_pct(st.session_state["dcf_terminal_g_pct"]),
        discounting_convention=st.session_state["dcf_discount_conv"],
        include_leases=include_leases,
        exit_ebitda_multiple=exit_ebitda_multiple,
    )


# ---------------------------------------------------------------------------
# Sidebar & Context Header Bar
# ---------------------------------------------------------------------------


def _render_sidebar() -> tuple[str, str, bool]:
    """Render sidebar controls in bordered blocks and persistent legal disclaimer."""
    st.sidebar.markdown("## SEC DCF Audit Engine")
    st.sidebar.caption("Equity Research & Valuation Workbench")

    # (1) Tryb pracy i wybór spółki
    with st.sidebar.container(border=True):
        st.markdown("**1. Tryb pracy i spółka**")
        mode = st.radio(
            "Tryb danych",
            options=["Offline Demo", "Live (SEC EDGAR + FRED + BYOK Gemini)"],
            index=0,
            key="data_mode",
        )
        if mode == "Offline Demo":
            ticker = st.selectbox(
                "Spółka demo (dane 10-K)",
                options=list(DEMO_TICKERS),
                index=0,
                key="demo_ticker",
            )
            st.caption("Dane lokalne bez zapytań sieciowych i bez kluczy API.")
        else:
            raw_ticker = st.text_input(
                "Ticker spółki niefinansowej (US)",
                value=st.session_state.get("live_ticker_input", "MSFT"),
                key="live_ticker_input",
            )
            ticker = raw_ticker.strip().upper() or "MSFT"

    # (2) Reżim księgowy (ASC 842)
    with st.sidebar.container(border=True):
        st.markdown("**2. Reżim księgowy (ASC 842)**")
        include_leases = st.toggle(
            "Uwzględnij zobowiązania leasingowe",
            value=True,
            key="include_leases_toggle",
            help=(
                "Zapewnia pełną spójność między Invested Capital (ROIC), wagą długu w WACC "
                "oraz odjęciem długu w moście EV -> Equity Value."
            ),
        )
        st.caption(
            "Włącza leasingi operacyjne i finansowe do Invested Capital (ROIC), "
            "wag kapitału WACC oraz mostu EV → Equity."
        )

    # (3) Konfiguracja Live / klucze BYOK (wyłącznie w trybie Live)
    if mode != "Offline Demo":
        with st.sidebar.container(border=True):
            st.markdown("**3. Klucze BYOK i tożsamość (sesja)**")
            env_edgar = bool(os.getenv("EDGAR_IDENTITY", "").strip())
            env_fred = bool(os.getenv("FRED_API_KEY", "").strip())
            env_gemini = bool(os.getenv("GEMINI_API_KEY", "").strip())

            st.text_input(
                "EDGAR_IDENTITY (nadpisanie .env)",
                key="session_edgar_identity",
                placeholder="np. AppName kontakt@domena.com",
                help=f"Wykryto w .env: {'Tak' if env_edgar else 'Nie'}",
            )
            st.text_input(
                "FRED_API_KEY (opcjonalny dla DGS10)",
                type="password",
                key="session_fred_api_key",
                help=f"Wykryto w .env: {'Tak' if env_fred else 'Nie'}",
            )
            st.text_input(
                "GEMINI_API_KEY (BYOK dla Zakładki 4)",
                type="password",
                key="session_gemini_api_key",
                help=(
                    "Klucz przechowywany wyłącznie w st.session_state (nigdy na dysku ani w logach). "
                    f"Wykryto w .env: {'Tak' if env_gemini else 'Nie'}"
                ),
            )
            if st.button("Wyczyść klucze z sesji", width="stretch"):
                for key_name in (
                    "session_edgar_identity",
                    "session_fred_api_key",
                    "session_gemini_api_key",
                ):
                    st.session_state[key_name] = ""
                st.toast("Klucze wyczyszczone z pamięci sesji.")

    # (4) Kompaktowa klauzula edukacyjna i atrybucja FRED
    with st.sidebar.container(border=True):
        st.markdown("**Klauzula edukacyjna (u.o.i.f. / MAR)**")
        st.info(LEGAL_DISCLAIMER_PL)
        st.caption(FRED_ATTRIBUTION_TEXT)

    return mode, ticker, include_leases


def _render_context_header_bar(
    *,
    report: Any,
    base_fy: int,
    summary: dict[str, Any],
    include_leases: bool,
    add_back_sbc: bool,
    mode: str,
) -> None:
    """Render the persistent institutional context bar above the 4 main tabs."""
    if not summary["is_valid"]:
        audit_label = "Audyt XBRL: FAIL"
        audit_cls = "pill-fail"
    elif summary["warning_count"] > 0:
        audit_label = f"Audyt XBRL: PASS (uwagi: {summary['warning_count']})"
        audit_cls = "pill-warn"
    else:
        audit_label = "Audyt XBRL: PASS"
        audit_cls = "pill-pass"

    lease_label = "ASC 842: Leasingi włączone" if include_leases else "ASC 842: Bez leasingów"
    sbc_label = "SBC: Wariant CFO (add-back)" if add_back_sbc else "SBC: Koszt ekonomiczny (ex-SBC)"
    mode_label = "Tryb: Offline Demo" if mode == "Offline Demo" else "Tryb: Live EDGAR"

    company_esc = html.escape(str(report.company_name))
    ticker_esc = html.escape(str(report.ticker))
    cik_esc = html.escape(str(summary["cik"]))
    sic_esc = html.escape(str(summary["sic"]))

    with st.container(border=True):
        st.markdown(
            f"""
            <div class="context-bar-header">
                <div class="context-title-block">
                    <h2>{company_esc} ({ticker_esc})</h2>
                    <p>Rok bazowy: <strong>FY{base_fy}</strong> · CIK {cik_esc} · SIC {sic_esc} · Zakres: 5Y 10-K</p>
                </div>
                <div class="context-pills">
                    <span class="status-pill {audit_cls}">{html.escape(audit_label)}</span>
                    <span class="status-pill pill-navy">{html.escape(lease_label)}</span>
                    <span class="status-pill pill-navy">{html.escape(sbc_label)}</span>
                    <span class="status-pill pill-slate">{html.escape(mode_label)}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Tab 1: Fundamentals & XBRL Audit (5Y)
# ---------------------------------------------------------------------------


def _render_tab_financials(
    report: Any,
    metrics_report: HistoricalMetricsReport,
    summary: dict[str, Any],
    include_leases: bool,
) -> None:
    latest_m = metrics_report.latest_period
    nd_ebitda = (
        latest_m.net_debt_to_ebitda_incl_leases
        if include_leases
        else latest_m.net_debt_to_ebitda_excl_leases
    )

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(f"Przychody (FY{latest_m.fiscal_year})", f"${latest_m.revenue / BILLION:,.2f} mld")
    k2.metric(
        "Marża EBIT",
        f"{latest_m.ebit_margin * PERCENT_SCALE:.1f}%" if latest_m.ebit_margin is not None else "N/A",
    )
    k3.metric(
        "ROIC",
        f"{latest_m.roic * PERCENT_SCALE:.1f}%" if latest_m.roic is not None else "N/A",
    )
    k4.metric(
        "True FCF ex-SBC",
        f"${latest_m.fcf_ex_sbc / BILLION:,.2f} mld" if latest_m.fcf_ex_sbc is not None else "N/A",
    )
    k5.metric(
        "Net Debt / EBITDA",
        f"{nd_ebitda:.2f}x" if nd_ebitda is not None else "N/A",
    )

    # 2-column chart grid
    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.plotly_chart(create_revenue_ebit_fcf_chart(metrics_report), width="stretch")
    with c2:
        with st.container(border=True):
            st.plotly_chart(create_roic_and_margins_chart(metrics_report), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        with st.container(border=True):
            st.plotly_chart(create_cash_conversion_capex_chart(metrics_report), width="stretch")
    with c4:
        with st.container(border=True):
            st.markdown("#### Podsumowanie audytu księgowego XBRL")
            a1, a2, a3 = st.columns(3)
            if not summary["is_valid"]:
                audit_status_txt = "FAIL"
            elif summary["warning_count"] > 0:
                audit_status_txt = f"PASS ({summary['warning_count']} uw.)"
            else:
                audit_status_txt = "PASS"
            a1.metric("Status", audit_status_txt)
            a2.metric("Okresy 10-K", str(summary["periods_count"]))
            a3.metric("Błędy / Uwagi", f"{summary['error_count']} / {summary['warning_count']}")

            if not summary["is_valid"]:
                st.error(
                    "Wykryto błędy walidacji księgowej — sprawdź szczegóły poniżej przed interpretacją wyceny."
                )
            elif summary["warning_count"] > 0:
                st.warning(
                    f"Równania bilansowe i spójność RZiS zweryfikowane deterministycznie "
                    f"(CIK: {summary['cik']}, SIC: {summary['sic']}); zgłoszono {summary['warning_count']} "
                    "uwagi informacyjne XBRL."
                )
            else:
                st.success(
                    f"Równania bilansowe i spójność RZiS zweryfikowane deterministycznie bez uwag "
                    f"(CIK: {summary['cik']}, SIC: {summary['sic']})."
                )
            st.caption(
                f"Aktywny reżim: {'z uwzględnieniem leasingów ASC 842' if include_leases else 'bez leasingów ASC 842'}. "
                "Wszystkie wielkości obliczono deterministycznie na typach `Decimal`."
            )

    metrics_df = build_historical_metrics_dataframe(metrics_report)
    with st.container(border=True):
        st.markdown("#### Zestawienie historyczne (5Y)")
        st.dataframe(
            metrics_df,
            width="stretch",
            hide_index=True,
            column_config={
                "Przychody ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "EBIT ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "EBITDA ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "Zysk netto ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "SBC ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "Simple FCF ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "True FCF ex-SBC ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "Marża EBIT (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Marża EBITDA (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Stopa podatkowa NOPAT (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "ROIC (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Konwersja gotówki (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "CapEx / Przychody (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "D&A / Przychody (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "SBC / Przychody (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "SBC / CFO (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Net Debt ($ mld)": st.column_config.NumberColumn(format="%.2f"),
                "Net Debt / EBITDA": st.column_config.NumberColumn(format="%.2fx"),
            },
        )

    if summary["issues"]:
        with st.expander(f"Uwagi walidacyjne XBRL ({len(summary['issues'])})", expanded=not summary["is_valid"]):
            st.dataframe(summary["issues"], width="stretch", hide_index=True)

    with st.expander("Definicje finansowe zastosowane w Module 2", expanded=False):
        st.markdown(
            "- **Simple FCF**: `CFO - CapEx` (przepływy operacyjne minus nakłady inwestycyjne).\n"
            "- **True FCF (ex-SBC)**: `CFO - CapEx - SBC` (koryguje księgowe CFO o niepieniężny koszt wynagrodzeń w akcjach `Stock-Based Compensation`).\n"
            "- **Non-cash NWC**: `(Aktywa obrotowe - Gotówka i kr. inwestycje) - (Zobowiązania kr. - Dług kr. - Krótkoterminowe leasingi)`.\n"
            "- **ROIC**: `NOPAT / Średni Invested Capital` (z lub bez leasingów ASC 842 zależnie od przełącznika w pasku bocznym)."
        )

    with st.expander("Inspektor śladu audytowego XBRL (`audit_trail`)", expanded=False):
        fiscal_years = [p.fiscal_year for p in sorted(report.periods, key=lambda x: x.fiscal_year, reverse=True)]
        selected_fy = st.selectbox("Wybierz rok obrotowy", options=fiscal_years, key="audit_trail_fy")
        audit_df = build_audit_trail_dataframe(report, int(selected_fy))
        st.dataframe(audit_df, width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Tab 2: WACC & DCF Model
# ---------------------------------------------------------------------------


def _render_tab_valuation(
    mode: str,
    report: Any,
    metrics_report: HistoricalMetricsReport,
    include_leases: bool,
) -> tuple[
    WaccInputs | None,
    WaccResult | None,
    DcfAssumptions | None,
    DcfValuationResult | None,
    SensitivityMatrix | None,
    str | None,
]:
    latest = sorted(report.periods, key=lambda p: p.period_end)[-1]
    base_fy = latest.fiscal_year

    # Top container populated after computing current-run valuation so updates show immediately
    top_results_container = st.container()

    col_wacc, col_dcf_ctrl = st.columns([1, 1.35])

    # --- WACC Inputs ---
    wacc_error_msg: str | None = None
    with col_wacc:
        with st.container(border=True):
            st.markdown("#### 1. Parametry WACC i struktura kapitału")

            rf_options = ["Ręczna stopa Rf"]
            if mode != "Offline Demo":
                rf_options.append("Pobierz DGS10 z FRED")

            rf_choice = st.radio(
                "Źródło stopy wolnej od ryzyka (Rf)",
                options=rf_options,
                horizontal=True,
                key="wacc_rf_source",
            )

            live_rf_quote: RiskFreeRateQuote | None = None
            if rf_choice == "Pobierz DGS10 z FRED":
                fred_key = st.session_state.get("session_fred_api_key", "").strip() or None
                try:
                    live_rf_quote = _cached_fred_dgs10(fred_key)
                    st.caption(
                        f"FRED DGS10: **{live_rf_quote.rate * PERCENT_SCALE:.2f}%** "
                        f"(data obserwacji: {live_rf_quote.observation_date})"
                    )
                except FredApiError as exc:
                    wacc_error_msg = f"Błąd FRED API: {exc}"
                    st.error(wacc_error_msg)

            if rf_choice == "Ręczna stopa Rf":
                st.number_input(
                    "Stopa wolna od ryzyka Rf (%)",
                    min_value=-2.0,
                    max_value=25.0,
                    step=0.10,
                    format="%.2f",
                    key="wacc_rf_pct",
                )

            c_b, c_erp = st.columns(2)
            with c_b:
                st.number_input("Beta", min_value=0.10, max_value=5.00, step=0.05, format="%.2f", key="wacc_beta")
            with c_erp:
                st.number_input("Premia za ryzyko ERP (%)", min_value=0.0, max_value=20.0, step=0.10, format="%.2f", key="wacc_erp_pct")

            eq_mode = st.radio(
                "Wartość kapitału własnego do wag WACC",
                options=["Cena akcji × akcje", "Market Cap override ($ mld)", "Kapitał własny księgowy (fallback)"],
                key="wacc_equity_mode",
            )
            if eq_mode == "Cena akcji × akcje":
                st.number_input(
                    "Bieżąca cena akcji ($ — wyłącznie do wag WACC i mnożników)",
                    min_value=0.01,
                    step=1.0,
                    format="%.2f",
                    key="wacc_share_price",
                )
            elif eq_mode == "Market Cap override ($ mld)":
                st.number_input("Market Cap ($ mld)", min_value=0.01, step=10.0, format="%.2f", key="wacc_market_cap_b")

            with st.expander("Zaawansowane nadpisania WACC (Kd, podatek)"):
                kd_on = st.checkbox("Ręczny koszt długu przed podatkiem (Kd)", key="wacc_kd_override_on")
                if kd_on:
                    st.number_input("Pre-tax Kd (%)", min_value=0.0, max_value=25.0, step=0.10, format="%.2f", key="wacc_kd_override_pct")
                st.number_input("Domyślny spread kredytowy przy fallbacku (%)", min_value=0.0, max_value=15.0, step=0.10, format="%.2f", key="wacc_credit_spread_pct")
                tax_on = st.checkbox("Ręczna efektywna stopa podatkowa do tarczy odsetkowej", key="wacc_tax_override_on")
                if tax_on:
                    st.number_input("Stopa podatkowa WACC (%)", min_value=0.0, max_value=60.0, step=0.50, format="%.2f", key="wacc_tax_override_pct")

            # Compute WACC inside try block
            rf_decimal: Decimal | None = None
            if rf_choice == "Ręczna stopa Rf":
                rf_decimal = _dec_from_pct(st.session_state["wacc_rf_pct"])
            elif live_rf_quote is not None:
                rf_decimal = live_rf_quote.rate

            wacc_inputs_obj: WaccInputs | None = None
            wacc_result: WaccResult | None = None
            if rf_decimal is None:
                if wacc_error_msg is None:
                    wacc_error_msg = "Brak stopy Rf (np. błąd FRED API) — przełącz na ręczną stopę Rf lub uzupełnij klucz FRED_API_KEY."
            else:
                wacc_kwargs: dict[str, Any] = {
                    "risk_free_rate": rf_decimal,
                    "beta": _dec_from_float(st.session_state["wacc_beta"]),
                    "equity_risk_premium": _dec_from_pct(st.session_state["wacc_erp_pct"]),
                    "default_credit_spread": _dec_from_pct(st.session_state["wacc_credit_spread_pct"]),
                    "include_leases": include_leases,
                }
                if eq_mode == "Cena akcji × akcje":
                    wacc_kwargs["share_price"] = _dec_from_float(st.session_state["wacc_share_price"])
                elif eq_mode == "Market Cap override ($ mld)":
                    wacc_kwargs["market_cap_override"] = _dec_from_float(st.session_state["wacc_market_cap_b"]) * BILLION
                else:
                    wacc_kwargs["allow_book_equity_fallback"] = True

                if st.session_state["wacc_kd_override_on"]:
                    wacc_kwargs["pretax_cost_of_debt_override"] = _dec_from_pct(st.session_state["wacc_kd_override_pct"])
                if st.session_state["wacc_tax_override_on"]:
                    wacc_kwargs["tax_rate"] = _dec_from_pct(st.session_state["wacc_tax_override_pct"])

                try:
                    wacc_inputs_obj = WaccInputs(**wacc_kwargs)
                    wacc_result = calculate_wacc(report, wacc_inputs_obj)
                except (WaccCalculationError, ValueError) as exc:
                    wacc_error_msg = f"Błąd kalkulacji WACC: {exc}"

            if wacc_result is not None:
                st.divider()
                ws1, ws2, ws3, ws4 = st.columns(4)
                ws1.caption(f"**Ke:** {wacc_result.cost_of_equity * PERCENT_SCALE:.2f}%")
                ws2.caption(f"**Kd po pod.:** {wacc_result.after_tax_cost_of_debt * PERCENT_SCALE:.2f}%")
                ws3.caption(f"**Waga E:** {wacc_result.equity_weight * PERCENT_SCALE:.1f}%")
                ws4.caption(f"**Waga D:** {wacc_result.debt_weight * PERCENT_SCALE:.1f}%")
                if wacc_result.notes:
                    with st.expander("Notatki audytowe WACC"):
                        for note in wacc_result.notes:
                            st.write(f"- {note}")

    # --- DCF Assumptions Controls ---
    assumptions: DcfAssumptions | None = None
    assumptions_error: str | None = None

    with col_dcf_ctrl:
        with st.container(border=True):
            h_left, h_right = st.columns([2.2, 1])
            with h_left:
                st.markdown("#### 2. Założenia projekcji 5Y FCFF/DCF")
            with h_right:
                if st.button("Resetuj z historii", width="stretch"):
                    suggested_reset = suggest_dcf_assumptions(report, include_leases=include_leases)
                    _apply_dcf_assumptions_to_state(suggested_reset, base_fy)
                    st.rerun()

            c_conv, c_g, c_mult = st.columns(3)
            with c_conv:
                st.radio(
                    "Konwencja dyskontowania",
                    options=["mid_year", "end_of_year"],
                    horizontal=True,
                    key="dcf_discount_conv",
                )
            with c_g:
                st.number_input(
                    "Terminal growth g (%)",
                    min_value=-2.0,
                    max_value=8.0,
                    step=0.25,
                    format="%.2f",
                    key="dcf_terminal_g_pct",
                )
            with c_mult:
                exit_on = st.checkbox("Cross-check EV/EBITDA", key="dcf_exit_mult_on")
                if exit_on:
                    st.number_input(
                        "Mnożnik wyjścia EV/EBITDA",
                        min_value=1.0,
                        max_value=60.0,
                        step=0.5,
                        format="%.1f",
                        key="dcf_exit_mult",
                    )

            t_5y, t_sbc = st.columns(2)
            with t_5y:
                edit_5y_mode = st.toggle(
                    "Edycja rok po roku (5Y)",
                    value=False,
                    key="dcf_edit_mode_5y",
                )
            with t_sbc:
                add_back_sbc = st.toggle(
                    "Korekta SBC (wariant CFO)",
                    value=bool(st.session_state.get("dcf_add_back_sbc", False)),
                    key="dcf_add_back_sbc",
                    help=(
                        "GAAP EBIT już zawiera koszt SBC. Domyślnie (OFF) SBC jest traktowane jako realny koszt "
                        "ekonomiczny. Włączenie (ON) dodaje SBC z powrotem do FCFF, aby porównać model z konwencją CFO."
                    ),
                )

            exit_mult_dec = (
                _dec_from_float(st.session_state["dcf_exit_mult"])
                if st.session_state["dcf_exit_mult_on"]
                else None
            )

            if not edit_5y_mode:
                s1, s2, s3 = st.columns(3)
                with s1:
                    st.number_input("Wzrost przychodów (%)", min_value=-50.0, max_value=100.0, step=0.5, format="%.2f", key="dcf_rev_growth_pct")
                    st.number_input("D&A / Przychody (%)", min_value=0.0, max_value=40.0, step=0.25, format="%.2f", key="dcf_da_pct")
                    st.number_input("SBC / Przychody (%)", min_value=0.0, max_value=40.0, step=0.25, format="%.2f", key="dcf_sbc_pct")
                with s2:
                    st.number_input("Marża EBIT (%)", min_value=-50.0, max_value=80.0, step=0.5, format="%.2f", key="dcf_ebit_margin_pct")
                    st.number_input("CapEx / Przychody (%)", min_value=0.0, max_value=50.0, step=0.25, format="%.2f", key="dcf_capex_pct")
                with s3:
                    st.number_input("Stopa podatkowa (%)", min_value=0.0, max_value=60.0, step=0.5, format="%.2f", key="dcf_tax_rate_pct")
                    st.number_input("Non-cash NWC / Przychody (%)", min_value=-40.0, max_value=60.0, step=0.25, format="%.2f", key="dcf_nwc_pct")

                try:
                    assumptions = _scalar_state_to_assumptions(
                        include_leases=include_leases,
                        exit_ebitda_multiple=exit_mult_dec,
                    )
                    st.session_state["dcf_5y_df"] = assumptions_to_5y_dataframe(assumptions, base_fy)
                except Exception as exc:  # noqa: BLE001
                    assumptions_error = f"Nieprawidłowe założenia skalarne: {exc}"
            else:
                st.caption("Edytuj ścieżkę rok-po-roku (wartości w %, np. `15.30` oznacza `15.30%`).")
                col_config = {
                    "Rok": st.column_config.TextColumn("Rok", disabled=True),
                    **{
                        col_label: st.column_config.NumberColumn(col_label, format="%.2f", step=0.25)
                        for _, col_label, _ in FIVE_YEAR_FIELD_SPECS
                    },
                }
                edited_df = st.data_editor(
                    st.session_state["dcf_5y_df"],
                    width="stretch",
                    hide_index=True,
                    column_config=col_config,
                    key="dcf_5y_editor_widget",
                )
                try:
                    assumptions = dataframe_to_dcf_assumptions(
                        edited_df,
                        terminal_growth_rate=_dec_from_pct(st.session_state["dcf_terminal_g_pct"]),
                        discounting_convention=st.session_state["dcf_discount_conv"],
                        include_leases=include_leases,
                        add_back_sbc=add_back_sbc,
                        exit_ebitda_multiple=exit_mult_dec,
                    )
                except DcfValuationError as exc:
                    assumptions_error = str(exc)

    # --- Valuation Execution ---
    dcf_result: DcfValuationResult | None = None
    sensitivity: SensitivityMatrix | None = None
    multiples_cmp = None
    xlsx_bytes: bytes | None = None
    valuation_error_msg: str | None = wacc_error_msg or assumptions_error

    if valuation_error_msg is None and wacc_result is not None and assumptions is not None and wacc_inputs_obj is not None:
        try:
            dcf_result = run_dcf_valuation(report, assumptions, wacc_result)
            wacc_grid, g_grid = build_sensitivity_grid_params(
                wacc_result.wacc,
                assumptions.terminal_growth_rate,
            )
            sensitivity = build_sensitivity_matrix(
                report,
                assumptions,
                wacc_grid,
                g_grid,
                base_wacc=wacc_result,
            )
            multiples_cmp = compute_market_multiples(
                report,
                include_leases=include_leases,
                share_price=wacc_inputs_obj.share_price,
                market_cap_override=wacc_inputs_obj.market_cap_override,
                dcf_result=dcf_result,
            )
            xlsx_bytes = export_dcf_workbook_bytes(
                report=report,
                metrics_report=metrics_report,
                wacc_inputs=wacc_inputs_obj,
                wacc_result=wacc_result,
                assumptions=assumptions,
                dcf_result=dcf_result,
                sensitivity=sensitivity,
                multiples=multiples_cmp,
            )
        except DcfValuationError as exc:
            valuation_error_msg = f"Nie można wyznaczyć wyceny DCF: {exc}"

    # --- Populate Top Results Strip ---
    with top_results_container:
        if valuation_error_msg is not None or dcf_result is None or wacc_result is None:
            with st.container(border=True):
                st.warning(
                    valuation_error_msg
                    or "Uzupełnij poprawne parametry WACC i założeń DCF poniżej, aby przeliczyć model."
                )
        else:
            with st.container(border=True):
                c_hdr, c_export = st.columns([3.0, 1.4])
                with c_hdr:
                    st.markdown("#### Wyniki wyceny FCFF/DCF (założenia użytkownika)")
                with c_export:
                    assert xlsx_bytes is not None
                    st.download_button(
                        label="Eksportuj model do Excela (.xlsx)",
                        data=xlsx_bytes,
                        file_name=f"{report.ticker}_DCF_Audit_FY{base_fy}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary",
                        width="stretch",
                        key="download_dcf_excel",
                    )

                m1, m2, m3, m4, m5, m6 = st.columns(6)
                m1.metric(
                    "Wartość / akcję",
                    f"${dcf_result.bridge.implied_value_per_share:,.2f}",
                )
                m2.metric(
                    "Enterprise Value",
                    f"${dcf_result.bridge.enterprise_value / BILLION:,.2f} mld",
                )
                m3.metric(
                    "Implied Equity",
                    f"${dcf_result.bridge.implied_equity_value / BILLION:,.2f} mld",
                )
                m4.metric(
                    "WACC",
                    f"{wacc_result.wacc * PERCENT_SCALE:.2f}%",
                )
                tv_share = dcf_result.terminal_value.tv_share_of_ev
                m5.metric(
                    "Udział TV w EV",
                    f"{tv_share * PERCENT_SCALE:.1f}%" if tv_share is not None else "N/A",
                )
                m6.metric(
                    "Wpływ SBC / akcję",
                    f"+${dcf_result.sbc_valuation_impact_per_share:,.2f}",
                    delta="Wariant CFO" if dcf_result.add_back_sbc else "Wariant ex-SBC",
                    delta_color="off",
                )
                st.caption(
                    "Implikowana wartość wynika wyłącznie z założeń wprowadzonych przez użytkownika "
                    "i ma charakter edukacyjno-badawczy (brak rekomendacji Kup/Sprzedaj oraz ceny docelowej)."
                )

    if valuation_error_msg is not None or dcf_result is None or multiples_cmp is None:
        return wacc_inputs_obj, wacc_result, assumptions, None, None, valuation_error_msg

    # Domain warnings
    if dcf_result.bridge.equity_floored_at_zero:
        st.warning(
            f"Surowe Equity Value ({dcf_result.bridge.raw_implied_equity_value / BILLION:,.2f} mld USD) "
            "jest ujemne — wartość dla akcjonariuszy ograniczono do $0.00."
        )
    for note in dcf_result.notes:
        if "Warning:" in note or "(> 80%)" in note:
            st.warning(note)

    # --- 2-Column Layout: EV Waterfall | Market vs Implied Multiples ---
    col_wf, col_mult = st.columns([1.15, 1.1])
    with col_wf:
        with st.container(border=True):
            st.plotly_chart(create_ev_bridge_waterfall_chart(dcf_result), width="stretch")
            st.caption(
                f"Liczba akcji ({dcf_result.bridge.shares_source}): "
                f"{dcf_result.bridge.diluted_shares_used / Decimal('1000000'):,.2f} mln | "
                f"Baza NWC: {dcf_result.initial_nwc_source}"
            )
    with col_mult:
        with st.container(border=True):
            st.markdown(f"#### Bieżące vs. Implikowane mnożniki (FY{multiples_cmp.fiscal_year})")
            if multiples_cmp.current_market_cap is None:
                st.caption(
                    "Aktywny tryb kapitału księgowego (`book_equity_fallback`) bez ceny akcji / Market Cap — "
                    "wyświetlane są wyłącznie mnożniki implikowane z wyceny DCF."
                )
            st.dataframe(
                build_multiples_comparison_dataframe(multiples_cmp),
                width="stretch",
                hide_index=True,
                column_config={
                    "Mnożnik / Stopa zwrotu": st.column_config.TextColumn("Mnożnik"),
                    "Bieżący rynek": st.column_config.TextColumn("Rynek"),
                    "Implikowany z DCF": st.column_config.TextColumn("DCF"),
                    "Podstawa": st.column_config.TextColumn("Podstawa"),
                },
            )
            if dcf_result.sbc_valuation_impact_ev != Decimal("0"):
                sbc_mode_desc = (
                    "Wariant CFO (`add_back_sbc=True`)"
                    if dcf_result.add_back_sbc
                    else "Wariant ekonomiczny (`add_back_sbc=False` — GAAP EBIT uwzględnia SBC)"
                )
                st.info(
                    f"**Wpływ traktowania SBC ({sbc_mode_desc}):** "
                    f"różnica między konwencjami wynosi **+${dcf_result.sbc_valuation_impact_ev / BILLION:,.2f} mld EV** "
                    f"(**+${dcf_result.sbc_valuation_impact_per_share:,.2f} / akcję**)."
                )

    # --- Bottom: 5Y FCFF Projections & EV Bridge Details ---
    with st.container(border=True):
        st.markdown("#### Prognoza przepływów FCFF (5Y)")
        st.dataframe(
            build_dcf_projections_dataframe(dcf_result),
            width="stretch",
            hide_index=True,
            column_config={
                "Przychody ($ mld)": st.column_config.NumberColumn("Przych. ($B)", format="%.2f"),
                "EBIT ($ mld)": st.column_config.NumberColumn("EBIT ($B)", format="%.2f"),
                "NOPAT ($ mld)": st.column_config.NumberColumn("NOPAT ($B)", format="%.2f"),
                "D&A ($ mld)": st.column_config.NumberColumn("D&A ($B)", format="%.2f"),
                "CapEx ($ mld)": st.column_config.NumberColumn("CapEx ($B)", format="%.2f"),
                "ΔNWC ($ mld)": st.column_config.NumberColumn("ΔNWC ($B)", format="%.2f"),
                "FCFF ($ mld)": st.column_config.NumberColumn("FCFF ($B)", format="%.2f"),
                "Okres dysk. (t)": st.column_config.NumberColumn("t", format="%.1f"),
                "PV FCFF ($ mld)": st.column_config.NumberColumn("PV FCFF ($B)", format="%.2f"),
            },
        )

    with st.expander("Szczegółowa tabela mostu EV → Implied Equity Value", expanded=False):
        st.dataframe(
            build_ev_bridge_rows(dcf_result),
            width="stretch",
            hide_index=True,
            column_config={"Wartość ($ mld)": st.column_config.NumberColumn(format="%.2f")},
        )

    return wacc_inputs_obj, wacc_result, assumptions, dcf_result, sensitivity, None


# ---------------------------------------------------------------------------
# Tab 3: Sensitivity & Monte Carlo (10,000 scenarios)
# ---------------------------------------------------------------------------


def _render_tab_sensitivity_and_mc(
    report: Any,
    wacc_inputs_obj: WaccInputs | None,
    wacc_result: WaccResult | None,
    assumptions: DcfAssumptions | None,
    dcf_result: DcfValuationResult | None,
    sensitivity: SensitivityMatrix | None,
    valuation_error_msg: str | None,
) -> None:
    if (
        valuation_error_msg is not None
        or wacc_inputs_obj is None
        or wacc_result is None
        or assumptions is None
        or dcf_result is None
        or sensitivity is None
    ):
        with st.container(border=True):
            st.warning(
                valuation_error_msg
                or "Popraw parametry WACC lub założenia DCF w Zakładce 2, aby wyświetlić analizę wrażliwości i Monte Carlo."
            )
        return

    # Top: 2 columns side-by-side (Heatmap | Football Field)
    c_heat, c_ff = st.columns(2)
    with c_heat:
        with st.container(border=True):
            st.plotly_chart(create_sensitivity_heatmap(sensitivity), width="stretch")
            with st.expander("Tabela macierzy wrażliwości (USD / akcję)", expanded=False):
                st.dataframe(
                    build_sensitivity_dataframe(sensitivity),
                    width="stretch",
                )
    with c_ff:
        with st.container(border=True):
            st.plotly_chart(create_valuation_football_field_chart(dcf_result, sensitivity), width="stretch")

    # Main Monte Carlo section in a visible bordered container
    with st.container(border=True):
        st.markdown("#### Symulacja Monte Carlo (10 000 scenariuszy)")
        st.caption(
            "Deterministycznie odtwarzalna symulacja Monte Carlo (parametr `seed`) z równoległym przesunięciem "
            "ścieżek 5Y, opcjonalną korelacją `wzrost ↔ marża EBIT` oraz kontrolą spreadu `WACC - g`."
        )

        mc_c1, mc_c2, mc_c3, mc_c4 = st.columns(4)
        with mc_c1:
            mc_dist = st.selectbox(
                "Rozkład zmiennych",
                options=["normal", "triangular"],
                format_func=lambda opt: "Normalny (Gauss)" if opt == "normal" else "Trójkątny (symetryczny)",
                key="mc_distribution",
            )
        with mc_c2:
            mc_n = st.number_input(
                "Liczba scenariuszy",
                min_value=500,
                max_value=50_000,
                value=10_000,
                step=1_000,
                key="mc_n_simulations",
            )
        with mc_c3:
            mc_seed = st.number_input(
                "Seed (powtarzalność)",
                min_value=0,
                max_value=999_999,
                value=42,
                step=1,
                key="mc_seed",
            )
        with mc_c4:
            mc_min_spread_bps = st.number_input(
                "Min. spread WACC − g (pb)",
                min_value=0,
                max_value=500,
                value=50,
                step=10,
                key="mc_min_spread_bps",
                help="Ścieżki z (WACC − g) poniżej progu są odrzucane, aby uniknąć osobliwości wzoru Gordona.",
            )

        mc_s1, mc_s2, mc_s3, mc_s4, mc_s5 = st.columns(5)
        with mc_s1:
            mc_wacc_std_pct = st.slider(
                "WACC σ (pp)",
                min_value=0.0,
                max_value=5.0,
                value=1.0,
                step=0.1,
                format="%.1f",
                key="mc_wacc_std_pct",
            )
        with mc_s2:
            mc_growth_std_pct = st.slider(
                "Wzrost przych. σ (pp)",
                min_value=0.0,
                max_value=15.0,
                value=3.0,
                step=0.5,
                format="%.1f",
                key="mc_growth_std_pct",
            )
        with mc_s3:
            mc_margin_std_pct = st.slider(
                "Marża EBIT σ (pp)",
                min_value=0.0,
                max_value=10.0,
                value=2.5,
                step=0.25,
                format="%.2f",
                key="mc_margin_std_pct",
            )
        with mc_s4:
            mc_term_g_std_pct = st.slider(
                "Terminal g σ (pp)",
                min_value=0.0,
                max_value=2.0,
                value=0.5,
                step=0.1,
                format="%.1f",
                key="mc_term_g_std_pct",
            )
        with mc_s5:
            mc_corr = st.slider(
                "Korelacja wzrost ↔ marża (ρ)",
                min_value=-0.80,
                max_value=0.80,
                value=0.0,
                step=0.05,
                format="%.2f",
                key="mc_growth_margin_corr",
            )

        mc_config = MonteCarloConfig(
            n_simulations=int(mc_n),
            seed=int(mc_seed),
            distribution=mc_dist,
            wacc_std=_dec_from_pct(mc_wacc_std_pct),
            revenue_growth_std=_dec_from_pct(mc_growth_std_pct),
            ebit_margin_std=_dec_from_pct(mc_margin_std_pct),
            terminal_growth_std=_dec_from_pct(mc_term_g_std_pct),
            growth_margin_correlation=_dec_from_float(mc_corr),
            min_wacc_g_spread=_dec_from_float(mc_min_spread_bps) / Decimal("10000"),
            reference_share_price=wacc_inputs_obj.share_price,
        )

        mc_clicked = st.button(
            "Uruchom symulację Monte Carlo",
            type="primary",
            width="stretch",
            key="run_monte_carlo_btn",
        )

        mc_signature = (
            report.ticker,
            wacc_result.wacc,
            assumptions.model_dump(),
            mc_config.model_dump(),
        )

        if mc_clicked:
            try:
                mc_res = run_monte_carlo_dcf(report, assumptions, wacc_result, mc_config)
                st.session_state["mc_simulation_entry"] = {
                    "signature": mc_signature,
                    "result": mc_res,
                }
            except (DcfValuationError, ValueError) as exc:
                st.error(f"Błąd symulacji Monte Carlo: {exc}")

        mc_entry = st.session_state.get("mc_simulation_entry")
        if mc_entry is not None:
            mc_res: MonteCarloResult = mc_entry["result"]
            if mc_entry["signature"] != mc_signature:
                st.info(
                    "Parametry WACC, założenia DCF lub ustawienia Monte Carlo uległy zmianie od ostatniego "
                    "przeliczenia — kliknij **Uruchom symulację Monte Carlo**, aby zaktualizować rozkład."
                )

            m_p10, m_p50, m_p90, m_mean, m_valid = st.columns(5)
            m_p10.metric(
                "Percentyl P10",
                f"${mc_res.p10_value_per_share:,.2f}" if mc_res.p10_value_per_share is not None else "N/A",
            )
            m_p50.metric(
                "Mediana (P50)",
                f"${mc_res.p50_value_per_share:,.2f}" if mc_res.p50_value_per_share is not None else "N/A",
            )
            m_p90.metric(
                "Percentyl P90",
                f"${mc_res.p90_value_per_share:,.2f}" if mc_res.p90_value_per_share is not None else "N/A",
            )
            m_mean.metric(
                "Średnia ± σ",
                (
                    f"${mc_res.mean_value_per_share:,.2f}"
                    if mc_res.mean_value_per_share is not None
                    else "N/A"
                ),
                delta=(
                    f"± ${mc_res.std_value_per_share:,.2f}"
                    if mc_res.std_value_per_share is not None
                    else None
                ),
                delta_color="off",
            )
            m_valid.metric(
                "Ważne scenariusze",
                f"{mc_res.valid_simulations:,} / {mc_res.total_simulations:,}",
                delta=f"odrzucone: {mc_res.discarded_simulations:,}" if mc_res.discarded_simulations else None,
                delta_color="off",
            )

            if mc_res.prob_above_reference_price is not None and mc_res.reference_share_price is not None:
                st.info(
                    f"**Statystyka referencyjna (edukacyjna):** w **{mc_res.prob_above_reference_price:.1%}** "
                    f"ważnych scenariuszy implikowana wartość przekracza cenę referencyjną "
                    f"**${mc_res.reference_share_price:,.2f}** (przy założeniach użytkownika; "
                    "nie stanowi rekomendacji Kup/Sprzedaj ani ceny docelowej)."
                )

            st.plotly_chart(create_monte_carlo_histogram(mc_res), width="stretch")

            if mc_res.valid_simulations > 0:
                with st.expander("Szczegóły rozkładu Monte Carlo (P25 / P75 / Min / Max)", expanded=False):
                    d_c1, d_c2, d_c3, d_c4, d_c5 = st.columns(5)
                    d_c1.metric("Minimum", f"${mc_res.min_value_per_share:,.2f}")
                    d_c2.metric("Percentyl P25", f"${mc_res.p25_value_per_share:,.2f}")
                    d_c3.metric("Percentyl P75", f"${mc_res.p75_value_per_share:,.2f}")
                    d_c4.metric("Maksimum", f"${mc_res.max_value_per_share:,.2f}")
                    d_c5.metric("Ograniczone do $0.00", f"{mc_res.floored_at_zero_count:,}")
        else:
            st.caption(
                "Kliknij **Uruchom symulację Monte Carlo**, aby wygenerować rozkład implikowanej wartości "
                "na akcję dla bieżących założeń WACC i FCFF."
            )


# ---------------------------------------------------------------------------
# Tab 4: Qualitative 10-K Audit (AI)
# ---------------------------------------------------------------------------


def _render_quote_badge(verified: bool) -> None:
    if verified:
        st.badge("Zweryfikowany cytat", color="green")
    else:
        st.badge("Niezweryfikowany cytat", color="red")


def _render_qualitative_report(outcome: QualitativeRunOutcome) -> None:
    report = outcome.report
    if outcome.fallback_triggered:
        st.warning(
            f"Model `{outcome.requested_model}` zwrócił błąd `503 UNAVAILABLE` — "
            f"automatycznie zastosowano fallback na `{outcome.executed_model}`."
        )

    truncated_sections = [
        sec for sec, is_trunc in report.section_truncation_status.items() if is_trunc
    ]
    trunc_label = ", ".join(truncated_sections) if truncated_sections else "Brak"
    compliance_label = "0 flag" if not report.compliance_flags else f"Flagi: {len(report.compliance_flags)}"

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Spółka (10-K)", f"{report.ticker}")
    m2.metric("verified_quote_ratio", f"{report.verified_quote_ratio * PERCENT_SCALE:.1f}%")
    m3.metric("Zweryfikowane cytaty", f"{report.verified_claims} / {report.total_claims}")
    m4.metric("Przycięcie sekcji", trunc_label)
    m5.metric("Zgodność u.o.i.f.", compliance_label)

    if truncated_sections:
        st.warning(
            "Uwaga: następujące sekcje 10-K zostały przycięte przed wysłaniem do modelu AI "
            f"(weryfikacja cytatów nadal działa na pełnym tekście): {', '.join(truncated_sections)}."
        )

    if report.compliance_flags:
        st.error(
            f"Wykryto {len(report.compliance_flags)} flag(i) zgodności w odpowiedzi AI "
            "(niedozwolone sformułowania doradcze)."
        )
        for flag in report.compliance_flags:
            st.caption(f"- `{flag.field_path}` ({flag.matched_pattern}): {flag.excerpt}")

    if report.warnings:
        with st.expander(f"Ostrzeżenia audytu jakościowego ({len(report.warnings)})", expanded=False):
            for warn in report.warnings:
                st.write(f"- {warn}")

    with st.container(border=True):
        st.markdown(f"#### Executive Summary (`{outcome.executed_model}`)")
        st.write(report.executive_summary)

    sub_risks, sub_growth, sub_flags = st.tabs(
        [
            f"Ryzyka — Item 1A ({len(report.risks)})",
            f"Motory wzrostu — Item 7 ({len(report.growth_drivers)})",
            f"Czerwone flagi ({len(report.red_flags)})",
        ]
    )

    with sub_risks:
        if not report.risks:
            st.caption("Brak wyekstrahowanych ryzyk.")
        for item in report.risks:
            with st.container(border=True):
                top_l, top_r = st.columns([3.5, 1])
                with top_l:
                    st.markdown(
                        f"**{item.title}** · `{item.section}` · Kategoria: `{item.category}` · Istotność: `{item.materiality}`"
                    )
                with top_r:
                    _render_quote_badge(item.quote_verified)
                st.write(item.summary)
                st.markdown(f"> *„{item.verbatim_quote}”*")
                if item.verification_note:
                    st.caption(f"Audyt cytatu: {item.verification_note}")

    with sub_growth:
        if not report.growth_drivers:
            st.caption("Brak wyekstrahowanych motorów wzrostu.")
        for item in report.growth_drivers:
            with st.container(border=True):
                top_l, top_r = st.columns([3.5, 1])
                with top_l:
                    st.markdown(
                        f"**{item.title}** · `{item.section}` · Ton zarządu: `{item.management_tone}`"
                    )
                with top_r:
                    _render_quote_badge(item.quote_verified)
                st.write(item.summary)
                st.markdown(f"> *„{item.verbatim_quote}”*")
                if item.verification_note:
                    st.caption(f"Audyt cytatu: {item.verification_note}")

    with sub_flags:
        if not report.red_flags:
            st.caption("Brak wyekstrahowanych czerwonych flag.")
        for item in report.red_flags:
            with st.container(border=True):
                top_l, top_r = st.columns([3.5, 1])
                with top_l:
                    st.markdown(f"**{item.title}** · `{item.section}` · Waga: `{item.severity}`")
                with top_r:
                    _render_quote_badge(item.quote_verified)
                st.write(item.rationale)
                st.markdown(f"> *„{item.verbatim_quote}”*")
                if item.verification_note:
                    st.caption(f"Audyt cytatu: {item.verification_note}")

    st.caption(report.educational_disclaimer)


def _render_tab_qualitative(mode: str, ticker: str) -> None:
    if mode == "Offline Demo":
        st.info(
            "W trybie Offline Demo prezentujemy gotową próbkę `sample_10k_sections.json` (ACME Cloud Systems), "
            "przepuszczoną lokalnie przez `audit_qualitative_payload` — pokazuje zarówno poprawne cytaty "
            "(odporne na znaki EDGAR), jak i wykrycie 1 zmyślonego cytatu."
        )
        demo_doc, demo_report = _cached_demo_qualitative()
        demo_outcome = QualitativeRunOutcome(
            report=demo_report,
            requested_model=demo_report.model_name,
            executed_model=demo_report.model_name,
            fallback_triggered=False,
        )
        _render_qualitative_report(demo_outcome)
        with st.expander("Podgląd źródłowego tekstu próbki (`Item 1A` i `Item 7`)"):
            if demo_doc.item_1a:
                st.markdown("**Item 1A**")
                st.text(demo_doc.item_1a.raw_text)
            if demo_doc.item_7:
                st.markdown("**Item 7**")
                st.text(demo_doc.item_7.raw_text)
        return

    # Live BYOK Gemini mode
    with st.container(border=True):
        c_model, c_fb, c_btn = st.columns([1.2, 1.5, 1])
        with c_model:
            selected_model = st.selectbox(
                "Model Gemini",
                options=list(AVAILABLE_GEMINI_MODELS),
                index=0,
                key="qual_selected_model",
            )
        with c_fb:
            enable_fallback = st.checkbox(
                "Automatyczny fallback na `gemini-3.5-flash-lite` przy `503 UNAVAILABLE`",
                value=True,
                key="qual_enable_503_fallback",
            )
        with c_btn:
            st.write("")
            run_clicked = st.button(f"Uruchom audyt AI dla {ticker}", type="primary", width="stretch")

    outcomes_by_ticker: dict[str, QualitativeRunOutcome] = st.session_state.setdefault(
        "live_qualitative_outcomes",
        {},
    )

    if run_clicked:
        identity_override = st.session_state.get("session_edgar_identity", "").strip() or None
        gemini_key = st.session_state.get("session_gemini_api_key", "").strip() or None
        try:
            with st.spinner(f"Pobieranie 10-K i uruchamianie analizy ({selected_model})..."):
                doc = _cached_live_10k_sections(ticker, identity_override)
                outcome = run_qualitative_audit_with_fallback(
                    document=doc,
                    selected_model=selected_model,
                    enable_503_fallback=enable_fallback,
                    api_key=gemini_key,
                )
            outcomes_by_ticker[ticker] = outcome
        except (SecDcfError, GeminiApiError, ValueError) as exc:
            st.error(f"Nie udało się wykonać audytu jakościowego dla {ticker}: {exc}")

    existing_outcome = outcomes_by_ticker.get(ticker)
    if existing_outcome is not None:
        _render_qualitative_report(existing_outcome)
    else:
        st.caption(
            f"Podaj klucz `GEMINI_API_KEY` w pasku bocznym (lub w `.env`) i kliknij "
            f"**Uruchom audyt AI dla {ticker}**, aby przeanalizować sekcje `Item 1A` i `Item 7`."
        )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    st.markdown(_INSTITUTIONAL_CSS, unsafe_allow_html=True)
    mode, ticker, include_leases = _render_sidebar()

    try:
        if mode == "Offline Demo":
            report = _cached_demo_financials(ticker)
        else:
            identity_override = st.session_state.get("session_edgar_identity", "").strip() or None
            with st.spinner(f"Pobieranie danych 10-K dla {ticker} z SEC EDGAR..."):
                report = _cached_live_financials(ticker, identity_override)
    except SecDcfError as exc:
        st.error(f"Błąd pobierania lub walidacji danych dla '{ticker}': {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"Nieoczekiwany błąd ładowania danych dla '{ticker}': {exc}")
        return

    context_key = (mode, report.ticker)
    if st.session_state.get("active_valuation_context") != context_key:
        _seed_valuation_session_state(
            mode=mode,
            ticker=report.ticker,
            report=report,
            include_leases=include_leases,
        )

    latest_period = sorted(report.periods, key=lambda p: p.period_end)[-1]
    base_fy = latest_period.fiscal_year
    summary = build_validation_summary(report)
    metrics_report = compute_historical_metrics(report, include_leases=include_leases)

    # Placeholder slot above tabs; populated after Tab 2 so SBC pill reflects live toggle immediately
    context_bar_container = st.container()

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "1. Fundamenty & Audyt XBRL (5Y)",
            "2. Model WACC & DCF",
            "3. Wrażliwość & Monte Carlo",
            "4. Audyt Jakościowy 10-K (AI)",
        ]
    )

    with tab1:
        _render_tab_financials(report, metrics_report, summary, include_leases)
    with tab2:
        (
            wacc_inputs_obj,
            wacc_result,
            assumptions,
            dcf_result,
            sensitivity,
            valuation_error_msg,
        ) = _render_tab_valuation(mode, report, metrics_report, include_leases)
    with tab3:
        _render_tab_sensitivity_and_mc(
            report,
            wacc_inputs_obj,
            wacc_result,
            assumptions,
            dcf_result,
            sensitivity,
            valuation_error_msg,
        )
    with tab4:
        _render_tab_qualitative(mode, report.ticker)

    with context_bar_container:
        _render_context_header_bar(
            report=report,
            base_fy=base_fy,
            summary=summary,
            include_leases=include_leases,
            add_back_sbc=bool(st.session_state.get("dcf_add_back_sbc", False)),
            mode=mode,
        )


if __name__ == "__main__":
    main()
