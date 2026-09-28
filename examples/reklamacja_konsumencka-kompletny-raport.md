# Raport źródeł i uwag – Reklamacja konsumencka towaru

Status: projekt eksperymentalny – nie jest poradą prawną; nie sprawdzony przez prawnika.

Ten raport jest przeznaczony dla użytkownika i nie jest częścią pisma do adresata.

DANE PRZYKŁADOWE: strony i dane w tym dokumencie są fikcyjne (np. „Jan Przykładowy”) – wyłącznie do demonstracji.

## Dokument

| Pole | Wartość |
|---|---|
| Szablon | reklamacja_konsumencka (wersja 0.1.0) |
| Status szablonu | experimental — not reviewed by a lawyer (eksperymentalny – nie sprawdzony przez prawnika) |
| Status dokumentu | kompletny projekt |
| Powiązanie (binding_hash) | 3a40618598d1c84d474825e5b20c046d91cde091d7e4b8c343bfb86ec29e961f |
| report_id | example-3a40618598d1c84d |
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
| adres sprzedawcy | ul. Handlowa 3, 00-003 Przykładowo |
| opis towaru (nazwa, model) | czajnik elektryczny, model PRZYKŁAD-1 |
| numer paragonu, faktury lub zamówienia (opcjonalnie) | paragon nr 0001/2026 |
| cena towaru (opcjonalnie) | 189,99 PLN |
| data zawarcia umowy / zakupu (RRRR-MM-DD) | 10.08.2026 r. |
| data dostarczenia (wydania) towaru (RRRR-MM-DD) | 12.08.2026 r. |
| data stwierdzenia wady (RRRR-MM-DD) | 18.09.2026 r. |
| opis wady / niezgodności z umową | Czajnik przestał się nagrzewać; dioda zasilania świeci się, ale woda pozostaje zimna. |
| żądanie wybrane przez kupującego | wymiana |
| załączniki (tylko dokumenty faktycznie posiadane) | kopia paragonu nr 0001/2026, zdjęcie tabliczki znamionowej |

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

Raport: example-3a40618598d1c84d, utworzony 2026-09-26T12:00:00+00:00. raport przykładowy – nie z rzeczywistej kontroli. Kontrola sprawdza istnienie źródła i wierność cytatu (pytania 1–2). Nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie (pytania 3–4).

Statusy recenzji semantycznej pochodzą od klienta AI, a nie z niezależnej kontroli serwera.

Data sprawy przyjęta do kontroli czasowej: 2026-09-26.

| Twierdzenie | Status cytatu | Status czasowy | Recenzja semantyczna | Recenzent |
|---|---|---|---|---|
| c1 | cytat zgodny dosłownie ze wskazaną wersją (verified_exact) | tekst jednolity (znana data stanu prawnego) (consolidated_text) | zgłoszona przez klienta jako pozytywna (serwer tego nie weryfikował) | model językowy (LLM) uruchomiony przez klienta – to NIE jest weryfikacja przez prawnika |
| c2 | cytat zgodny po normalizacji odstępów i łączników (verified_normalized) | tekst jednolity (znana data stanu prawnego) (consolidated_text) | nie przeprowadzono | brak recenzenta |

Snapshoty źródeł, na których oparto kontrolę:

- example:705901828dcef510

## Kontrola czasowa

- data zawarcia umowy / zakupu (RRRR-MM-DD): Wymagana kontrola czasowa: ustal brzmienie przepisów i przepisy przejściowe właściwe dla daty zawarcia umowy; szablon nie przesądza reżimu.
- Daty podaje użytkownik (RRRR-MM-DD). Szablon nie oblicza terminów reklamacji ani terminu odpowiedzi sprzedawcy i nie przesądza reżimu odpowiedzialności. Dla każdej daty zakupu wymagana jest kontrola czasowa (get_legal_document z as_of = data zakupu, przepisy przejściowe).

## Nierozwiązane problemy i uwagi

- c1: raport przykładowy – nie z rzeczywistej kontroli
- c2: raport przykładowy – nie z rzeczywistej kontroli
- c2: nie oceniono, czy źródło wspiera twierdzenie i czy ma zastosowanie do faktów.

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
