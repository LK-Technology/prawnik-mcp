# Raport źródeł i uwag – Reklamacja konsumencka towaru

Status: projekt eksperymentalny – nie jest poradą prawną; nie sprawdzony przez prawnika.

Ten raport jest przeznaczony dla użytkownika i nie jest częścią pisma do adresata.

DANE PRZYKŁADOWE: strony i dane w tym dokumencie są fikcyjne (np. „Jan Przykładowy”) – wyłącznie do demonstracji.

## Dokument

| Pole | Wartość |
|---|---|
| Szablon | reklamacja_konsumencka (wersja 0.1.0) |
| Status szablonu | experimental — not reviewed by a lawyer (eksperymentalny – nie sprawdzony przez prawnika) |
| Status dokumentu | niekompletny formularz |
| Powiązanie (binding_hash) | 73c725a4109bf2bfad696c14d30a1be0fc9dfdf2857f623dc8470e93bbd779c8 |
| report_id | brak (formularz niekompletny – raport niewymagany) |
| Wygenerowano | 2026-09-26 20:45 UTC |
| Historia przeglądów szablonu | brak przeglądu przez prawnika |

## Wykorzystane fakty

| Fakt | Wartość podana przez użytkownika |
|---|---|
| miejscowość sporządzenia pisma | Przykładowo |
| data sporządzenia pisma (RRRR-MM-DD) | 22.09.2026 r. |
| imię i nazwisko kupującego | Jan Przykładowy |
| adres kupującego | ul. Fikcyjna 1, 00-001 Przykładowo |
| adres e-mail kupującego (opcjonalnie) | jan.przykladowy@example.invalid |
| nazwa sprzedawcy | Sklep Przykładowy sp. z o.o. |
| opis towaru (nazwa, model) | czajnik elektryczny, model PRZYKŁAD-1 |
| numer paragonu, faktury lub zamówienia (opcjonalnie) | paragon nr 0001/2026 |
| cena towaru (opcjonalnie) | 189,99 PLN |
| data zawarcia umowy / zakupu (RRRR-MM-DD) | 10.08.2026 r. |
| data stwierdzenia wady (RRRR-MM-DD) | 18.09.2026 r. |
| opis wady / niezgodności z umową | Czajnik przestał się nagrzewać; dioda zasilania świeci się, ale woda pozostaje zimna. |
| załączniki (tylko dokumenty faktycznie posiadane) | kopia paragonu nr 0001/2026, zdjęcie tabliczki znamionowej |

Brakujące dane (w piśmie oznaczone jako [UZUPEŁNIJ: …]):

- adres sprzedawcy
- data dostarczenia (wydania) towaru (RRRR-MM-DD)
- żądanie wybrane przez kupującego

## Pytania kwalifikujące

| Pytanie | Odpowiedź |
|---|---|
| Czy kupujący kupił towar jako osoba prywatna, a zakup nie był bezpośrednio związany z jego działalnością gospodarczą lub zawodową? | tak |
| Czy kupujący prowadzi jednoosobową działalność gospodarczą i kupił towar na firmę (np. faktura na NIP)? | nie |
| Czy sprzedawca sprzedał towar w ramach prowadzonej działalności gospodarczej (sklep, firma), a nie jako osoba prywatna? | tak |
| Czego dotyczy reklamacja? | towar (rzecz ruchoma) |
| Czy umowę zawarto przed zmianą przepisów o odpowiedzialności sprzedawcy wobec konsumenta? (datę graniczną ustal w źródłach; szablon jej nie przesądza) | nie |
| Czy umowa podlega prawu polskiemu (np. zakup w polskim sklepie lub od sprzedawcy kierującego ofertę do Polski)? | tak |
| Czy chcesz skorzystać z gwarancji producenta (karta gwarancyjna) zamiast reklamacji u sprzedawcy? | nie |

## Kontrola cytatów

Nie przeprowadzono kontroli cytatów dla tego dokumentu (formularz niekompletny). Po uzupełnieniu danych wypełniony projekt wymaga raportu z check_citations.

## Kontrola czasowa

- data zawarcia umowy / zakupu (RRRR-MM-DD): Wymagana kontrola czasowa: ustal brzmienie przepisów i przepisy przejściowe właściwe dla daty zawarcia umowy; szablon nie przesądza reżimu.
- Daty podaje użytkownik (RRRR-MM-DD). Szablon nie oblicza terminów reklamacji ani terminu odpowiedzi sprzedawcy i nie przesądza reżimu odpowiedzialności. Dla każdej daty zakupu wymagana jest kontrola czasowa (get_legal_document z as_of = data zakupu, przepisy przejściowe).

## Nierozwiązane problemy i uwagi

- Brak zgłoszonych problemów. Nie oznacza to, że pismo jest poprawne prawnie.

## Źródła do samodzielnej weryfikacji

Lista zawiera wyłącznie identyfikatory i lokalizatory. Treść, aktualne brzmienie i zastosowanie każdego przepisu należy sprawdzić narzędziem get_legal_document (z parametrem as_of = data zawarcia umowy) i check_citations. Szablon nie zawiera tekstu przepisów ani twierdzeń o ich treści.

- eli:DU/2014/827 art. 43a – do weryfikacji: zgodność towaru z umową (brzmienie właściwe dla daty umowy)
- eli:DU/2014/827 art. 43b – do weryfikacji: kryteria zgodności towaru z umową
- eli:DU/2014/827 art. 43c – do weryfikacji: odpowiedzialność przedsiębiorcy
- eli:DU/2014/827 art. 43d – do weryfikacji: żądania naprawy lub wymiany
- eli:DU/2014/827 art. 43e – do weryfikacji: obniżenie ceny i odstąpienie od umowy
- eli:DU/1964/93 art. 22^1 – do weryfikacji: definicja konsumenta
- eli:DU/1964/93 art. 43^1 – do weryfikacji: definicja przedsiębiorcy
- eli:DU/1964/93 art. 556 – do kontroli czasowej: przepisy o rękojmi (brzmienie dla umów zawartych w innym okresie)

## Ograniczenia

- Dokument jest projektem eksperymentalnym i nie jest poradą prawną; szablonu nie sprawdził prawnik.
- Serwer sprawdza istnienie źródeł i wierność cytatów; nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie.
- Szablon nie oblicza terminów ani odsetek i nie stwierdza zachowania terminów.
- Recenzja wykonana przez model językowy nie jest recenzją prawnika.
