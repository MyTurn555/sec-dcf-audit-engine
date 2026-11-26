"""Offline Streamlit AppTest smoke tests verifying Offline Demo mode."""

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
    at = AppTest.from_file(str(APP_PATH), default_timeout=20)
    at.run()

    assert not at.exception
    assert len(at.tabs) == 3

    # Verify persistent legal disclaimer is rendered in sidebar
    sidebar_info_texts = " ".join(box.value for box in at.sidebar.info)
    assert "u.o.i.f." in sidebar_info_texts
    assert "MAR" in sidebar_info_texts

    # Switch demo ticker AAPL -> MSFT and verify automatic valuation context reset
    at.sidebar.selectbox(key="demo_ticker").select("MSFT").run()
    assert not at.exception
    assert at.session_state["active_valuation_context"] == ("Offline Demo", "MSFT")
    assert at.session_state["wacc_share_price"] == 425.0

    # Toggle include_leases off and verify clean rerun
    at.sidebar.toggle(key="include_leases_toggle").set_value(False).run()
    assert not at.exception

    # Switch Tab 2 to 5Y data_editor mode and verify clean rerun
    at.toggle(key="dcf_edit_mode_5y").set_value(True).run()
    assert not at.exception
