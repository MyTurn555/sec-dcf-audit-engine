"""Interactive Streamlit + Plotly portfolio dashboard for SEC DCF Audit Engine."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from sec_dcf import (
    DcfAssumptions,
    DcfValuationError,
    FredApiError,
    FredClient,
    GeminiApiError,
    QualitativeAuditReport,
    RiskFreeRateQuote,
    SecDcfError,
    TenKTextExtractor,
    TenKSectionsDocument,
    WaccCalculationError,
    WaccInputs,
    build_sensitivity_matrix,
    calculate_wacc,
    compute_historical_metrics,
    compute_market_multiples,
    export_dcf_workbook_bytes,
    fetch_company_financials,
    run_dcf_valuation,
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
# Sidebar rendering
# ---------------------------------------------------------------------------


def _render_sidebar() -> tuple[str, str, bool]:
    """Render sidebar controls and persistent legal disclaimer."""
    st.sidebar.title("SEC DCF Audit Engine")
    st.sidebar.caption("Deterministyczna wycena FCFF/DCF + audytowalny AI 10-K")

    mode = st.sidebar.radio(
        "Tryb danych",
        options=["Offline Demo", "Live (SEC EDGAR + FRED + BYOK Gemini)"],
        index=0,
        key="data_mode",
    )

    if mode == "Offline Demo":
        ticker = st.sidebar.selectbox(
            "Spółka demo (lokalne dane 10-K)",
            options=list(DEMO_TICKERS),
            index=0,
            key="demo_ticker",
        )
        st.sidebar.success("Tryb Offline Demo: zero zapytań sieciowych i brak wymogu kluczy API.")
    else:
        raw_ticker = st.sidebar.text_input(
            "Ticker spółki niefinansowej (US)",
            value=st.session_state.get("live_ticker_input", "MSFT"),
            key="live_ticker_input",
        )
        ticker = raw_ticker.strip().upper() or "MSFT"

        with st.sidebar.expander("Klucze i tożsamość (BYOK — tylko w sesji)", expanded=True):
            env_edgar = bool(os.getenv("EDGAR_IDENTITY", "").strip())
            env_fred = bool(os.getenv("FRED_API_KEY", "").strip())
            env_gemini = bool(os.getenv("GEMINI_API_KEY", "").strip())

            st.text_input(
                "EDGAR_IDENTITY (opcjonalne nadpisanie .env)",
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
                "GEMINI_API_KEY (BYOK dla Zakładki 3)",
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

    include_leases = st.sidebar.toggle(
        "Uwzględnij zobowiązania leasingowe (ASC 842)",
        value=True,
        key="include_leases_toggle",
        help=(
            "Zapewnia pełną spójność między Invested Capital (ROIC), wagą długu w WACC "
            "oraz odjęciem długu w moście EV -> Equity Value."
        ),
    )

    st.sidebar.divider()
    st.sidebar.markdown("### Klauzula edukacyjna (u.o.i.f. / MAR)")
    st.sidebar.info(LEGAL_DISCLAIMER_PL)
    st.sidebar.caption(FRED_ATTRIBUTION_TEXT)

    return mode, ticker, include_leases


# ---------------------------------------------------------------------------
# Tab 1: Financials & 5Y Ratios
# ---------------------------------------------------------------------------


def _render_tab_financials(report: Any, include_leases: bool) -> None:
    st.subheader(f"{report.company_name} ({report.ticker}) — Sprawozdania 10-K i wskaźniki 5Y")

    summary = build_validation_summary(report)
    col_status, col_periods, col_errors, col_warnings = st.columns(4)
    col_status.metric("Walidacja księgowa", "PASS" if summary["is_valid"] else "FAIL")
    col_periods.metric("Okresy 10-K", str(summary["periods_count"]))
    col_errors.metric("Błędy (error)", str(summary["error_count"]))
    col_warnings.metric("Ostrzeżenia (warning)", str(summary["warning_count"]))

    if summary["is_valid"]:
        st.success(
            f"Równania bilansowe i spójność RZiS zweryfikowane deterministycznie "
            f"(CIK: {summary['cik']}, SIC: {summary['sic']})."
        )
    else:
        st.error(
            "Wykryto błędy walidacji księgowej — sprawdź szczegóły poniżej przed interpretacją wyceny."
        )

    if summary["issues"]:
        with st.expander(f"Lista uwag walidacyjnych ({len(summary['issues'])})", expanded=not summary["is_valid"]):
            st.dataframe(summary["issues"], width="stretch", hide_index=True)

    with st.expander("Ślad audytowy XBRL (`audit_trail`)", expanded=False):
        fiscal_years = [p.fiscal_year for p in sorted(report.periods, key=lambda x: x.fiscal_year, reverse=True)]
        selected_fy = st.selectbox("Wybierz rok obrotowy", options=fiscal_years, key="audit_trail_fy")
        audit_df = build_audit_trail_dataframe(report, int(selected_fy))
        st.dataframe(audit_df, width="stretch", hide_index=True)

    metrics_report = compute_historical_metrics(report, include_leases=include_leases)
    metrics_df = build_historical_metrics_dataframe(metrics_report)

    st.markdown("#### Panel wskaźników historycznych (5Y)")
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

    with st.expander("Definicje finansowe zastosowane w Module 2"):
        st.markdown(
            "- **Simple FCF**: `CFO - CapEx` (przepływy operacyjne minus nakłady inwestycyjne).\n"
            "- **True FCF (ex-SBC)**: `CFO - CapEx - SBC` (koryguje księgowe CFO o niepieniężny koszt wynagrodzeń w akcjach `Stock-Based Compensation`).\n"
            "- **Non-cash NWC**: `(Aktywa obrotowe - Gotówka i kr. inwestycje) - (Zobowiązania kr. - Dług kr. - Krótkoterminowe leasingi)`.\n"
            "- **ROIC**: `NOPAT / Średni Invested Capital` (z lub bez leasingów ASC 842 zależnie od przełącznika w pasku bocznym)."
        )

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(create_revenue_ebit_fcf_chart(metrics_report), width="stretch")
    with c2:
        st.plotly_chart(create_roic_and_margins_chart(metrics_report), width="stretch")

    st.plotly_chart(create_cash_conversion_capex_chart(metrics_report), width="stretch")


# ---------------------------------------------------------------------------
# Tab 2: WACC & FCFF/DCF Valuation
# ---------------------------------------------------------------------------


def _render_tab_valuation(mode: str, report: Any, include_leases: bool) -> None:
    st.subheader(f"{report.ticker} — WACC i deterministyczny model FCFF/DCF (5Y)")

    latest = sorted(report.periods, key=lambda p: p.period_end)[-1]
    base_fy = latest.fiscal_year

    col_wacc, col_dcf_ctrl = st.columns([1, 1.3])

    # --- WACC Inputs ---
    with col_wacc:
        st.markdown("#### 1. Parametry WACC (CAPM + struktura kapitału)")

        rf_options = ["Ręczna stopa Rf"]
        if mode != "Offline Demo":
            rf_options.append("Pobierz DGS10 z FRED")

        rf_choice = st.radio("Źródło stopy wolnej od ryzyka (Rf)", options=rf_options, horizontal=True, key="wacc_rf_source")

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
                st.error(f"Błąd FRED API: {exc}")

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
            st.number_input("Bieżąca cena akcji ($ — wyłącznie do wag WACC)", min_value=0.01, step=1.0, format="%.2f", key="wacc_share_price")
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

    # Build WaccInputs & compute WaccResult
    rf_decimal: Decimal | None = None
    if rf_choice == "Ręczna stopa Rf":
        rf_decimal = _dec_from_pct(st.session_state["wacc_rf_pct"])
    elif live_rf_quote is not None:
        rf_decimal = live_rf_quote.rate

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

    wacc_inputs_obj: WaccInputs | None = None
    wacc_result = None
    if rf_decimal is None:
        st.warning("Brak stopy Rf (np. błąd FRED API) — przełącz na ręczną stopę Rf lub uzupełnij klucz FRED_API_KEY.")
    else:
        try:
            wacc_inputs_obj = WaccInputs(**wacc_kwargs)
            wacc_result = calculate_wacc(report, wacc_inputs_obj)
        except (WaccCalculationError, ValueError) as exc:
            st.error(f"Błąd kalkulacji WACC: {exc}")

    # --- DCF Assumptions Controls ---
    with col_dcf_ctrl:
        h_left, h_right = st.columns([2, 1])
        with h_left:
            st.markdown("#### 2. Założenia projekcji 5-letniej FCFF/DCF")
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
                st.number_input("Mnożnik wyjścia EV/EBITDA", min_value=1.0, max_value=60.0, step=0.5, format="%.1f", key="dcf_exit_mult")

        t_5y, t_sbc = st.columns(2)
        with t_5y:
            edit_5y_mode = st.toggle(
                "Zaawansowana edycja rok po roku (5Y)",
                value=False,
                key="dcf_edit_mode_5y",
            )
        with t_sbc:
            add_back_sbc = st.toggle(
                "Dodaj SBC z powrotem do FCFF (wariant księgowego CFO)",
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

        assumptions: DcfAssumptions | None = None
        assumptions_error: str | None = None

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
                # Keep 5Y DataFrame synchronized when editing in scalar mode
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

    if wacc_result is not None:
        st.divider()
        w1, w2, w3, w4, w5 = st.columns(5)
        w1.metric("WACC", f"{wacc_result.wacc * PERCENT_SCALE:.2f}%")
        w2.metric("Koszt kapitału wł. (Ke)", f"{wacc_result.cost_of_equity * PERCENT_SCALE:.2f}%")
        w3.metric("Kd po podatku", f"{wacc_result.after_tax_cost_of_debt * PERCENT_SCALE:.2f}%")
        w4.metric("Waga E / (E+D)", f"{wacc_result.equity_weight * PERCENT_SCALE:.1f}%")
        w5.metric("Waga D / (E+D)", f"{wacc_result.debt_weight * PERCENT_SCALE:.1f}%")
        if wacc_result.notes:
            with st.expander("Notatki audytowe WACC"):
                for note in wacc_result.notes:
                    st.write(f"- {note}")

    if assumptions_error:
        st.error(assumptions_error)
        return

    if wacc_result is None or assumptions is None:
        return

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
    except DcfValuationError as exc:
        st.error(f"Nie można wyznaczyć wyceny DCF: {exc}")
        return

    st.divider()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Enterprise Value (EV)", f"${dcf_result.bridge.enterprise_value / BILLION:,.2f} mld")
    k2.metric("Implied Equity Value", f"${dcf_result.bridge.implied_equity_value / BILLION:,.2f} mld")
    k3.metric("Implikowana wartość / akcję", f"${dcf_result.bridge.implied_value_per_share:,.2f}")
    tv_share = dcf_result.terminal_value.tv_share_of_ev
    k4.metric(
        "Udział Terminal Value w EV",
        f"{tv_share * PERCENT_SCALE:.1f}%" if tv_share is not None else "N/A",
    )

    st.caption(
        "Powyższa implikowana wartość wynika wyłącznie z założeń wprowadzonych przez użytkownika "
        "i ma charakter edukacyjno-badawczy (brak rekomendacji Kup/Sprzedaj oraz ceny docelowej)."
    )

    if dcf_result.sbc_valuation_impact_ev != Decimal("0"):
        sbc_mode_label = (
            "Wariant CFO (add_back_sbc=True)"
            if dcf_result.add_back_sbc
            else "Wariant ekonomiczny (add_back_sbc=False — GAAP EBIT uwzględnia koszt SBC)"
        )
        st.info(
            f"**Wpływ traktowania SBC ({sbc_mode_label}):** "
            f"dodanie SBC do FCFF podnosi wycenę o **+${dcf_result.sbc_valuation_impact_ev / BILLION:,.2f} mld EV** "
            f"(**+${dcf_result.sbc_valuation_impact_per_share:,.2f} / akcję**)."
        )

    assert wacc_inputs_obj is not None
    metrics_for_export = compute_historical_metrics(report, include_leases=include_leases)
    multiples_cmp = compute_market_multiples(
        report,
        include_leases=include_leases,
        share_price=wacc_inputs_obj.share_price,
        market_cap_override=wacc_inputs_obj.market_cap_override,
        dcf_result=dcf_result,
    )

    c_mult_hdr, c_xl_btn = st.columns([2.2, 1])
    with c_mult_hdr:
        st.markdown(f"#### Konfrontacja z bieżącymi mnożnikami rynkowymi (FY{multiples_cmp.fiscal_year} vs DCF)")
    with c_xl_btn:
        xlsx_bytes = export_dcf_workbook_bytes(
            report=report,
            metrics_report=metrics_for_export,
            wacc_inputs=wacc_inputs_obj,
            wacc_result=wacc_result,
            assumptions=assumptions,
            dcf_result=dcf_result,
            sensitivity=sensitivity,
            multiples=multiples_cmp,
        )
        st.download_button(
            label="Eksportuj model do Excela (.xlsx)",
            data=xlsx_bytes,
            file_name=f"{report.ticker}_DCF_Audit_FY{base_fy}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            key="download_dcf_excel",
        )

    if multiples_cmp.current_market_cap is None:
        st.caption(
            "Aktywny tryb kapitału księgowego (`book_equity_fallback`) bez ceny akcji / Market Cap — "
            "wyświetlane są wyłącznie mnożniki implikowane z wyceny DCF."
        )
    st.dataframe(
        build_multiples_comparison_dataframe(multiples_cmp),
        width="stretch",
        hide_index=True,
    )

    if dcf_result.bridge.equity_floored_at_zero:
        st.warning(
            f"Surowe Equity Value ({dcf_result.bridge.raw_implied_equity_value / BILLION:,.2f} mld USD) "
            "jest ujemne — wartość dla akcjonariuszy ograniczono do $0.00."
        )

    for note in dcf_result.notes:
        if "Warning:" in note or "(> 80%)" in note:
            st.warning(note)

    c_wf, c_bridge_tbl = st.columns([1.4, 1])
    with c_wf:
        st.plotly_chart(create_ev_bridge_waterfall_chart(dcf_result), width="stretch")
    with c_bridge_tbl:
        st.markdown("#### Most EV → Implied Equity Value")
        st.dataframe(
            build_ev_bridge_rows(dcf_result),
            width="stretch",
            hide_index=True,
            column_config={"Wartość ($ mld)": st.column_config.NumberColumn(format="%.2f")},
        )
        st.caption(
            f"Liczba akcji ({dcf_result.bridge.shares_source}): "
            f"{dcf_result.bridge.diluted_shares_used / Decimal('1000000'):,.2f} mln | "
            f"Baza NWC: {dcf_result.initial_nwc_source}"
        )

    st.markdown("#### Prognoza przepływów FCFF (5Y)")
    st.dataframe(
        build_dcf_projections_dataframe(dcf_result),
        width="stretch",
        hide_index=True,
        column_config={
            col: st.column_config.NumberColumn(format="%.2f")
            for col in (
                "Przychody ($ mld)",
                "EBIT ($ mld)",
                "NOPAT ($ mld)",
                "D&A ($ mld)",
                "CapEx ($ mld)",
                "ΔNWC ($ mld)",
                "FCFF ($ mld)",
                "PV FCFF ($ mld)",
            )
        },
    )

    c_heat, c_ff = st.columns(2)
    with c_heat:
        st.plotly_chart(create_sensitivity_heatmap(sensitivity), width="stretch")
        with st.expander("Tabela macierzy wrażliwości (USD / akcję)"):
            st.dataframe(
                build_sensitivity_dataframe(sensitivity),
                width="stretch",
            )
    with c_ff:
        st.plotly_chart(create_valuation_football_field_chart(dcf_result, sensitivity), width="stretch")


# ---------------------------------------------------------------------------
# Tab 3: Qualitative 10-K Audit (AI)
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

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Spółka (10-K)", f"{report.ticker}")
    m2.metric("Model wykonujący", outcome.executed_model)
    m3.metric("verified_quote_ratio", f"{report.verified_quote_ratio * PERCENT_SCALE:.1f}%")
    m4.metric("Zweryfikowane cytaty", f"{report.verified_claims} / {report.total_claims}")

    truncated_sections = [
        sec for sec, is_trunc in report.section_truncation_status.items() if is_trunc
    ]
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
        with st.expander(f"Ostrzeżenia audytu jakościowego ({len(report.warnings)})"):
            for warn in report.warnings:
                st.write(f"- {warn}")

    st.markdown("#### Podsumowanie wykonawcze (Executive Summary)")
    st.info(report.executive_summary)

    st.markdown("#### Ryzyka (`Item 1A` / `Item 7`)")
    if not report.risks:
        st.caption("Brak wyekstrahowanych ryzyk.")
    for item in report.risks:
        with st.container(border=True):
            top_l, top_r = st.columns([3, 1])
            with top_l:
                st.markdown(f"**{item.title}** · `{item.section}` · Kategoria: `{item.category}` · Istotność: `{item.materiality}`")
            with top_r:
                _render_quote_badge(item.quote_verified)
            st.write(item.summary)
            st.markdown(f"> *„{item.verbatim_quote}”*")
            if item.verification_note:
                st.caption(f"Audyt cytatu: {item.verification_note}")

    st.markdown("#### Motory wzrostu i komentarz zarządu")
    if not report.growth_drivers:
        st.caption("Brak wyekstrahowanych motorów wzrostu.")
    for item in report.growth_drivers:
        with st.container(border=True):
            top_l, top_r = st.columns([3, 1])
            with top_l:
                st.markdown(f"**{item.title}** · `{item.section}` · Ton zarządu: `{item.management_tone}`")
            with top_r:
                _render_quote_badge(item.quote_verified)
            st.write(item.summary)
            st.markdown(f"> *„{item.verbatim_quote}”*")
            if item.verification_note:
                st.caption(f"Audyt cytatu: {item.verification_note}")

    st.markdown("#### Potencjalne czerwone flagi (Red Flags)")
    if not report.red_flags:
        st.caption("Brak wyekstrahowanych czerwonych flag.")
    for item in report.red_flags:
        with st.container(border=True):
            top_l, top_r = st.columns([3, 1])
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
    st.subheader("Audyt jakościowy 10-K (AI + deterministyczny weryfikator cytatów)")

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
    mode, ticker, include_leases = _render_sidebar()

    st.title("SEC DCF Audit Engine — Portfolio Dashboard")

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

    tab1, tab2, tab3 = st.tabs(
        [
            "1. Sprawozdania i wskaźniki (5Y)",
            "2. WACC i wycena FCFF/DCF",
            "3. Audyt jakościowy 10-K (AI)",
        ]
    )

    with tab1:
        _render_tab_financials(report, include_leases)
    with tab2:
        _render_tab_valuation(mode, report, include_leases)
    with tab3:
        _render_tab_qualitative(mode, report.ticker)


if __name__ == "__main__":
    main()
