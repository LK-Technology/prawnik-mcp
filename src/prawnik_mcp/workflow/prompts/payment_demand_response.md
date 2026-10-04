# Odpowiedź na wezwanie do zapłaty

Cel: ocenić otrzymane wezwanie i przygotować rzeczową odpowiedź. Serwer nie liczy przedawnienia ani
terminów; model może podać przepisy i daty, a ocenę końcową pozostawia użytkownikowi lub profesjonaliście.

1. Jeśli wierzyciel jest firmą, sprawdź go `lookup_entity` (NIP lub KRS z wezwania), w tym rachunek do zapłaty.
   Wypisz z wezwania: wierzyciela, kwotę (należność główna, odsetki, koszty), podstawę roszczenia (umowa,
   faktura, regulamin), daty (wymagalność, termin zapłaty) i to, czego wierzyciel żąda.
2. Ustal fakty po stronie użytkownika: czy świadczenie było wykonane, czy płacił (jak i kiedy),
   czy ma dowody, czy jest konsumentem. Oddziel fakty od założeń.
3. Pobierz przepisy, które mogą mieć znaczenie, na przykład: art. 455 i 481 kc (wymagalność, odsetki),
   art. 117–125 kc (przedawnienie — podaj przepisy i daty, nie wyliczaj wyniku jako pewnego),
   art. 385^1–385^3 kc, jeśli podstawą jest wzorzec umowny wobec konsumenta.
   Termin odpowiedzi lub zapłaty licz narzędziem `compute_deadline` (`regime: "civil"`), podając datę
   doręczenia wezwania; początek terminu ustala użytkownik.
4. Oceń każdy element żądania osobno: co jest bezsporne, co sporne i dlaczego, czego brakuje w dowodach.
5. Szukaj orzeczeń w podobnych sprawach (`search_legal`, `kinds: ["judgment"]`) i podaj je z zastrzeżeniem,
   że wiążą tylko w danej sprawie.
6. Zaproponuj odpowiedź: uznanie, częściowe uznanie, zaprzeczenie z uzasadnieniem albo propozycję ugody.
   Pismo ma być rzeczowe, bez gróźb. Nie uzależniaj zawiadomienia organu (UOKiK, UODO, urząd skarbowy,
   policja) od zapłaty ani rezygnacji z roszczenia; pobierz art. 115 § 12 i art. 191 kk i wyjaśnij,
   dlaczego takie sformułowanie jest ryzykowne.

Wynik: ocena żądania punkt po punkcie, lista dowodów do zebrania, projekt odpowiedzi i ryzyka.
