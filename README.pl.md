<div align="center">

# ⚖️ prawnik-mcp

**Prawo polskie i unijne dla asystentów AI — ze źródłami, które da się sprawdzić.**

Otwarty serwer [Model Context Protocol](https://modelcontextprotocol.io), dzięki któremu Claude, Cursor czy inny klient
MCP wyszukuje przepisy i orzeczenia, cytuje **dokładne brzmienie z wersją i pochodzeniem**, sprawdza cytaty przed
udzieleniem odpowiedzi i przygotowuje proste pisma — bez wymyślania prawa.

[![CI](https://github.com/OWNER/prawnik-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/prawnik-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![Licencja: MIT](https://img.shields.io/badge/licencja-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-eksperymentalny-orange)](docs/quality.md)

[Szybki start](#-szybki-start) · [Narzędzia](#-narzędzia) · [Źródła](#-źródła-danych) · [Ograniczenia](#-uczciwie-o-ograniczeniach) ·
[English 🇬🇧](README.md)

</div>

> [!IMPORTANT]
> **To nie jest porada prawna ani prawnik.** Narzędzie daje asystentowi AI sprawdzalne źródła, jawne statusy i
> walidator cytatów. Ocena, czy przepis ma zastosowanie do Twojej sprawy, wymaga osądu — najlepiej prawnika.
> Projekt nie był dotąd sprawdzany przez prawnika.

## Po co

Modele językowe piszą płynnie językiem prawniczym i pewnie mylą szczegóły: nieaktualne brzmienia, zmyślone sygnatury,
stanowisko strony przedstawione jako pogląd sądu. `prawnik-mcp` każe asystentowi pracować na **źródłach pierwotnych**:

- 📜 **Dokładny tekst i wersja** — każdy przepis z informacją, z którego tekstu jednolitego pochodzi
  (np. *Dz.U. 2026 poz. 795, stan prawny na 19.05.2026*), z hashem snapshotu i adresem źródła.
- 🕰️ **Czas** — pytasz o brzmienie na datę zdarzenia; zmiany jeszcze nieobowiązujące, przepisy przejściowe i
  nieustalone wersje są oznaczane jako `temporal_unknown`, a nie zgadywane.
- 🔎 **Kontrola cytatów** — cytat porównywany z zapisanym tekstem (dokładnie / po normalizacji / inny artykuł / inna
  wersja / brak); sygnatura sprawdzana z sądem i datą.
- 🚦 **Jawne statusy** — `not_found` w lokalnym korpusie ≠ „nie istnieje”; `source_unavailable`, `ambiguous`,
  `stale`, `out_of_scope` są raportowane wprost.
- 🧾 **Pisma z bramką** — trzy szablony (wezwanie do zapłaty, reklamacja, odstąpienie od umowy na odległość) eksportują
  się do DOCX tylko z aktualnym raportem cytatów powiązanym z faktami; inaczej powstaje jawnie niekompletny formularz.
- 🏠 **Lokalnie** — źródła trafiają do lokalnej bazy SQLite + FTS5. Bez kluczy API i GPU.

## 🚀 Szybki start

```bash
pipx install git+https://github.com/OWNER/prawnik-mcp     # po publikacji: pipx install prawnik-mcp
prawnik-mcp sync                                            # korpus startowy: KC, upk, dyrektywa 2011/83/UE, próbka orzeczeń
claude mcp add prawnik -- prawnik-mcp serve                 # Claude Code
```

Claude Desktop / Cursor — w konfiguracji MCP:

```json
{ "mcpServers": { "prawnik": { "command": "prawnik-mcp", "args": ["serve"] } } }
```

Przykładowe pytanie do asystenta:

> *Kupiłem kurtkę przez internet 14 września, odebrałem 17. Czy mogę jeszcze odstąpić od umowy? Podaj przepisy z wersją
> i sprawdź cytaty.*

## 🧰 Narzędzia

| Narzędzie | Opis |
|---|---|
| `search_legal` | Szukanie po identyfikatorze (`art. 27 upk`, `I ACa 772/13`, `Dz.U. 2024 poz. 1061`, `RODO`) albo opisie; najpierw lokalnie, w razie potrzeby **na żywo** w API źródeł. |
| `get_legal_document` | Dokładny tekst artykułu / § / ust. / pkt lub orzeczenia z wersją i snapshotem; brakujące dokumenty są **dociągane ze źródła**. |
| `check_citations` | Weryfikacja twierdzeń i cytatów; wynik to `report_id`. |
| `get_citations` | Graf powołań: co cytuje orzeczenie i które lokalne orzeczenia powołują dany przepis. |
| `list_act_versions` | Oś czasu ustawy: teksty jednolite, akty zmieniające, zmiany oczekujące. |
| `sources_status` | Zakres lokalnego korpusu, świeżość, warunki, znane luki, katalog źródeł. |
| `get_document_template` / `render_document` | Trzy pisma → DOCX + Markdown + osobny raport źródeł. |

## 📚 Źródła danych

<!-- sources:start -->
| Source | Content | Status | Polite rate | Terms |
|---|---|---|---|---|
| **Cellar — Publications Office of the EU (EUR-Lex)** | EU acts | 🔵 beta | 1 req/s | [terms](https://eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html) |
| **ELI API — Dziennik Ustaw (Chancellery of the Sejm)** | statutes | 🔵 beta | 1 req/s | [terms](https://api.sejm.gov.pl/eli_pl.html) |
| **SAOS — court judgments (ICM, University of Warsaw)** | judgments | 🔵 beta | 1 req/s | [terms](https://www.saos.org.pl/) |
| **CBOSA — administrative courts (NSA/WSA)** | judgments | ⚪ planned | 0.5 req/s | [terms](https://orzeczenia.nsa.gov.pl/cbo/query) |
| **EUREKA — tax interpretations (Ministry of Finance)** | tax rulings | ⚪ planned | 1 req/s | [terms](https://podatki.gov.pl/narzedzia/eureka) |
| **KIO — National Appeal Chamber (public procurement)** | judgments | ⚪ planned | 1 req/s | [terms](https://orzeczenia.uzp.gov.pl/) |
| **Portal Orzeczeń Sądów Powszechnych (common courts portal)** | judgments | ⚪ planned | 0.5 req/s | [terms](https://orzeczenia.ms.gov.pl/) |
| **UODO — data protection authority decisions** | decisions | ⚪ planned | 1 req/s | [terms](https://orzeczenia.uodo.gov.pl/) |
| **UOKiK — competition and consumer protection decisions** | decisions | ⚪ planned | 0.5 req/s | [terms](https://uokik.gov.pl/) |
<!-- sources:end -->

Dane pochodzą z **oficjalnych, publicznych źródeł**, pobieranych grzecznie (identyfikowalny User-Agent, limity
zapytań, `Retry-After`, bez obchodzenia CAPTCHA/WAF). **Repozytorium nie zawiera korpusu** — synchronizujesz go sam(a),
więc obowiązują Cię warunki źródeł. Szczegóły: [docs/sources.md](docs/sources.md).

```bash
prawnik-mcp sync --source saos --court-type SUPREME --query "przedawnienie" --limit 500   # orzeczenia SN
prawnik-mcp sync --source saos --since 2026-01-01 --max-gb 2        # hurtowo: wszystkie sądy za okres
prawnik-mcp sync --source eli --act DU/2018/1000                    # konkretna ustawa
prawnik-mcp sync --source cellar --celex 32016R0679                  # RODO
```

Import hurtowy zapisuje checkpoint po każdej stronie — po Ctrl-C uruchom to samo polecenie, aby wznowić.
`PRAWNIK_MCP_OFFLINE=1` wyłącza dostęp do sieci.

## ⚠️ Uczciwie o ograniczeniach

- **To nie jest porada prawna.** Kontrola cytatów sprawdza, że cytat jest prawdziwy — nie, że przepis ma zastosowanie.
- **Historia brzmień jest niepełna.** Parsowany jest najnowszy tekst jednolity; dawne brzmienia nie są odtwarzane, więc
  dla wielu dat wynik to `temporal_unknown` — celowo, zamiast zgadywać.
- **Wyszukiwanie jest leksykalne** (FTS5 z prostą obsługą odmiany), bez wyszukiwania semantycznego.
- **Pokrycie zależy od synchronizacji** i od samych źródeł (SAOS ma luki i błędy danych — są oznaczane, nie poprawiane).
- **Pisma** obejmują trzy wąskie sytuacje cywilno-konsumenckie i nie liczą terminów ani odsetek.
- **Dostawca modelu** dostaje to, co wyśle Twój klient AI. Lokalny serwer MCP nie czyni modelu lokalnym.

## 🔒 Prywatność

Serwer przechowuje dane lokalnie i nie loguje faktów spraw. Łączy się wyłącznie z publicznymi źródłami prawa.
Nie wklejaj danych osobowych do publicznych zgłoszeń (issues).

## 🤝 Współtworzenie

Zgłoszenia i PR-y są mile widziane — zwłaszcza nowe źródła, poprawki parserów i przypadki testowe.
Zasady: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencja

Kod: [MIT](LICENSE). Teksty prawne i orzeczenia nie są objęte licencją kodu — warunki poszczególnych źródeł:
[docs/sources.md](docs/sources.md). Podziękowania i atrybucje: [NOTICE](NOTICE).
