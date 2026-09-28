# prawnik-mcp

Serwer MCP, który daje asystentom AI dokładne, wersjonowane teksty prawa polskiego i unijnego oraz walidator
odrzucający cytaty, których nie ma w źródłach. Ustawy pochodzą z API ELI Sejmu, akty UE z EUR-Lex (Cellar), a
orzeczenia i decyzje z SAOS, KIO, UODO, EUREKA i CBOSA.

[![CI](https://github.com/LK-Technology/prawnik-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/LK-Technology/prawnik-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![Licencja: MIT](https://img.shields.io/badge/licencja-MIT-green)](LICENSE)

[English](README.md) · [Źródła](docs/sources.md) · [Architektura](docs/architecture.md) · [Jakość](docs/quality.md)

> [!IMPORTANT]
> To narzędzie badawcze, a nie porada prawna; nie było sprawdzane przez prawnika. Projekt nie jest powiązany z
> Kancelarią Sejmu, Urzędem Publikacji UE, ICM (SAOS) ani z żadnym organem, którego dane odczytuje.

## Jak to wygląda

Przepis na konkretną datę. Odpowiedź mówi, z którego tekstu jednolitego pochodzi, podaje hash snapshotu i jawny
status, gdy brzmienia na tę datę nie da się ustalić:

```console
$ prawnik-mcp get eli:DU/2014/827 "art. 27" --as-of 2026-09-20
{
  "status": "temporal_unknown",
  "data": {
    "locator": "art. 27",
    "version_label": "tekst jednolity Dz.U. 2026 poz. 1244, stan prawny na 2026-09-02",
    "snapshot_id": "eli:0be910651325e9f0",
    "text": "Art. 27. 1. Konsument, który zawarł umowę na odległość lub poza lokalem przedsiębiorstwa, może w terminie 14 dni odstąpić od niej bez podawania przyczyny …"
  },
  "warnings": ["tekst jednolity obejmuje zmianę Dz.U. 2025 poz. 1172 – część przepisów wchodzi w życie 2027-03-01 …"]
}
```

Przed odpowiedzią asystent sprawdza cytaty: prawdziwy przechodzi, wymyślony („30 dni”) zostaje odrzucony
(`verified_exact` dla art. 27, `mismatch` dla drugiego cytatu, eksport pisma zablokowany).

## Szybki start

Python 3.12+. Wersji na PyPI jeszcze nie ma; instalacja z GitHuba:

```bash
uv tool install git+https://github.com/LK-Technology/prawnik-mcp    # albo: pipx install git+…
prawnik-mcp sync        # korpus startowy: KC, ustawa o prawach konsumenta, dyrektywa 2011/83/UE, próbka orzeczeń
claude mcp add prawnik -- prawnik-mcp serve                          # Claude Code
```

Inne klienty (Claude Desktop, Cursor), w konfiguracji MCP:

```json
{ "mcpServers": { "prawnik": { "command": "prawnik-mcp", "args": ["serve"] } } }
```

## Narzędzia

<!-- tools:start -->
| Narzędzie | Co robi (opis widoczny dla modelu) | Główne argumenty |
|---|---|---|
| `search_legal` | Search statutes, judgments and decisions by identifier or by a description of the problem. | `query`, `kinds`, `filters`, `relevant_date`, `cursor`, `limit`, `live` |
| `get_legal_document` | Return the exact text of a provision or a judgment with its version and provenance. | `document_id`, `locator`, `as_of`, `snapshot_id`, `cursor`, `live` |
| `check_citations` | Verify that quoted sources exist and that each quote matches the cited provision and version. | `claims`, `evidence`, `relevant_date`, `binding`, `client_review` |
| `get_document_template` | Describe one of the three letter templates: fields, qualifying questions, exclusions and sources. | `template_id` |
| `render_document` | Render a draft letter (Markdown + DOCX) and a separate sources report. | `template_id`, `facts`, `draft`, `report_id` |
| `sources_status` | Report what the local corpus covers and the status of every source. | — |
| `get_citations` | List what a document cites and which local documents cite it. | `document_id`, `direction`, `locator`, `limit`, `cursor` |
| `list_act_versions` | Show the version timeline of a Polish act. | `document_id`, `live` |
<!-- tools:end -->

## Źródła danych

<!-- sources:start -->
| Źródło | Zawartość | Status | Limit zapytań | Warunki |
|---|---|---|---|---|
| Cellar — Publications Office of the EU (EUR-Lex) | akty UE | beta | 1 req/s | [warunki](https://eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html) |
| ELI API — Dziennik Ustaw (Chancellery of the Sejm) | ustawy | beta | 1 req/s | [warunki](https://api.sejm.gov.pl/eli_pl.html) |
| SAOS — court judgments (ICM, University of Warsaw) | orzeczenia | beta | 1 req/s | [warunki](https://www.saos.org.pl/) |
| CBOSA — administrative courts (NSA/WSA) | orzeczenia | eksperymentalne | 0.5 req/s | [warunki](https://orzeczenia.nsa.gov.pl/cbo/query) |
| EUREKA — tax interpretations (Ministry of Finance / KIS) | interpretacje podatkowe | eksperymentalne | 0.5 req/s | [warunki](https://www.gov.pl/web/kas/system-informacji-celno-skarbowej-eureka) |
| KIO — National Appeal Chamber (public procurement) | orzeczenia | eksperymentalne | 1 req/s | [warunki](https://orzeczenia.uzp.gov.pl/Home/Cookies) |
| UODO — decisions of the President of the Personal Data Protection Office | decyzje | eksperymentalne | 1 req/s | [warunki](https://orzeczenia.uodo.gov.pl/) |
| Portal Orzeczeń Sądów Powszechnych (common courts portal) | orzeczenia | planowane | 0.5 req/s | [warunki](https://orzeczenia.ms.gov.pl/) |
| UOKiK — competition and consumer protection decisions | decyzje | planowane | 0.5 req/s | [warunki](https://uokik.gov.pl/) |
<!-- sources:end -->

Repozytorium nie zawiera korpusu: budujesz go lokalnie z oficjalnych źródeł i obowiązują Cię ich warunki
([szczegóły i znane luki](docs/sources.md)). Brakujące dokumenty są pobierane na żądanie, wyniki wyszukiwania na żywo
trafiają do cache na 24 h. Większe zbiory: `prawnik-mcp sync --source saos --court-type SUPREME --limit 500`,
`--source eli --act DU/1964/16`, `--source cellar --celex 32016R0679` (z wznawianiem po przerwaniu).

## Kiedy się przyda, a kiedy nie

Przyda się, gdy asystent ma pracować na tekstach źródłowych prawa cywilnego, konsumenckiego, podatkowego,
zamówień publicznych i ochrony danych, z wersją przepisu, i gdy trzeba wyłapać zmyślone lub źle przypisane cytaty.

Nie przyda się do komentarzy i doktryny (LEX, Legalis), kompletnego i bieżącego zbioru orzecznictwa, brzmienia
ustawy na dawną datę (jeszcze nieodtwarzane) ani spraw spoza prawa polskiego i unijnego.

## Ograniczenia

- Ustawy: parsowany jest tylko najnowszy tekst jednolity; zmiany oczekujące są śledzone dla całego aktu, więc dla
  wielu dat wynik to `temporal_unknown` zamiast zgadywania.
- Akty bez tekstu jednolitego są zapisywane w brzmieniu ogłoszonym i oznaczane; konsolidacje UE mają charakter
  dokumentacyjny.
- Wyszukiwanie leksykalne (SQLite FTS5 z prostą obsługą odmiany), bez wyszukiwania semantycznego.
- CBOSA nie jest przeszukiwana (robots.txt zabrania dostępu do wyszukiwarki); pobierane są tylko dokumenty o znanym id.
- Błędy danych źródeł (np. daty z przyszłości) są oznaczane, nie poprawiane; prawomocność orzeczeń jest zwykle nieznana.
- `check_citations` sprawdza, czy cytat istnieje we wskazanej wersji. Nie ocenia, czy przepis ma zastosowanie.
- Trzy szablony pism dotyczą wąskich spraw konsumenckich i nie liczą terminów ani odsetek.
- Trafność prawna nie była mierzona. Co sprawdzono, a czego nie: [docs/quality.md](docs/quality.md).

## Bezpieczeństwo i prywatność

Serwer łączy się wyłącznie z hostami z [katalogu źródeł](src/prawnik_mcp/sources/catalog.toml), po HTTPS, z limitami
zapytań i identyfikowalnym User-Agentem; nie obchodzi CAPTCHA ani zabezpieczeń anty-botowych i nie używa ścieżek
zablokowanych w robots.txt. Nie ma telemetrii, faktów spraw się nie loguje, dane leżą w lokalnym katalogu.
`PRAWNIK_MCP_OFFLINE=1` wyłącza sieć. Dostawca modelu nadal otrzymuje to, co wpiszesz do asystenta.

## Rozwój

```bash
git clone https://github.com/LK-Technology/prawnik-mcp && cd prawnik-mcp
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Zasady współtworzenia: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencja

Kod: MIT. Teksty prawne i orzeczenia nie są objęte licencją kodu; warunki źródeł: [docs/sources.md](docs/sources.md).
Atrybucje: [NOTICE](NOTICE).

Rozwijane przez [LK Technology](https://lktech.pl).
