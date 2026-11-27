# Założenia księgowe, definicje i ograniczenia modelu (`ASSUMPTIONS.md`)

Dokument opisuje deterministyczne definicje księgowe, hierarchię mapowania tagów XBRL, traktowanie leasingów (ASC 842) oraz wynagrodzeń w akcjach (SBC) stosowane w **SEC DCF Audit Engine**.

---

## 1. Zakres podmiotowy i źródło danych

- **Spółki niefinansowe US GAAP (`10-K` / `10-K/A`):** silnik obsługuje wyłącznie spółki operacyjne. Instytucje finansowe (`company.is_financial_institution()` oraz kody **SIC 6000–6999**) są blokowane wyjątkiem `UnsupportedCompanyTypeError`, ponieważ pojęcia długu, `EBIT` i kapitału obrotowego netto nie mają tam standardowej interpretacji FCFF.
- **Źródło XBRL:** biblioteka `edgartools` (`EntityFacts`) z zachowaniem limitu SEC (`<= 8 req/s`) i deduplikacją faktów wyłącznie z rocznych raportów `10-K` / `10-K/A` (bez `10-Q` i bez wymiarów segmentowych `dimensions`). Przy wielu raportach dla tego samego roku wygrywa najnowsza data złożenia (`filing_date`, w tym korekty `10-K/A` i dane porównawcze).

---

## 2. Hierarchia tagów XBRL i ochrona przed podwójnym liczeniem

Każda pozycja w `AnnualFinancialPeriod` posiada obiekt `LineItemAudit` w `audit_trail` (`tag_exact`, `tag_fallback`, `derived`, `derived_from_assets`, `zero_if_absent`, `missing`).

- **D&A (`da`):** najpierw tagi zagregowane (`DepreciationDepletionAndAmortization`, `DepreciationAndAmortization`, `DepreciationAmortizationAndAccretionNet`), następnie suma `Depreciation + AmortizationOfIntangibleAssets`, a w ostateczności samo `Depreciation`. Świadomie wykluczono `FinanceLeaseRightOfUseAssetAmortization`, aby nie dublować amortyzacji ujętej już w agregacie D&A.
- **Dług krótkoterminowy (`short_term_debt`):** najpierw agregaty (`DebtCurrent`, `ShortTermBorrowings`); dopiero przy ich braku sumowane są komponenty (`CommercialPaper + LongTermDebtCurrent`).
- **Dług długoterminowy (`long_term_debt`):** preferowany `LongTermDebtNoncurrent`. Przy użyciu `LongTermDebt` podejmowana jest próba odjęcia `LongTermDebtCurrent` (ostrzeżenie `AGGREGATE_DEBT_TAG_USED`). Przy `LongTermDebtAndCapitalLeaseObligations` silnik blokuje dodawanie osobnych zobowiązań z tytułu leasingu finansowego (`DEBT_INCLUDES_FINANCE_LEASES`).
- **Zobowiązania ogółem (`total_liabilities`):** nietautologiczna kolejność: (1) `Liabilities`, (2) `LiabilitiesCurrent + LiabilitiesNoncurrent`, (3) `total_assets - total_equity` wyłącznie jako ostatni fallback z oznaczeniem `derived_from_assets` i ostrzeżeniem `BALANCE_SHEET_IDENTITY_UNVERIFIED_DERIVED_LIABILITIES` (widoczne np. dla `AMZN`).
- **CapEx (`capex`):** akceptowane są wyłącznie realne wypływy gotówkowe (`PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsToAcquireProductiveAssets`), normalizowane do wartości nieujemnej (`>= 0`). Tag `CapitalExpendituresIncurredButNotYetPaid` jest celowo ignorowany.

---

## 3. Kluczowe definicje finansowe

- **`EBIT`:** `us-gaap:OperatingIncomeLoss` (z fallbackiem `pretax_income + interest_expense` i ostrzeżeniem `EBIT_DERIVED_FALLBACK`).
- **`EBITDA`:** `EBIT + D&A`.
- **Stopa podatkowa NOPAT (`nopat_tax_rate`):** wyliczana jako `tax_expense / pretax_income` i ograniczana do przedziału `[0%, 50%]` (`clamped`). Przy `pretax_income <= 0` lub braku danych stosowana jest bezpieczna stopa domyślna `21%` (`fallback_loss_or_missing`), aby jednorazowa strata podatkowa nie zniekształcała historycznego `NOPAT` i `ROIC`.
- **`Non-cash NWC`:**
  $$\text{Non-cash NWC} = (\text{Current Assets} - \text{Total Cash}) - (\text{Current Liabilities} - \text{Short-Term Debt} - \text{Current Lease Liabilities})$$
  gdzie $\text{Total Cash} = \text{Cash \& Equivalents} + \text{Short-Term Investments}$.
- **`Invested Capital` i `ROIC`:**
  - bez leasingów: $\text{Financial Debt} + \text{Total Equity (z NCI)} - \text{Total Cash}$,
  - z leasingami (`include_leases=True`): $\text{Financial Debt} + \text{Lease Liabilities} + \text{Total Equity (z NCI)} - \text{Total Cash}$,
  - `ROIC = NOPAT / Średni Invested Capital` (pierwszy dostępny rok korzysta z końcowego IC; gdy średni `IC <= 0`, `ROIC = None`).
- **`Simple FCF` vs. `True FCF (ex-SBC)`:**
  - `fcf_simple = CFO - CapEx` (konwencja księgowa oparta wprost o sprawozdanie z przepływów pieniężnych),
  - `fcf_ex_sbc = CFO - CapEx - SBC` (koryguje `CFO` o niepieniężny koszt wynagrodzeń akcyjnych).

---

## 4. Leasingi według ASC 842 (`include_leases`) i zobowiązania pozabilansowe

- **Spójność w całym łańcuchu wyceny:** przełącznik `include_leases` musi być identyczny w `compute_historical_metrics`, `calculate_wacc`, `run_dcf_valuation`, `compute_market_multiples` oraz w eksporcie Excela (niezgodność zgłasza `DcfValuationError`).
- **Koszt długu (`Kd`) i `EBIT` przy `include_leases=True`:** zgodnie z US GAAP (ASC 842) koszt leasingu operacyjnego jest ujmowany jako jednolity koszt operacyjny w `EBIT`, a nie w `interest_expense`. Dlatego:
  1. implikowany koszt długu `Kd` (bez ręcznego nadpisania) liczony jest zawsze wobec oprocentowanego `financial_debt` (a nie `total_debt_incl_leases`) z kontrolą wiarygodności `[Rf, 25%]`;
  2. `EBIT` i `EBITDA` nie są korygowane o implikowane odsetki od leasingów operacyjnych (świadome uproszczenie spójne z raportowaniem US GAAP).
- **Ograniczenie — leasingi nierozpoczęte (`leases not yet commenced`):** zobowiązania z tytułu umów leasingowych jeszcze nierozpoczętych (np. budowane centra danych lub magazyny, jak u spółek chmurowych/AI typu `AMZN`/`MSFT`) nie figurują w bilansie ani w tagach `OperatingLeaseLiability*`, lecz wyłącznie w notach / `Item 7`. Moduł 3 (`gemini_client.py`) posiada jawną instrukcję wyszukiwania tych zobowiązań i oznaczania ich w sekcji `red_flags`.

---

## 5. Traktowanie Stock-Based Compensation (SBC)

- **Tagi XBRL:** `us-gaap:ShareBasedCompensation`, fallback `us-gaap:AllocatedShareBasedCompensationExpense`, domyślnie `0` (`zero_if_absent`) z pełnym wpisem w `audit_trail`.
- **Niuans księgowy w modelu DCF (`EBIT` vs `CFO`):**
  - W rachunku zysków i strat US GAAP pozycja `OperatingIncomeLoss` (`EBIT`) **już uwzględnia koszt SBC** (SBC zostało odjęte).
  - Dlatego domyślny wzór FCFF w `dcf.py` przy `add_back_sbc=False`:
    $$\text{FCFF}_t = \text{EBIT}_t \cdot (1 - t) + \text{D\&A}_t - \text{CapEx}_t - \Delta \text{NWC}_t$$
    traktuje SBC jako **realny koszt ekonomiczny akcjonariuszy** (i nie odejmuje go drugi raz!).
  - Gdy użytkownik włączy `add_back_sbc=True`, model przechodzi na konwencję zbliżoną do księgowego `CFO` (dodając niepieniężne `SBC_t` z powrotem do `FCFF_t`), a silnik raportuje jawnie różnicę wyceny (`sbc_valuation_impact_ev`, `sbc_valuation_impact_per_share`).
  - **Uwaga o rozwodnieniu:** wariant `add_back_sbc=False` koryguje przepływy pieniężne o koszt bieżących emisji pracowniczych, natomiast przy `add_back_sbc=True` inwestor powinien dodatkowo uwzględnić przyszłe rozwodnienie liczby akcji.

---

## 6. Mnożniki rynkowe i konwencja dyskontowania

- **Mnożniki EV i P/E:** wyliczane są względem ostatniego pełnego roku obrotowego `10-K` (`EV/Revenue`, `EV/EBITDA`, `EV/EBIT`, `P/E`). Przy ujemnym `net_income` (np. `AMZN` w FY2022) `P/E` zwraca `None` (`N/A`).
- **FCF Yield:** zarówno `Reported FCF Yield`, jak i `True FCF Yield (ex-SBC)` są stopami zwrotu dla kapitału własnego (`FCF / Market Cap` lub `FCF / Implied Equity Value`), ponieważ `CFO - CapEx` jest przepływem po odsetkach (lewarowanym). Gdy aktywny jest `book_equity_fallback` bez ceny akcji / `Market Cap`, bieżące mnożniki rynkowe nie są wyliczane (`None`).
- **Konwencja dyskontowania:** domyślnie `mid_year` ($t - 0.5$ dla przepływów lat 1–5 oraz $5.0$ dla Terminal Value metodą Gordona przy warunku $WACC > g$).

---

## 7. Charakter edukacyjny i zgodność prawna

Projekt ma charakter wyłącznie edukacyjny, badawczy i inżynierski. Wszystkie parametry wejściowe (`Rf`, `Beta`, `ERP`, ścieżki 5Y, `g`, `include_leases`, `add_back_sbc`) podaje użytkownik. System nie generuje rekomendacji „Kup/Sprzedaj” ani „ceny docelowej” w rozumieniu `u.o.i.f.` oraz rozporządzenia `MAR` (UE nr 596/2014).
