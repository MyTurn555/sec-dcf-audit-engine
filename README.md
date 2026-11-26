# SEC DCF Audit Engine — Moduł 1: Pobieranie i Walidacja 10-K

Deterministyczny silnik pobierania, normalizacji XBRL i audytu księgowego rocznych sprawozdań finansowych (`10-K` / `10-K/A`) z SEC EDGAR dla spółek niefinansowych.

---

## 1. Cel i architektura Modułu 1

Moduł 1 (`sec_dcf`) stanowi deterministyczną warstwę danych wejściowych pod przyszły model wyceny DCF/WACC:

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

## 2. Instalacja i uruchomienie testów (offline)

Projekt wymaga **Pythona 3.11+**.

```powershell
# 1. Utworzenie i aktywacja środowiska wirtualnego (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Instalacja paczki wraz z zależnościami deweloperskimi
pip install -e ".[dev]"

# 3. Uruchomienie pełnego zestawu testów jednostkowych i integracyjnych (100% offline)
pytest -v
```

> Testy korzystają z lokalnych próbek w `tests/fixtures/` (`aapl_companyfacts.json`, `msft_companyfacts.json`) i blokują wywołania sieciowe do SEC EDGAR. Konfiguracja `pythonpath = ["src"]` w `pyproject.toml` umożliwia uruchamianie `.\.venv\Scripts\python.exe -m pytest -v` również bezpośrednio bez wcześniejszej instalacji edytowalnej.

---

## 3. Konfiguracja `.env`

Przed wywołaniem zapytań do SEC EDGAR utwórz plik `.env` na bazie szablonu `.env.example` i uzupełnij zmienną `EDGAR_IDENTITY` zgodnie z wymogiem SEC (nazwa aplikacji oraz adres kontaktowy):

```dotenv
EDGAR_IDENTITY="TwojaNazwaAplikacji kontakt@twojadomena.com"
FRED_API_KEY=
GEMINI_API_KEY=
```

Plik `.env` jest ignorowany przez Git i nie powinien być nigdy commitowany do repozytorium.

---

## 4. Klauzula edukacyjna i brak doradztwa inwestycyjnego

**PL:** Projekt ma charakter wyłącznie edukacyjny, badawczy i inżynierski. Narzędzie oraz generowane przez nie dane i przedziały wartości nie stanowią rekomendacji inwestycyjnej, porady inwestycyjnej ani analizy inwestycyjnej w rozumieniu ustawy o obrocie instrumentami finansowymi (u.o.i.f.) oraz rozporządzenia MAR (UE nr 596/2014). Wszelkie założenia i decyzje analityczne należą wyłącznie do użytkownika.

**EN:** This project is strictly for educational, research, and engineering purposes only. Neither the software nor any outputs or implied valuation ranges produced by it constitute investment advice, a financial promotion, or an investment recommendation within the meaning of the Polish Act on Trading in Financial Instruments (u.o.i.f.) or Regulation (EU) No 596/2014 (MAR). All assumptions and analytical decisions remain solely the responsibility of the user.
