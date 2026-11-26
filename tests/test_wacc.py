"""Offline unit tests for FRED client and deterministic CAPM/WACC engine."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from urllib.error import HTTPError

import pytest

from sec_dcf.analysis.fred_client import FredClient
from sec_dcf.analysis.wacc import calculate_wacc
from sec_dcf.exceptions import FredApiError, WaccCalculationError
from sec_dcf.models.analysis import WaccInputs
from sec_dcf.models.financials import AnnualFinancialPeriod, CompanyFinancialsReport


@pytest.fixture(autouse=True)
def block_network_calls(monkeypatch):
    """Ensure FRED/WACC tests never attempt real HTTP calls."""

    def _forbid(*args, **kwargs):
        raise AssertionError("Network call attempted during offline WACC/FRED tests!")

    monkeypatch.setattr("sec_dcf.analysis.fred_client.urlopen", _forbid)


def _sample_report(periods: list[AnnualFinancialPeriod] | None = None) -> CompanyFinancialsReport:
    if periods is None:
        periods = [
            AnnualFinancialPeriod(
                fiscal_year=2024,
                period_start=date(2024, 1, 1),
                period_end=date(2024, 12, 31),
                revenue=Decimal("900"),
                ebit=Decimal("180"),
                da=Decimal("40"),
                interest_expense=Decimal("12"),
                pretax_income=Decimal("168"),
                tax_expense=Decimal("42"),  # 25%
                net_income=Decimal("126"),
                diluted_shares=Decimal("100"),
                cash_and_equivalents=Decimal("80"),
                total_assets=Decimal("900"),
                short_term_debt=Decimal("20"),
                long_term_debt=Decimal("180"),  # financial_debt = 200
                current_lease_liabilities=Decimal("10"),
                noncurrent_lease_liabilities=Decimal("40"),  # leases = 50
                total_liabilities=Decimal("400"),
                stockholders_equity=Decimal("500"),
                cfo=Decimal("190"),
                capex=Decimal("50"),
            ),
            AnnualFinancialPeriod(
                fiscal_year=2025,
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                revenue=Decimal("1000"),
                ebit=Decimal("200"),
                da=Decimal("50"),
                interest_expense=Decimal("12"),
                pretax_income=Decimal("188"),
                tax_expense=Decimal("37.6"),  # 20%
                net_income=Decimal("150.4"),
                diluted_shares=Decimal("100"),
                cash_and_equivalents=Decimal("100"),
                total_assets=Decimal("1000"),
                short_term_debt=Decimal("20"),
                long_term_debt=Decimal("180"),  # financial_debt = 200 (avg = 200 -> Kd = 12/200 = 6%)
                current_lease_liabilities=Decimal("10"),
                noncurrent_lease_liabilities=Decimal("40"),  # leases = 50 (total_debt_incl_leases = 250)
                total_liabilities=Decimal("450"),
                stockholders_equity=Decimal("550"),
                cfo=Decimal("210"),
                capex=Decimal("60"),
            ),
        ]
    return CompanyFinancialsReport(
        ticker="TEST",
        cik="0000999999",
        company_name="Test Corp",
        periods=periods,
    )


def test_fred_client_skips_missing_dot_and_passes_timeout():
    captured: dict[str, object] = {}

    def fake_get(url: str, timeout: float) -> str:
        captured["url"] = url
        captured["timeout"] = timeout
        return json.dumps(
            {
                "observations": [
                    {"date": "2026-11-26", "value": "."},
                    {"date": "2026-11-25", "value": "4.25"},
                ]
            }
        )

    client = FredClient(api_key="secret-fred-key", timeout=7.5, http_get=fake_get)
    quote = client.get_latest_rate("dgs10")
    assert captured["timeout"] == 7.5
    assert "series_id=DGS10" in str(captured["url"])
    assert quote.series_id == "DGS10"
    assert quote.rate == Decimal("0.0425")
    assert quote.observation_date == date(2026, 11, 25)
    assert quote.source == "fred"


def test_fred_client_masks_api_key_in_errors(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr("sec_dcf.analysis.fred_client.load_dotenv", lambda: False)
    with pytest.raises(FredApiError, match="FRED_API_KEY is missing"):
        FredClient()

    secret = "SUPER_SECRET_KEY_123"

    def failing_http(url: str, timeout: float) -> str:
        raise HTTPError(url=url, code=500, msg=f"bad request with {secret}", hdrs=None, fp=None)  # type: ignore[arg-type]

    client = FredClient(api_key=secret, http_get=failing_http)
    with pytest.raises(FredApiError) as exc_info:
        client.get_latest_rate("DGS10")
    assert secret not in str(exc_info.value)


def test_fred_client_rejects_empty_observations_and_invalid_json():
    client_empty = FredClient(
        api_key="abc",
        http_get=lambda _u, _t: json.dumps({"observations": [{"date": "2026-11-26", "value": "."}]}),
    )
    with pytest.raises(FredApiError, match="no valid numeric observations"):
        client_empty.get_latest_rate("DGS10")

    client_bad_json = FredClient(api_key="abc", http_get=lambda _u, _t: "{not-json")
    with pytest.raises(FredApiError, match="invalid JSON"):
        client_bad_json.get_latest_rate("DGS10")


def test_wacc_calculation_exact_and_include_leases_kd_consistency():
    report = _sample_report()

    # Case 1: include_leases=False, share_price=8 -> E = 800, D = 200 -> We=0.8, Wd=0.2
    # Rf=0.04, beta=1.2, ERP=0.05 -> Ke=0.10
    # Implied Kd = 12 / 200 = 0.06; tax_rate omitted -> latest_nopat_tax_rate = 0.20
    # After-tax Kd = 0.06 * 0.8 = 0.048
    # WACC = 0.8 * 0.10 + 0.2 * 0.048 = 0.0896
    res_excl = calculate_wacc(
        report,
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.2"),
            equity_risk_premium=Decimal("0.05"),
            share_price=Decimal("8"),
            include_leases=False,
        ),
    )
    assert res_excl.cost_of_equity == Decimal("0.10")
    assert res_excl.pretax_cost_of_debt == Decimal("0.06")
    assert res_excl.cost_of_debt_source == "implied_from_financial_debt"
    assert res_excl.tax_rate == Decimal("0.2")
    assert res_excl.tax_rate_source == "latest_nopat_tax_rate"
    assert res_excl.equity_weight == Decimal("0.8")
    assert res_excl.debt_weight == Decimal("0.2")
    assert res_excl.wacc == Decimal("0.0896")

    # Case 2: include_leases=True -> debt weight = 250, E = 750 (share_price=7.5) -> Wd=0.25, We=0.75
    # Crucially, implied Kd STILL uses financial_debt (200), NOT 250 -> Kd remains 0.06!
    res_incl = calculate_wacc(
        report,
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.2"),
            equity_risk_premium=Decimal("0.05"),
            share_price=Decimal("7.5"),
            include_leases=True,
        ),
    )
    assert res_incl.debt_value_for_weights == Decimal("250")
    assert res_incl.pretax_cost_of_debt == Decimal("0.06")
    assert res_incl.cost_of_debt_source == "implied_from_financial_debt"
    assert res_incl.equity_weight == Decimal("0.75")
    assert res_incl.debt_weight == Decimal("0.25")
    assert res_incl.wacc == Decimal("0.75") * Decimal("0.10") + Decimal("0.25") * Decimal("0.048")


def test_wacc_kd_sanity_check_fallback_and_omitted_tax_rate_loss_fallback():
    # interest_expense = 2 on financial_debt = 200 -> implied Kd = 1% < Rf (4%) -> fallback Rf + spread
    # Pretax loss -> omitted tax_rate uses safe nopat_tax_rate fallback (21%), not raw negative ETR
    p_loss = _sample_report().periods[-1].model_copy(
        update={
            "interest_expense": Decimal("2"),
            "pretax_income": Decimal("-50"),
            "tax_expense": Decimal("-10"),
        }
    )
    res = calculate_wacc(
        _sample_report([p_loss]),
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.0"),
            equity_risk_premium=Decimal("0.05"),
            market_cap_override=Decimal("800"),
            default_credit_spread=Decimal("0.02"),
        ),
    )
    assert res.pretax_cost_of_debt == Decimal("0.06")
    assert res.cost_of_debt_source == "rf_plus_spread"
    assert res.tax_rate == Decimal("0.21")
    assert res.tax_rate_source == "latest_nopat_tax_rate"
    assert any("outside plausible range" in n for n in res.notes)

    # Also test Kd > 25% sanity bound triggers fallback
    p_high_kd = _sample_report().periods[-1].model_copy(
        update={"interest_expense": Decimal("80")}  # 80/200 = 40% > 25%
    )
    res_high = calculate_wacc(
        _sample_report([p_high_kd]),
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.0"),
            equity_risk_premium=Decimal("0.05"),
            market_cap_override=Decimal("800"),
            default_credit_spread=Decimal("0.02"),
        ),
    )
    assert res_high.cost_of_debt_source == "rf_plus_spread"
    assert res_high.pretax_cost_of_debt == Decimal("0.06")


def test_wacc_zero_debt_and_book_equity_fallback_errors():
    p_no_debt = _sample_report().periods[-1].model_copy(
        update={
            "short_term_debt": Decimal("0"),
            "long_term_debt": Decimal("0"),
            "current_lease_liabilities": Decimal("0"),
            "noncurrent_lease_liabilities": Decimal("0"),
            "interest_expense": Decimal("0"),
        }
    )
    report_no_debt = _sample_report([p_no_debt])

    # Missing market cap / share price without allow_book_equity_fallback -> error
    with pytest.raises(WaccCalculationError, match="requires market value of equity"):
        calculate_wacc(
            report_no_debt,
            WaccInputs(
                risk_free_rate=Decimal("0.04"),
                beta=Decimal("1.0"),
                equity_risk_premium=Decimal("0.05"),
            ),
        )

    # With allow_book_equity_fallback=True and zero debt -> WACC = Ke
    res = calculate_wacc(
        report_no_debt,
        WaccInputs(
            risk_free_rate=Decimal("0.04"),
            beta=Decimal("1.0"),
            equity_risk_premium=Decimal("0.05"),
            allow_book_equity_fallback=True,
        ),
    )
    assert res.cost_of_debt_source == "zero_debt"
    assert res.equity_weight == Decimal("1")
    assert res.wacc == Decimal("0.09")
