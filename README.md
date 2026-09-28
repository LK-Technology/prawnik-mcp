# Prawnik MCP — wydanie eksperymentalne 0.1.0

Lokalny serwer MCP do wyszukiwania wybranych źródeł polskiego prawa cywilnego i konsumenckiego, pobierania dokładnych fragmentów z wersją i snapshotem, kontroli przywołań oraz przygotowania trzech prostych projektów pism.

> **To nie jest porada prawna. Szablony, przypadki testowe i reguły nie zostały ocenione przez prawnika.** Kontrola cytatów sprawdza, czy źródło istnieje w lokalnym korpusie i czy cytat jest wierny. Nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie do faktów. Wynik nigdy nie jest oznaczany jako „prawnie bezbłędny”.

## Co działa

| Narzędzie MCP | Działanie |
|---|---|
| `search_legal` | Identyfikatory (`art. 27 upk`, `art. 385^1 kc`, sygnatura) i pełny tekst (SQLite FTS5/BM25). Zwraca fragmenty ≤ 800 znaków, URL, `snapshot_id`, zakres przeszukania i status. |
| `get_legal_document` | Dokładny tekst artykułu lub jednostki (`§`, `ust.`, `pkt`), wersję (obwieszczenie tekstu jednolitego), datę stanu prawnego, zmiany oczekujące, pominięte przepisy przejściowe oraz `temporal_status` dla `as_of`. Orzeczenia są zwracane stronami. |
| `check_citations` | Sprawdza istnienie dokumentu, wierność cytatu (dokładną lub po normalizacji), lokalizator, wersję i metadane sygnatury. Twierdzenia prawne bez dowodu oznacza jako błąd krytyczny. Daje `report_id` powiązany z faktami i treścią pisma. |
| `get_document_template` | Trzy szablony: `wezwanie_do_zaplaty`, `reklamacja_konsumencka`, `odstapienie_od_umowy_na_odleglosc`. |
| `render_document` | Pismo w Markdown i DOCX oraz **osobny** raport źródeł i uwag. Blokuje eksport wypełnionego pisma, gdy brak raportu, raport jest nieaktualny (zmienione fakty, treść lub wersja szablonu), zawiera błędy krytyczne albo jego snapshoty zniknęły. Przy brakach tworzy tylko jawnie nieuzupełniony formularz. |
| `sources_status` | Zakres korpusu, ostatnia synchronizacja, dostęp, znane luki i obszary nieobsługiwane. |

Dostępne są też prompty `procedura_analizy` i `recenzja_zastosowania` (procedura dla klienta AI i osobny przegląd zastosowania prawa) oraz zasób `prawnik://procedura`.

Statusy wyników: `ok`, `not_found`, `ambiguous`, `source_unavailable`, `stale`, `out_of_scope`, `temporal_unknown`, `invalid_input`, `blocked`. Brak trafienia nie oznacza, że przepis lub orzeczenie nie istnieje.

## Instalacja

Wymagania: Python 3.12+ i dostęp do sieci tylko przy synchronizacji na żywo. Nie potrzeba klucza API ani GPU.

```bash
git clone <repo> prawnik-mcp && cd prawnik-mcp   # lub rozpakuj katalog
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock        # wersje przypięte
.venv/bin/pip install -e ".[dev]"

# Korpus: offline z próbek repozytorium (rzeczywiste odpowiedzi API z 26.09.2026)...
.venv/bin/prawnik-mcp --data ./data sync --offline
# ...albo na żywo: 2 PDF tekstów jednolitych (~1,8 MB), dyrektywa (~0,3 MB), ≤ 30 orzeczeń SAOS
.venv/bin/prawnik-mcp --data ./data sync

.venv/bin/prawnik-mcp --data ./data status
.venv/bin/prawnik-mcp --data ./data search "odstąpienie od umowy zawartej na odległość"
.venv/bin/prawnik-mcp --data ./data get eli:DU/2014/827 "art. 38 ust. 1 pkt 3"
.venv/bin/pytest -q                                # testy offline
.venv/bin/python evals/run_offline.py --data-dir ./data
```

### Podłączenie klienta MCP (stdio)

Claude Code:

```bash
claude mcp add prawnik -- /ABS/ŚCIEŻKA/prawnik-mcp/.venv/bin/prawnik-mcp --data /ABS/ŚCIEŻKA/prawnik-mcp/data serve
```

Claude Desktop i inne klienty zgodne z MCP (`mcpServers` w pliku konfiguracyjnym):

```json
{ "mcpServers": { "prawnik": {
    "command": "/ABS/ŚCIEŻKA/prawnik-mcp/.venv/bin/prawnik-mcp",
    "args": ["--data", "/ABS/ŚCIEŻKA/prawnik-mcp/data", "serve"] } } }
```

Serwer używa oficjalnego SDK `mcp==2.2.0`. Transport stdio działa: test uruchamia go jako podproces. Zgodność z konkretnymi klientami desktopowymi **nie została sprawdzona** (patrz raport jakości).

Model: analizę prawną wykonuje model użytkownika (zalecany możliwie silny model). Serwer sam nie wywołuje żadnego modelu.

## Granice (ważne)

- **MCP nie wymusza zachowania klienta.** Serwer dostarcza dowody, walidatory i procedurę. Nie może zmusić klienta AI do wywołania `check_citations`, wykonania osobnego przeglądu ani pokazania raportu. Blokuje tylko to, co kontroluje: eksport pisma przez `render_document`. Tekst wygenerowany przez model poza tym narzędziem nie przechodzi przez żadną bramkę.
- Wyniki przeglądu semantycznego przekazane przez klienta (`client_review`) są zapisywane jako **zgłoszone przez klienta**. `reviewer_type=llm` nie oznacza przeglądu przez prawnika.
- **Wersje w czasie:** korpus zawiera bieżące teksty jednolite, bez historii brzmień. Tekst jednolity może obejmować zmiany jeszcze nieobowiązujące (KC: Dz.U. 2026 poz. 507, data w ELI 2028-11-01). Zmiany są przypisane do całego aktu, nie do artykułów. Dlatego dla większości dat zdarzenia wynik to `temporal_unknown` z uzasadnieniem, a nie ciche założenie.
- **Prywatność:** lokalny MCP nie oznacza lokalnego przetwarzania. Fakty wpisane do klienta AI trafiają do dostawcy modelu, a serwer nie może tego zmienić. Moduł `privacy.py` (pseudonimizacja i przywracanie) jest przeznaczony dla przyszłego kontrolowanego runnera i w trybie MCP nie jest wywoływany. Pseudonimizacja nie gwarantuje anonimowości. Logi nie zawierają treści zapytań ani faktów. Eksporty i raporty są w `data/`.
- Treść źródeł jest traktowana jako dane, nie polecenia.
- Poza zakresem: pozwy, apelacje, kasacje, terminy procesowe, przedawnienie, odsetki (kwoty), podatki, sprawy karne, rodzinne, migracyjne, nieruchomości, prywatne PDF/OCR.

## Struktura

```
src/prawnik_mcp/contracts.py   wspólne modele i statusy     store.py      SQLite + FTS5 + snapshoty
src/prawnik_mcp/connectors/    ELI, SAOS, Cellar + PoliteClient (allowlista, Retry-After, SSRF)
src/prawnik_mcp/parsers/       PDF TJ (indeksy górne), SAOS JSON, Cellar XHTML
src/prawnik_mcp/evidence/      check_citations, temporal_status_for
src/prawnik_mcp/documents/     szablony, walidacja, eksport MD/DOCX, bramka eksportu
src/prawnik_mcp/workflow/      procedura klienta, prompt recenzenta, walidator analizy
src/prawnik_mcp/service.py     logika narzędzi      mcp_server/server.py   serwer MCP     cli.py
src/prawnik_mcp/templates/  examples/  evals/  docs/  scripts/
```

Dokumenty: [architektura (ADR 0001)](docs/adr/0001-architecture.md), [źródła i warunki danych](docs/sources.md), [raport jakości](docs/quality.md), [evals](evals/README.md).

## Licencje

Kod: MIT (`LICENSE`). Dane nie są objęte licencją kodu i nie są redystrybuowane poza małymi próbkami testowymi. Akty normatywne i dokumenty urzędowe są wyłączone z prawa autorskiego (art. 4 pr. aut.). Warunki baz danych SAOS i Cellar opisuje `docs/sources.md` (częściowo niezweryfikowane). Przy wykorzystaniu danych podawaj źródła: Dziennik Ustaw (API ELI Kancelarii Sejmu), SAOS, EUR-Lex/Cellar.
