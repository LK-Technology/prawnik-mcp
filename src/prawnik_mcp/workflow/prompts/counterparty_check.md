# Weryfikacja kontrahenta (spółka, fundacja, przedsiębiorca)

Cel: zebrać z rejestrów publicznych fakty o podmiocie i wskazać, czego rejestry nie mówią.

1. Ustal identyfikator: numer KRS, NIP albo REGON (dla podmiotu z UE: numer VAT z prefiksem kraju).
   Serwer nie wyszukuje po nazwie ani po osobach; jeśli użytkownik zna tylko nazwę, poproś o NIP lub KRS
   (są na fakturze, stronie internetowej, w stopce e-maila).
2. Wywołaj `lookup_entity` z identyfikatorem. Podaj `date`, jeśli liczy się stan na dzień transakcji lub płatności.
   Jeśli chodzi o konkretną płatność, podaj `bank_account`: biała lista sprawdzi, czy rachunek jest zgłoszony,
   i zwróci `requestId` (identyfikator potwierdzający sprawdzenie).
3. Przedstaw kartę podmiotu: nazwa, forma prawna, KRS/NIP/REGON, siedziba, data rejestracji, kapitał,
   status VAT na dzień, sposób reprezentacji (dosłownie z odpisu) i skład organu (dane osobowe są zamaskowane
   przez Ministerstwo Sprawiedliwości — nie próbuj ich odtwarzać).
4. Wypisz sygnały ostrzegawcze z danych: likwidacja, upadłość, restrukturyzacja, wykreślenie, status VAT inny niż
   „Czynny”, rachunek spoza białej listy, brak wzmianek o złożonych sprawozdaniach finansowych.
5. Reprezentacja: jeśli użytkownik pyta, kto może podpisać umowę, zestaw sposób reprezentacji z listą funkcji.
   Ocenę opisz jako pomocniczą; wiąże treść wpisu w KRS. Przepisy: art. 201 i 205 ksh (sp. z o.o.),
   art. 368 i 373 ksh (spółka akcyjna) — pobierz je `get_legal_document`, jeśli są potrzebne.
6. Czego rejestry nie pokazują: beneficjentów rzeczywistych (CRBR), sprawozdań finansowych (RDF),
   ogłoszeń w MSiG i KRZ, zaległości podatkowych. Podaj użytkownikowi, gdzie sprawdzić je ręcznie
   (crbr.podatki.gov.pl, ekrs.ms.gov.pl, krz.ms.gov.pl).
7. Brak negatywnych wpisów nie potwierdza dobrej kondycji podmiotu. Odpis z API nie zastępuje urzędowego
   odpisu z KRS.

Wynik: karta podmiotu ze źródłami i datami pobrania, sygnały ostrzegawcze, ocena reprezentacji (jeśli dotyczy)
i lista rzeczy do sprawdzenia ręcznie.
