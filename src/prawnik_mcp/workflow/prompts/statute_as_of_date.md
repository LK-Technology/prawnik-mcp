# Brzmienie przepisu na określoną datę

Cel: podać tekst przepisu obowiązujący w dniu zdarzenia albo uczciwie stwierdzić, że nie da się go ustalić.

1. Ustal datę zdarzenia (zawarcie umowy, wydanie towaru, czyn, doręczenie). Jeśli jest kilka dat,
   wypisz je i ustal, która decyduje; w razie wątpliwości zapytaj.
2. Zidentyfikuj akt: `search_legal` po nazwie, skrócie („kc”, „upk”, „RODO”) albo po „Dz.U. RRRR poz. N”.
3. Wywołaj `list_act_versions` dla aktu: zobacz teksty jednolite i akty zmieniające z datami wejścia w życie.
4. Wywołaj `get_legal_document` z `locator` i `as_of` równym dacie zdarzenia.
   - `temporal_status` = `in_force`: podaj tekst i etykietę wersji.
   - `temporal_unknown`: podaj tekst z zastrzeżeniem, wymień zmiany z `pending_changes` i daty ich wejścia
     w życie; wskaż, czy mogły dotyczyć tego przepisu. Nie zgaduj brzmienia historycznego.
5. Sprawdź przepisy przejściowe ustawy zmieniającej (`excluded_provisions`, akt zmieniający z listy wersji):
   to one często decydują, które brzmienie stosować do umów zawartych przed zmianą.
6. Dla aktów UE: tekst z Dziennika Urzędowego jest wiążący; wersja skonsolidowana ma charakter dokumentacyjny.

Wynik: tekst przepisu, wersja (tekst jednolity, stan prawny na dzień), status czasowy, zmiany, które mogą mieć
znaczenie, i przepisy przejściowe.
