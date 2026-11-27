"""Offline Streamlit AppTest smoke tests verifying Offline Demo mode and 4-tab layout."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py"


@pytest.fixture(autouse=True)
def block_all_network_calls(monkeypatch):
    """Ensure AppTest runs 100% offline with zero network calls."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Outbound network call attempted during offline AppTest!")

    monkeypatch.setattr("edgar.Company", _forbid)
    monkeypatch.setattr("sec_dcf.ingestion.edgar_client.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.extractor.Company", _forbid)
    monkeypatch.setattr("sec_dcf.qualitative.gemini_client.genai.Client", _forbid)
    monkeypatch.setattr("urllib.request.urlopen", _forbid)
    monkeypatch.setattr("socket.create_connection", _forbid)


def test_streamlit_app_offline_demo_smoke_and_ticker_switch():
    at = AppTest.from_file(str(APP_PATH), default_timeout=25)
    at.run()

    assert not at.exception

    tab_labels = [t.label for t in at.tabs]
    for expected_tab in (
        "1. Fundamenty & Audyt XBRL (5Y)",
        "2. Model WACC & DCF",
        "3. Wrażliwość & Monte Carlo",
        "4. Audyt Jakościowy 10-K (AI)",
    ):
        assert expected_tab in tab_labels

    # Verify Tab 4 qualitative sub-tabs are also rendered
    assert any(lbl.startswith("Ryzyka — Item 1A") for lbl in tab_labels)
    assert any(lbl.startswith("Motory wzrostu — Item 7") for lbl in tab_labels)
    assert any(lbl.startswith("Czerwone flagi") for lbl in tab_labels)

    # Verify persistent legal disclaimer is rendered in sidebar
    sidebar_info_texts = " ".join(box.value for box in at.sidebar.info)
    assert "u.o.i.f." in sidebar_info_texts
    assert "MAR" in sidebar_info_texts

    # Verify BYOK key inputs are hidden in Offline Demo mode
    sidebar_input_keys = {inp.key for inp in at.sidebar.text_input}
    assert "session_gemini_api_key" not in sidebar_input_keys

    # Switch demo ticker AAPL -> MSFT -> AMZN and verify automatic valuation context reset
    at.sidebar.selectbox(key="demo_ticker").select("MSFT").run()
    assert not at.exception
    assert at.session_state["active_valuation_context"] == ("Offline Demo", "MSFT")
    assert at.session_state["wacc_share_price"] == 425.0

    at.sidebar.selectbox(key="demo_ticker").select("AMZN").run()
    assert not at.exception
    assert at.session_state["active_valuation_context"] == ("Offline Demo", "AMZN")
    assert at.session_state["wacc_share_price"] == 210.0

    # Toggle add_back_sbc and verify clean rerun
    at.toggle(key="dcf_add_back_sbc").set_value(True).run()
    assert not at.exception

    # Toggle include_leases off and verify clean rerun
    at.sidebar.toggle(key="include_leases_toggle").set_value(False).run()
    assert not at.exception

    # Switch Tab 2 to 5Y data_editor mode and verify clean rerun
    at.toggle(key="dcf_edit_mode_5y").set_value(True).run()
    assert not at.exception

    # Force WACC <= g valuation error and verify graceful warning in Tab 2 & Tab 3 (no NoneType crash)
    at.number_input(key="dcf_terminal_g_pct").set_value(8.0).run()
    at.number_input(key="wacc_rf_pct").set_value(0.5).run()
    at.number_input(key="wacc_erp_pct").set_value(1.0).run()
    assert not at.exception
    assert len(at.warning) >= 2
