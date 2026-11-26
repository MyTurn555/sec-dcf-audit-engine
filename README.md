# SEC DCF Audit Engine — Moduł 1 & Moduł 2

Deterministyczny silnik pobierania, normalizacji XBRL, audytu księgowego (`10-K` / `10-K/A`), analizy wskaźnikowej, wyznaczania WACC oraz 5-letniej wyceny FCFF/DCF dla spółek niefinansowych.

---

## 1. Architektura Modułu 1: Pobieranie i Walidacja 10-K

Moduł 1 (`sec_dcf.ingestion`, `sec_dcf.validation`) stanowi deterministyczną warstwę danych wejściowych pod model wyceny:

- **100% deterministyki w Pythonie (`Decimal`)**: wszystkie kwoty finansowe (w pełnych USD) i wskaźniki są przetwarzane bez użycia arytmetyki zmiennoprzecinkowej i bez udziału modeli LLM.
- **Źródło danych**: biblioteka `edgartools` (`Company` / `EntityFacts`) z wymaganym identyfikatorem `EDGAR_IDENTITY` oraz wbudowanym ogranicznikiem zapytań (`<= 8 req/s`, poniżej limitu SEC 10 req/s).
- **Zakres podmiotowy**: wyłącznie spółki operacyjne niefinansowe — instytucje finansowe (`company.is_financial_institution()` oraz kody **SIC 6000–6999**) są blokowane wyjątkiem `UnsupportedCompanyTypeError`.
- **Mapowanie XBRL i pełny ślad audytowy (`audit_trail`)**:
  - 5 ostatnich lat obrotowych (`10-K` / `10-K/A`) z deduplikacją okresów po najnowszej dacie złożenia (`filing_date`) i ignorowaniem raportów kwartalnych (`10-Q`).
  - Kanoniczne modele Pydantic v2 (`AnnualFinancialPeriod`, `CompanyFinancialsReport`) zawierające m.in.: `revenue`, `ebit`, `da`, `net_income`, `cash_and_equivalents`, `short_term_investments`, `short_term_debt`, `long_term_debt`, `current_lease_liabilities`, `noncurrent_lease_liabilities`, `total_liabilities`, `stockholders_equity`, `cfo`, `capex` (znormalizowany do wartości `>= 0`), `diluted_shares` (`duration`), `ending_shares` (`instant`) oraz pola wyliczane (`total_cash`, `financial_debt`, `lease_liabilities`, `total_debt_incl_leases`, `non_cash_nwc`, `fcf_simple`).
  - Każda zmapowana pozycja posiada obiekt `LineItemAudit` (`concept`, `raw_value`, `source_kind`, `period_end`, `filing_date`, `accession`, `note`).
- **Automatyczna walidacja księgowa (`validate_financials`)**:
  - Nietautologiczny test równania bilansowego (tolerancja `max($1M, 0.1% * Total Assets)`),
  - Test spójności RZiS (`pretax_income - tax_expense ≈ net_income`),
  - Kontrola unikalności i ciągłości okresów, znaków pozycji oraz relacji strukturalnych.

---

## 2. Architektura Modułu 2: Wskaźniki, WACC i Wycena FCFF/DCF

Moduł 2 (`sec_dcf.analysis`, `sec_dcf.models.analysis`) operuje wyłącznie na zwalidowanych modelach Pydantic (`Decimal`) z Modułu 1:

- **`ratios.py` (`compute_historical_metrics`)**:
  - Marże (`ebit_margin`, `ebitda_margin`, `net_margin`), bezpieczna stopa podatkowa do NOPAT (ograniczona do `[0%, 50%]`, z fallbackiem `21%` przy stracie przed opodatkowaniem lub braku danych), `invested_capital` (z i bez leasingów), `ROIC` na bazie średniego kapitału zainwestowanego (`None` dla `IC <= 0`), konwersja gotówki (`fcf_simple / net_income`, `None` przy stracie netto), kapitałochłonność (`capex_to_revenue`, `da_to_revenue`, `non_cash_nwc_to_revenue`), `Net Debt` (bez i z leasingami), `Net Debt / EBITDA` (dopuszczające ujemne wartości przy pozycji gotówki netto, `None` przy `EBITDA <= 0`) oraz `interest_coverage`.
- **`fred_client.py` (`FredClient`, `fetch_risk_free_rate`)**:
  - Lekki klient darmowego FRED API (oparty o standardową bibliotekę `urllib.request` z jawnym timeoutem `10s`) pobierający najnowszą ważną obserwację serii `DGS10` (rentowność 10-letnich obligacji skarbowych USA), z pomijaniem braków danych (`"."`) oraz maskowaniem klucza `FRED_API_KEY` w komunikatach wyjątków `FredApiError`.
- **`wacc.py` (`calculate_wacc`)**:
  - Koszt kapitału własnego w modelu CAPM (`Rf + beta * ERP`) z możliwością podania ręcznego `risk_free_rate` (tryb w 100% offline), wagi rynkowe kapitału (`market_cap_override` lub `share_price * shares`, opcjonalny `allow_book_equity_fallback`) oraz koszt długu przed i po podatku (z kontrolą wiarygodności `[Rf, 25%]` i fallbackiem `Rf + default_credit_spread`).
- **`dcf.py` (`suggest_dcf_assumptions`, `run_dcf_valuation`, `build_sensitivity_matrix`)**:
  - Model 5-letnich założeń projekcji (`DcfAssumptions`, obsługujący stałe wartości `Decimal` lub 5-elementowe ścieżki rok-po-roku) wraz z funkcją wyznaczającą punkt startowy z median historycznych.
  - Projekcja $FCFF_t = EBIT_t \cdot (1 - t) + D\&A_t - CapEx_t - \Delta NWC_t$ z obsługą dyskontowania `end_of_year` oraz `mid_year` ($t - 0.5$ dla $FCFF_t$ oraz $5.0$ dla wartości rezydualnej).
  - Wartość rezydualna metodą Gordona (twardy wyjątek domenowy `DcfValuationError`, gdy $WACC \le g$, oraz ostrzeżenie przy $FCFF_5 \le 0$), opcjonalny cross-check mnożnikiem wyjścia `EV/EBITDA`, most `Enterprise Value -> Equity Value -> Implied Value per Share` oraz dwuwymiarowa macierz wrażliwości `WACC × terminal_growth_rate`.
- **Spójna obsługa leasingów ASC 842 (`include_leases: bool`)**:
  - Flaga `include_leases` musi być spójna między `WaccResult` a `DcfAssumptions` (niezgodność zgłasza `DcfValuationError`).
  - Przy `include_leases=True` zobowiązania leasingowe są włączane konsekwentnie do `Invested Capital`, wag długu w WACC oraz odjęć w moście `EV -> Equity Value`. Zgodnie z US GAAP (ASC 842) koszt leasingu operacyjnego nie trafia do `interest_expense`, dlatego implikowany koszt długu `Kd` (bez override'u) wyliczany jest zawsze od oprocentowanego `financial_debt`, a `EBIT`/`EBITDA` nie są korygowane o implikowane odsetki leasingowe.

---

## 3. Przykład użycia w Pythonie

```python
from decimal import Decimal
from sec_dcf import (
    WaccInputs,
    build_sensitivity_matrix,
    calculate_wacc,
    compute_historical_metrics,
    fetch_company_financials,
    run_dcf_valuation,
    suggest_dcf_assumptions,
)

# 1. Pobranie i walidacja 5 lat 10-K z SEC EDGAR (wymaga EDGAR_IDENTITY w .env)
report = fetch_company_financials("MSFT", years=5, strict=True)

# 2. Panel wskaźników historycznych (spójnie z flagą include_leases)
metrics = compute_historical_metrics(report, include_leases=True)

# 3. Wyliczenie WACC (risk_free_rate=None pobiera DGS10 z FRED_API_KEY; można też podać ręcznie)
wacc_result = calculate_wacc(
    report,
    WaccInputs(
        risk_free_rate=Decimal("0.0425"),  # lub None -> FRED DGS10
        beta=Decimal("1.10"),
        equity_risk_premium=Decimal("0.045"),
        share_price=Decimal("450"),
        include_leases=True,
    ),
)

# 4. Sugestia założeń z historii + 5-letnia wycena FCFF/DCF (mid_year)
assumptions = suggest_dcf_assumptions(
    report,
    include_leases=True,
    discounting_convention="mid_year",
)
dcf_result = run_dcf_valuation(report, assumptions, wacc_result)

# 5. Dwuwymiarowa macierz wrażliwości WACC x terminal_growth_rate
sensitivity = build_sensitivity_matrix(
    report,
    assumptions,
    wacc_values=[
        wacc_result.wacc - Decimal("0.005"),
        wacc_result.wacc,
        wacc_result.wacc + Decimal("0.005"),
    ],
    terminal_growth_values=[Decimal("0.020"), Decimal("0.025"), Decimal("0.030")],
    base_wacc=wacc_result,
)

print("WACC:", wacc_result.wacc)
print("Implied Value/Share:", dcf_result.bridge.implied_value_per_share)
print("Range:", sensitivity.min_implied_value_per_share, "-", sensitivity.max_implied_value_per_share)
```

---

## 4. Instalacja i uruchomienie testów (100% offline)

Projekt wymaga **Pythona 3.11+**.

```powershell
# 1. Utworzenie i aktywacja środowiska wirtualnego (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Instalacja paczki wraz z zależnościami deweloperskimi
pip install -e ".[dev]"

# 3. Uruchomienie pełnego zestawu testów (43 testy jednostkowe i integracyjne — 100% offline)
pytest -v
```

> Wszystkie **43 testy `pytest`** (Moduł 1 i Moduł 2) działają w **100% offline** — korzystają z lokalnych próbek w `tests/fixtures/` (`aapl_companyfacts.json`, `msft_companyfacts.json`) oraz mockowanych transportów, blokując jakiekolwiek wywołania sieciowe do SEC EDGAR i FRED API. Konfiguracja `pythonpath = ["src"]` w `pyproject.toml` umożliwia uruchamianie `.\.venv\Scripts\python.exe -m pytest -v` również bezpośrednio bez wcześniejszej instalacji edytowalnej.

---

## 5. Konfiguracja `.env` i atrybucja danych FRED

Przed wywołaniem zapytań sieciowych do SEC EDGAR lub FRED API utwórz plik `.env` na bazie szablonu `.env.example` i uzupełnij wymagane zmienne:

```dotenv
EDGAR_IDENTITY="TwojaNazwaAplikacji kontakt@twojadomena.com"
FRED_API_KEY=twoj_klucz_fred_api
GEMINI_API_KEY=
```

- `EDGAR_IDENTITY` jest wymagane przez SEC EDGAR przy wywołaniach `fetch_company_financials`.
- `FRED_API_KEY` (darmowy klucz z serwisu FRED) jest wymagany przez `FredClient` przy pobieraniu bieżącej stopy `DGS10`. W trybie offline lub w testach można przekazać `risk_free_rate` ręcznie w `WaccInputs`.
- Plik `.env` jest ignorowany przez Git i nie powinien być nigdy commitowany do repozytorium.

**Atrybucja danych FRED:** Produkt korzysta z FRED® API (Federal Reserve Bank of St. Louis) wyłącznie w celach badawczych i edukacyjnych, ale nie jest wspierany ani certyfikowany przez Federal Reserve Bank of St. Louis. Korzystanie z danych FRED podlega warunkom [FRED® API Terms of Use](https://fred.stlouisfed.org/docs/api/terms_of_use.html).

---

## 6. Klauzula edukacyjna i brak doradztwa inwestycyjnego

**PL:** Projekt ma charakter wyłącznie edukacyjny, badawczy i inżynierski. Narzędzie oraz generowane przez nie dane i przedziały wartości nie stanowią rekomendacji inwestycyjnej, porady inwestycyjnej ani analizy inwestycyjnej w rozumieniu ustawy o obrocie instrumentami finansowymi (u.o.i.f.) oraz rozporządzenia MAR (UE nr 596/2014). Wszelkie założenia i decyzje analityczne należą wyłącznie do użytkownika.

**EN:** This project is strictly for educational, research, and engineering purposes only. Neither the software nor any outputs or implied valuation ranges produced by it constitute investment advice, a financial promotion, or an investment recommendation within the meaning of the Polish Act on Trading in Financial Instruments (u.o.i.f.) or Regulation (EU) No 596/2014 (MAR). All assumptions and analytical decisions remain solely the responsibility of the user.
