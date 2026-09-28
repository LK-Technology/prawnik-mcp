# Raport źródeł i uwag – Wezwanie do zapłaty

Status: projekt eksperymentalny – nie jest poradą prawną; nie sprawdzony przez prawnika.

Ten raport jest przeznaczony dla użytkownika i nie jest częścią pisma do adresata.

DANE PRZYKŁADOWE: strony i dane w tym dokumencie są fikcyjne (np. „Jan Przykładowy”) – wyłącznie do demonstracji.

## Dokument

| Pole | Wartość |
|---|---|
| Szablon | wezwanie_do_zaplaty (wersja 0.1.0) |
| Status szablonu | experimental — not reviewed by a lawyer (eksperymentalny – nie sprawdzony przez prawnika) |
| Status dokumentu | niekompletny formularz |
| Powiązanie (binding_hash) | 988703ce91a54e529f08d9a3e0030acd9a503744ad24b4b47e19a1c02ddb2661 |
| report_id | brak (formularz niekompletny – raport niewymagany) |
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
| kwota należności głównej | 1 234,50 PLN |
| kwota słownie | tysiąc dwieście trzydzieści cztery złote 50/100 |
| podstawa należności (np. numer i data faktury lub umowy) | faktura VAT nr FV/2026/07/015 z dnia 15.07.2026 r. za usługi graficzne |
| termin płatności wynikający z faktury lub umowy (RRRR-MM-DD) | 29.07.2026 r. |
| czy wierzyciel żąda odsetek ustawowych za opóźnienie | tak |
| data, od której wierzyciel żąda odsetek (RRRR-MM-DD, wskazana przez użytkownika) | 30.07.2026 r. |
| załączniki (tylko dokumenty faktycznie posiadane) | kopia faktury VAT nr FV/2026/07/015 |

Brakujące dane (w piśmie oznaczone jako [UZUPEŁNIJ: …]):

- adres dłużnika
- termin zapłaty wyznaczony w wezwaniu (RRRR-MM-DD)
- numer rachunku bankowego wierzyciela

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

Nie przeprowadzono kontroli cytatów dla tego dokumentu (formularz niekompletny). Po uzupełnieniu danych wypełniony projekt wymaga raportu z check_citations.

## Kontrola czasowa

- termin płatności wynikający z faktury lub umowy (RRRR-MM-DD): Ustal brzmienie przepisów właściwe dla daty wymagalności (get_legal_document z as_of).
- Daty podaje użytkownik (RRRR-MM-DD). Szablon nie oblicza terminów ani odsetek i nie przesądza, które brzmienie przepisów obowiązuje w datach sprawy – to wymaga kontroli czasowej przez get_legal_document z parametrem as_of.

## Nierozwiązane problemy i uwagi

- Brak zgłoszonych problemów. Nie oznacza to, że pismo jest poprawne prawnie.

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
