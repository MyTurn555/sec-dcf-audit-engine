Zasady projektu: SEC DCF Audit Engine

Architektura i rygor finansowy

Wszystkie obliczenia finansowe, wskaźniki i model DCF muszą być w 100% deterministyczne w Pythonie. Nigdy nie używaj modeli LLM do arytmetyki.



Każda funkcja finansowa musi mieć testy jednostkowe w pytest (w tym testy tożsamości bilansowych i przypadków brzegowych).



Wyjaśniaj krótko sens finansowy proponowanych założeń i definicji (np. sposób liczenia FCF, kapitału obrotowego netto), aby autor mógł świadomie recenzować kod.



Stos technologiczny i dane

Python 3.11+. Do danych SEC EDGAR używaj wyłącznie biblioteki edgartools (from edgar import Company, set\_identity), czytając tożsamość ze zmiennej środowiskowej EDGAR\_IDENTITY i respektując limit 10 zapytań/s.



W późniejszym module AI używaj wyłącznie aktualnego SDK google-genai (from google import genai). Nigdy nie używaj wycofanego pakietu google-generativeai.



Nigdy nie zapisuj kluczy API, prywatnych e-maili ani surowych zrzutów płatnych danych w kodzie ani w historii Git.



Zgodność prawna i edukacyjna

Projekt ma charakter wyłącznie edukacyjny i badawczy. Interfejs i raporty nie mogą generować rekomendacji „Kup/Sprzedaj” ani „ceny docelowej” — wyłącznie przedział implikowanej wartości dla założeń wpisanych przez użytkownika.



Wpisanie zakazu używania google-generativeai w AGENTS.md z góry zapobiega częstemu błędowi agentów, które potrafią wracać do starego, wycofanego 30 listopada 2025 r. pakietu.

