# Reklamacja, odstąpienie od umowy, spór konsumencki

Cel: dobrać właściwą podstawę i przygotować pismo konsumenta.

1. Ustal typ umowy (sprzedaż towaru, usługa, treść cyfrowa), sposób zawarcia (w lokalu, na odległość,
   poza lokalem), daty (zawarcie, wydanie towaru, wykrycie wady) i czego konsument chce
   (naprawa, wymiana, obniżenie ceny, zwrot pieniędzy, wyjście z umowy).
2. Dobierz podstawę i pobierz przepisy:
   - odstąpienie bez podania przyczyny od umowy zawartej na odległość lub poza lokalem: art. 27, 38 upk;
   - niezgodność towaru z umową: art. 43a–43g upk;
   - treści i usługi cyfrowe: art. 43h i następne upk;
   - inne usługi i umowy: przepisy kc o danej umowie oraz art. 385^1 kc przy postanowieniach regulaminu.
   Wszystkie w `eli:DU/2014/827` albo `eli:DU/1964/93`; sprawdź wersję na datę umowy.
3. Sprawdź terminy w pobranym tekście i porównaj z datami sprawy; koniec terminu policz `compute_deadline`
   (`regime: "civil"`). Nie wyliczaj terminu jako pewnego,
   jeśli brakuje daty lub brzmienie na datę jest `temporal_unknown`.
4. Jeśli pasuje szablon (`get_document_template`: reklamacja_konsumencka, odstapienie_od_umowy_na_odleglosc,
   wezwanie_do_zaplaty), zadaj pytania kwalifikujące z szablonu, zbuduj tabelę twierdzeń, wywołaj
   `check_citations`, a potem `render_document`.
5. Wskaż dalsze kroki: odpowiedź przedsiębiorcy, powiatowy (miejski) rzecznik konsumentów,
   pozasądowe rozwiązywanie sporów, sąd.

Wynik: podstawa prawna z cytatami, ocena terminów, projekt pisma lub wypełniony szablon i dalsze kroki.
