# Linia orzecznicza w zagadnieniu prawnym

Cel: ustalić, jak sądy i organy rozstrzygają wskazane zagadnienie, z orzeczeniami za i przeciw.

1. Sformułuj zagadnienie jako pytanie prawne (jedno zdanie) i wypisz 3–5 wariantów zapytania:
   język ustawy, język potoczny, nazwa instytucji, numer przepisu (np. „art. 385^1 kc”).
2. Pobierz przepis, którego dotyczy zagadnienie (`search_legal`, `get_legal_document`), żeby wiedzieć,
   czego szukać w uzasadnieniach.
3. Szukaj orzeczeń każdym wariantem: `search_legal` z `kinds: ["judgment"]` i `live: true`.
   Uwzględnij sądy powszechne (SAOS), a w sprawach zamówień publicznych KIO, w sprawach danych osobowych
   decyzje UODO. Sąd Najwyższy przeszukasz, dodając `filters: {"court_type": "SUPREME"}` (sn.pl zwraca
   najnowsze trafienia bez fragmentów tekstu — pobierz treść, zanim ocenisz trafność). Jeśli źródło przekroczyło limit czasu,
   ponów wyszukiwanie po chwili.
4. Z wyników wybierz orzeczenia według zagadnienia i podobieństwa stanu faktycznego, nie według
   liczby wspólnych słów. Pobierz pełny tekst każdego wybranego orzeczenia (`get_legal_document`).
5. Dla każdego orzeczenia ustal: sąd, datę, sygnaturę, rozstrzygnięcie, pogląd sądu (nie strony)
   i czy jest prawomocne (zwykle nieznane — napisz to).
6. Szukaj aktywnie stanowisk przeciwnych. Jeśli ich nie znalazłeś, napisz, gdzie szukałeś.
7. `get_citations` pokaże, jakie przepisy i orzeczenia cytuje dane orzeczenie; użyj tego, żeby znaleźć
   orzeczenia wiodące.

Wynik: tabela (sąd, data, sygnatura, teza własnymi słowami, cytat z uzasadnienia, za/przeciw),
krótkie podsumowanie linii orzeczniczej i lista luk (np. brak orzeczeń SN z danego okresu).
Uchwały SN i wyroki TK opisz osobno; orzeczenia sądów powszechnych wiążą tylko w danej sprawie.
