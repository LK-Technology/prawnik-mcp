# Raport źródeł i uwag – Wezwanie do zapłaty

Status: projekt eksperymentalny – nie jest poradą prawną; nie sprawdzony przez prawnika.

Ten raport jest przeznaczony dla użytkownika i nie jest częścią pisma do adresata.

DANE PRZYKŁADOWE: strony i dane w tym dokumencie są fikcyjne (np. „Jan Przykładowy”) – wyłącznie do demonstracji.

## Dokument

| Pole | Wartość |
|---|---|
| Szablon | wezwanie_do_zaplaty (wersja 0.1.0) |
| Status szablonu | experimental — not reviewed by a lawyer (eksperymentalny – nie sprawdzony przez prawnika) |
| Status dokumentu | kompletny projekt |
| Powiązanie (binding_hash) | 51a1b0bb100006f037db6608a38e198f59d2faed995974ebbbf895c33db72199 |
| report_id | example-51a1b0bb100006f0 |
| Wygenerowano | 2026-09-26 20:45 UTC |
| Historia przeglądów szablonu | brak przeglądu przez prawnika |

## Wykorzystane fakty

| Fakt | Wartość podana przez użytkownika |
|---|---|
| miejscowość sporządzenia pisma | Przykładowo |
| data sporządzenia pisma (RRRR-MM-DD) | 20.09.2026 r. |
| imię i nazwisko lub nazwa wierzyciela | Jan Przykładowy – Usługi Graficzne |
| adres wierzyciela | ul. Fikcyjna 1, 00-001 Przykładowo |
| imię i nazwisko lub nazwa dłużnika | Przykładowa Spółka z o.o. |
| adres dłużnika | ul. Zmyślona 2, 00-002 Przykładowo |
| kwota należności głównej | 1 234,50 PLN |
| kwota słownie | tysiąc dwieście trzydzieści cztery złote 50/100 |
| podstawa należności (np. numer i data faktury lub umowy) | faktura VAT nr FV/2026/07/015 z dnia 15.07.2026 r. za usługi graficzne |
| termin płatności wynikający z faktury lub umowy (RRRR-MM-DD) | 29.07.2026 r. |
| termin zapłaty wyznaczony w wezwaniu (RRRR-MM-DD) | 05.10.2026 r. |
| numer rachunku bankowego wierzyciela | 91 9999 9999 0000 0000 0000 1234 |
| czy wierzyciel żąda odsetek ustawowych za opóźnienie | tak |
| data, od której wierzyciel żąda odsetek (RRRR-MM-DD, wskazana przez użytkownika) | 30.07.2026 r. |
| załączniki (tylko dokumenty faktycznie posiadane) | kopia faktury VAT nr FV/2026/07/015 |

## Pytania kwalifikujące

| Pytanie | Odpowiedź |
|---|---|
| Czy obie strony mają siedzibę lub miejsce zamieszkania w Polsce, a umowa podlega prawu polskiemu? | tak |
| Czy termin zapłaty wynikający z faktury lub umowy już upłynął? | tak |
| Czy w sprawie toczy się już postępowanie sądowe, egzekucyjne, upadłościowe lub restrukturyzacyjne dotyczące dłużnika? | nie |
| Czy dłużnik kwestionuje istnienie lub wysokość należności (np. złożył reklamację lub zarzuty)? | nie |
| Czy od terminu płatności minęło więcej niż 2 lata? | nie |
| Czy dłużnik jest osobą prywatną (konsumentem), a nie przedsiębiorcą? | nie |

## Kontrola cytatów

Raport: example-51a1b0bb100006f0, utworzony 2026-09-26T12:00:00+00:00. raport przykładowy – nie z rzeczywistej kontroli. Kontrola sprawdza istnienie źródła i wierność cytatu (pytania 1–2). Nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie (pytania 3–4).

Statusy recenzji semantycznej pochodzą od klienta AI, a nie z niezależnej kontroli serwera.

Data sprawy przyjęta do kontroli czasowej: 2026-09-26.

| Twierdzenie | Status cytatu | Status czasowy | Recenzja semantyczna | Recenzent |
|---|---|---|---|---|
| c1 | cytat zgodny dosłownie ze wskazaną wersją (verified_exact) | tekst jednolity (znana data stanu prawnego) (consolidated_text) | zgłoszona przez klienta jako pozytywna (serwer tego nie weryfikował) | model językowy (LLM) uruchomiony przez klienta – to NIE jest weryfikacja przez prawnika |
| c2 | cytat zgodny po normalizacji odstępów i łączników (verified_normalized) | tekst jednolity (znana data stanu prawnego) (consolidated_text) | nie przeprowadzono | brak recenzenta |

Snapshoty źródeł, na których oparto kontrolę:

- example:705901828dcef510

## Kontrola czasowa

- termin płatności wynikający z faktury lub umowy (RRRR-MM-DD): Ustal brzmienie przepisów właściwe dla daty wymagalności (get_legal_document z as_of).
- Daty podaje użytkownik (RRRR-MM-DD). Szablon nie oblicza terminów ani odsetek i nie przesądza, które brzmienie przepisów obowiązuje w datach sprawy – to wymaga kontroli czasowej przez get_legal_document z parametrem as_of.

## Nierozwiązane problemy i uwagi

- c1: raport przykładowy – nie z rzeczywistej kontroli
- c2: raport przykładowy – nie z rzeczywistej kontroli
- c2: nie oceniono, czy źródło wspiera twierdzenie i czy ma zastosowanie do faktów.

## Źródła do samodzielnej weryfikacji

Lista zawiera wyłącznie identyfikatory i lokalizatory. Treść, aktualne brzmienie i zastosowanie każdego przepisu należy sprawdzić narzędziem get_legal_document (z parametrem as_of) i check_citations. Szablon nie zawiera tekstu przepisów ani twierdzeń o ich treści.

- eli:DU/1964/93 art. 455 – do weryfikacji: termin spełnienia świadczenia
- eli:DU/1964/93 art. 476 – do weryfikacji: opóźnienie dłużnika
- eli:DU/1964/93 art. 481 – do weryfikacji: odsetki za opóźnienie (tylko jeśli użytkownik ich żąda)
- eli:DU/1964/93 art. 117 – do weryfikacji: przedawnienie
- eli:DU/1964/93 art. 118 – do weryfikacji: terminy przedawnienia

## Ograniczenia

- Dokument jest projektem eksperymentalnym i nie jest poradą prawną; szablonu nie sprawdził prawnik.
- Serwer sprawdza istnienie źródeł i wierność cytatów; nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie.
- Szablon nie oblicza terminów ani odsetek i nie stwierdza zachowania terminów.
- Recenzja wykonana przez model językowy nie jest recenzją prawnika.
