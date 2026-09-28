# Raport źródeł i uwag – Oświadczenie o odstąpieniu od umowy zawartej na odległość lub poza lokalem przedsiębiorstwa

Status: projekt eksperymentalny – nie jest poradą prawną; nie sprawdzony przez prawnika.

Ten raport jest przeznaczony dla użytkownika i nie jest częścią pisma do adresata.

DANE PRZYKŁADOWE: strony i dane w tym dokumencie są fikcyjne (np. „Jan Przykładowy”) – wyłącznie do demonstracji.

## Dokument

| Pole | Wartość |
|---|---|
| Szablon | odstapienie_od_umowy_na_odleglosc (wersja 0.1.0) |
| Status szablonu | experimental — not reviewed by a lawyer (eksperymentalny – nie sprawdzony przez prawnika) |
| Status dokumentu | kompletny projekt |
| Powiązanie (binding_hash) | 9421ee57c97a4d1a2e73b9e88dbf04d9ab58e914af766a488fdde5025736dc73 |
| report_id | example-9421ee57c97a4d1a |
| Wygenerowano | 2026-09-26 20:45 UTC |
| Historia przeglądów szablonu | brak przeglądu przez prawnika |

## Wykorzystane fakty

| Fakt | Wartość podana przez użytkownika |
|---|---|
| miejscowość sporządzenia pisma | Przykładowo |
| data sporządzenia pisma (RRRR-MM-DD) | 24.09.2026 r. |
| imię i nazwisko konsumenta | Jan Przykładowy |
| adres konsumenta | ul. Fikcyjna 1, 00-001 Przykładowo |
| adres e-mail konsumenta (opcjonalnie) | jan.przykladowy@example.invalid |
| nazwa przedsiębiorcy | Sklep Internetowy Przykład sp. z o.o. |
| adres przedsiębiorcy | ul. Wirtualna 4, 00-004 Przykładowo |
| rodzaj umowy | umowa sprzedaży |
| sposób zawarcia umowy | na odległość |
| przedmiot umowy (towar lub usługa) | kurtka przeciwdeszczowa, rozmiar M |
| numer zamówienia (opcjonalnie) | ZAM-2026-000123 |
| data zawarcia umowy (RRRR-MM-DD) | 14.09.2026 r. |
| data odebrania towaru (RRRR-MM-DD; dla umowy sprzedaży) | 17.09.2026 r. |
| numer rachunku do zwrotu płatności (opcjonalnie) | 91 9999 9999 0000 0000 0000 1234 |

## Pytania kwalifikujące

| Pytanie | Odpowiedź |
|---|---|
| Czy zawierający umowę jest osobą prywatną, a umowa nie była bezpośrednio związana z jego działalnością gospodarczą lub zawodową? | tak |
| Czy drugą stroną umowy jest przedsiębiorca (sklep, firma)? | tak |
| Czy umowę zawarto w sklepie stacjonarnym (w lokalu przedsiębiorstwa), przy jednoczesnej obecności stron? | nie |
| Czy umowa dotyczy treści cyfrowych lub usług cyfrowych (np. pliki do pobrania, aplikacje, subskrypcje online)? | nie |
| Czy umowa podlega prawu polskiemu? | tak |
| Czy przedsiębiorca poinformował o prawie odstąpienia od umowy (np. w potwierdzeniu zamówienia lub regulaminie)? | tak |
| Czy według Twoich ustaleń oświadczenie zostanie wysłane przed upływem terminu na odstąpienie? | tak |
| Czy towar wyprodukowano według specyfikacji konsumenta lub w celu zaspokojenia jego zindywidualizowanych potrzeb (np. na wymiar, z personalizacją)? | nie |
| Czy towar dostarczono w zapieczętowanym opakowaniu, którego ze względu na ochronę zdrowia lub higienę nie można zwrócić po otwarciu, i opakowanie zostało otwarte? | nie |
| Czy chodzi o nagrania dźwiękowe lub wizualne albo programy komputerowe w zapieczętowanym opakowaniu, które zostało otwarte? | nie |
| Czy towar ulega szybkiemu zepsuciu lub ma krótki termin przydatności do użycia? | nie |
| Czy towar po dostarczeniu został nierozłącznie połączony z innymi rzeczami? | nie |
| Czy usługa została w pełni wykonana za wyraźną zgodą konsumenta przed upływem terminu na odstąpienie? | nie dotyczy |
| Czy umowa dotyczy zakwaterowania (innego niż mieszkalne), przewozu rzeczy, najmu samochodów, gastronomii, usług rekreacyjnych, wydarzeń rozrywkowych, sportowych lub kulturalnych z oznaczonym dniem lub okresem świadczenia? | nie |
| Czy umowa dotyczy dzienników, periodyków lub czasopism (z wyjątkiem prenumeraty)? | nie |
| Czy umowę zawarto w drodze aukcji publicznej? | nie |
| Czy cena lub wynagrodzenie zależy od wahań na rynku finansowym, nad którymi przedsiębiorca nie sprawuje kontroli? | nie |
| Czy konsument sam wezwał przedsiębiorcę do przyjazdu w celu dokonania pilnej naprawy lub konserwacji? | nie |

## Kontrola cytatów

Raport: example-9421ee57c97a4d1a, utworzony 2026-09-26T12:00:00+00:00. raport przykładowy – nie z rzeczywistej kontroli. Kontrola sprawdza istnienie źródła i wierność cytatu (pytania 1–2). Nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie (pytania 3–4).

Statusy recenzji semantycznej pochodzą od klienta AI, a nie z niezależnej kontroli serwera.

Data sprawy przyjęta do kontroli czasowej: 2026-09-26.

| Twierdzenie | Status cytatu | Status czasowy | Recenzja semantyczna | Recenzent |
|---|---|---|---|---|
| c1 | cytat zgodny dosłownie ze wskazaną wersją (verified_exact) | tekst jednolity (znana data stanu prawnego) (consolidated_text) | zgłoszona przez klienta jako pozytywna (serwer tego nie weryfikował) | model językowy (LLM) uruchomiony przez klienta – to NIE jest weryfikacja przez prawnika |
| c2 | cytat zgodny po normalizacji odstępów i łączników (verified_normalized) | tekst jednolity (znana data stanu prawnego) (consolidated_text) | nie przeprowadzono | brak recenzenta |

Snapshoty źródeł, na których oparto kontrolę:

- example:705901828dcef510

## Kontrola czasowa

- data zawarcia umowy (RRRR-MM-DD): Ustal brzmienie przepisów właściwe dla daty zawarcia umowy (get_legal_document z as_of); termin na odstąpienie nie jest obliczany przez szablon.
- Daty podaje użytkownik (RRRR-MM-DD). W MVP szablon nie oblicza terminu na odstąpienie i nigdy nie stwierdza, że termin został zachowany. Brzmienie przepisów właściwe dla daty zawarcia umowy wymaga kontroli czasowej (get_legal_document z as_of).

## Nierozwiązane problemy i uwagi

- c1: raport przykładowy – nie z rzeczywistej kontroli
- c2: raport przykładowy – nie z rzeczywistej kontroli
- c2: nie oceniono, czy źródło wspiera twierdzenie i czy ma zastosowanie do faktów.

## Źródła do samodzielnej weryfikacji

Lista zawiera wyłącznie identyfikatory i lokalizatory. Treść, aktualne brzmienie i zastosowanie każdego przepisu należy sprawdzić narzędziem get_legal_document (z parametrem as_of = data zawarcia umowy) i check_citations. Szablon nie zawiera tekstu przepisów ani twierdzeń o ich treści.

- eli:DU/2014/827 art. 27 – do weryfikacji: prawo odstąpienia od umowy
- eli:DU/2014/827 art. 28 – do weryfikacji: początek biegu terminu
- eli:DU/2014/827 art. 29 – do weryfikacji: skutek braku informacji o prawie odstąpienia
- eli:DU/2014/827 art. 30 – do weryfikacji: forma oświadczenia
- eli:DU/2014/827 art. 32 – do weryfikacji: zwrot płatności
- eli:DU/2014/827 art. 34 – do weryfikacji: obowiązki konsumenta po odstąpieniu
- eli:DU/2014/827 art. 38 – do weryfikacji: wyłączenia prawa odstąpienia
- celex:32011L0083 art. 9 – do weryfikacji: prawo odstąpienia w dyrektywie
- celex:32011L0083 art. 16 – do weryfikacji: wyjątki od prawa odstąpienia w dyrektywie

## Ograniczenia

- Dokument jest projektem eksperymentalnym i nie jest poradą prawną; szablonu nie sprawdził prawnik.
- Serwer sprawdza istnienie źródeł i wierność cytatów; nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie.
- Szablon nie oblicza terminów ani odsetek i nie stwierdza zachowania terminów.
- Recenzja wykonana przez model językowy nie jest recenzją prawnika.
