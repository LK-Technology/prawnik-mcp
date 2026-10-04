# Interpretacje podatkowe w zagadnieniu

Cel: znaleźć interpretacje indywidualne dotyczące zagadnienia i ocenić ich znaczenie.

1. Sformułuj zagadnienie i podatek (PIT, CIT, VAT, PCC). Pobierz przepis ustawy podatkowej, którego
   dotyczy (`search_legal`, `get_legal_document`) w brzmieniu na datę zdarzenia.
2. Szukaj interpretacji: `search_legal` z `kinds: ["tax_ruling"]`, kilkoma wariantami zapytania
   (2–4 słowa kluczowe działają lepiej niż długie zdania). Wyniki obejmują interpretacje indywidualne,
   interpretacje ogólne i objaśnienia podatkowe; pole `authority_note` mówi, czym jest dany dokument.
   Interpretacje ogólne i objaśnienia przedstaw przed indywidualnymi. Sygnaturę podaj dosłownie.
3. Pobierz pełną treść najtrafniejszych (`get_legal_document`) i sprawdź datę, status („aktualna”)
   i stan faktyczny wnioskodawcy.
4. Pobierz art. 14b oraz art. 14k–14na Ordynacji podatkowej (`eli:DU/1997/926`): interpretacja chroni
   tylko wnioskodawcę i nie jest źródłem prawa. Napisz to w odpowiedzi.
5. Jeśli interpretacje są rozbieżne, pokaż obie grupy i daty; nowsze nie zawsze są korzystniejsze.
6. Kwoty w walucie obcej przeliczaj narzędziem `exchange_rate` (kurs średni NBP z ostatniego dnia roboczego
   przed zdarzeniem, `purpose: "vat"` albo `"pit"`), a terminy podatkowe narzędziem `compute_deadline`
   z `regime: "tax"`. Nie licz ich z pamięci.
7. Wyroki sądów administracyjnych: CBOSA nie jest przeszukiwana, więc brak wyroku w wynikach nic nie znaczy.

Wynik: przepis, tabela interpretacji (sygnatura, data, teza, stanowisko organu), wnioski z zastrzeżeniami
i sugestia, czy warto złożyć własny wniosek o interpretację.
