# prawnik-mcp

An MCP server that gives AI assistants exact, versioned text of Polish and EU law, and a checker that rejects
quotes the sources do not contain. Statutes come from the Sejm ELI API, EU acts from EUR-Lex (Cellar), and judgments
and decisions from SAOS, the Supreme Court, the Constitutional Tribunal, KIO, UODO, EUREKA and CBOSA.

[![CI](https://github.com/LK-Technology/prawnik-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/LK-Technology/prawnik-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Polski](README.pl.md) · [Sources](docs/sources.md) · [Architecture](docs/architecture.md) · [Quality](docs/quality.md) · [Changelog](CHANGELOG.md)

> [!IMPORTANT]
> This is a research tool, not legal advice, and it has not been reviewed by a lawyer. It is not affiliated with
> the Chancellery of the Sejm, the EU Publications Office, ICM (SAOS) or any authority whose data it reads.

## What it looks like

Ask for a provision as of a date. The answer carries the consolidated text it came from, the snapshot hash, and an
explicit status when the wording for that date cannot be established:

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

Before answering, the assistant checks its quotes. A real quote passes; an invented one ("30 dni") is rejected:

```python
check_citations(
    claims=[{"claim_id": "c1", "type": "law", "evidence_ids": ["e1", "e2"],
             "text": "Konsument ma 14 dni na odstąpienie."}],
    evidence=[
        {"evidence_id": "e1", "document_id": "eli:DU/2014/827", "locator": "art. 27",
         "quote": "może w terminie 14 dni odstąpić od niej bez podawania przyczyny"},
        {"evidence_id": "e2", "document_id": "eli:DU/2014/827", "locator": "art. 28",
         "quote": "może w terminie 30 dni odstąpić od niej"},
    ])
# e1: verified_exact (art. 27)   e2: mismatch   -> critical error, letter export blocked
```

## Quick start

Requires Python 3.12+. There is no PyPI release yet; install from GitHub:

```bash
uv tool install git+https://github.com/LK-Technology/prawnik-mcp    # or: pipx install git+…
prawnik-mcp sync        # small starter corpus: Civil Code, Consumer Rights Act, Directive 2011/83/EU, sample judgments
```

Add the server to your MCP client:

```json
{
  "mcpServers": {
    "prawnik": { "command": "prawnik-mcp", "args": ["serve"] }
  }
}
```

<details>
<summary>Claude Code, Cursor, custom data directory</summary>

```bash
claude mcp add prawnik -- prawnik-mcp serve
```

Cursor uses the same JSON in `.cursor/mcp.json`. If the client does not inherit your shell `PATH`, use the absolute
path from `which prawnik-mcp`. Data is stored in the per-user data directory; override it with
`prawnik-mcp --data DIR serve` or `PRAWNIK_MCP_DATA`. Only Claude Code has been tested end to end so far.
</details>

A first prompt to try: *"Kupiłem kurtkę online 14 września, odebrałem ją 17. Czy mogę jeszcze odstąpić od umowy?
Podaj przepisy z wersją i sprawdź cytaty."*

## Tools

<!-- tools:start -->
| Tool | What it does | Main arguments |
|---|---|---|
| `search_legal` | Search statutes, judgments and decisions by identifier or by a description of the problem. | `query`, `kinds`, `filters`, `relevant_date`, `cursor`, `limit`, `live` |
| `get_legal_document` | Return the exact text of a provision or a judgment with its version and provenance. | `document_id`, `locator`, `as_of`, `snapshot_id`, `cursor`, `live` |
| `check_citations` | Verify that quoted sources exist and that each quote matches the cited provision and version. | `claims`, `evidence`, `relevant_date`, `binding`, `client_review` |
| `get_document_template` | Describe one of the three letter templates: fields, qualifying questions, exclusions and sources. | `template_id` |
| `render_document` | Render a draft letter (Markdown + DOCX) and a separate sources report. | `template_id`, `facts`, `draft`, `report_id` |
| `sources_status` | Report what the local corpus covers and the status of every source. | — |
| `get_citations` | List what a document cites and which local documents cite it. | `document_id`, `direction`, `locator`, `limit`, `cursor` |
| `list_act_versions` | Show the version timeline of a Polish act. | `document_id`, `live` |
| `lookup_entity` | Look up a company, foundation or other entity in public registers by NIP, REGON, KRS number or EU VAT number (no search by name or PESEL). | `identifier`, `date`, `bank_account`, `requester_vat`, `include_full_history`, `include_vat` |
| `compute_deadline` | Compute the end of a statutory term: start_date (the triggering event, not counted), amount, unit (days\|weeks\|months\|years), regime (tax = Ordynacja podatkowa art. | `start_date`, `amount`, `unit`, `regime` |
| `exchange_rate` | NBP average exchange rate from the last business day before event_date (YYYY-MM-DD), as required by art. | `currency`, `event_date`, `table`, `purpose` |
<!-- tools:end -->

### Prompts

Step-by-step procedures the client can load (in Polish, since the sources are Polish). The task prompts take an
optional `context` argument with the user's description of the case and end with the same rules: quote only what
the tools returned, run `check_citations`, report gaps.

<!-- prompts:start -->
| Prompt | Title | What it does |
|---|---|---|
| `analysis_procedure` | Procedura analizy | Legal analysis procedure with a separate applicability review (Polish). |
| `applicability_review` | Kontrola zastosowania przepisów | Prompt for the separate reviewer pass (Polish). |
| `case_law_research` | Linia orzecznicza | Find how courts and authorities decide a legal issue, with rulings for and against. |
| `statute_as_of_date` | Brzmienie przepisu na datę | Establish the wording of a provision on the event date, or say why it cannot be established. |
| `payment_demand_response` | Odpowiedź na wezwanie do zapłaty | Assess a payment demand item by item and draft a factual reply without unlawful threats. |
| `unfair_terms_review` | Klauzule abuzywne | Review consumer contract or terms-of-service clauses against art. 385^1-385^3 of the Civil Code. |
| `consumer_claim` | Reklamacja i odstąpienie od umowy | Pick the legal basis for a consumer complaint or withdrawal and prepare the letter. |
| `gdpr_complaint` | Skarga do UODO | Assess a GDPR violation (e.g. unanswered access request) and draft a request or a complaint. |
| `uokik_notice` | Zawiadomienie do UOKiK | Decide whether a practice harms consumers collectively and draft a notice to UOKiK. |
| `tax_ruling_research` | Interpretacje podatkowe | Find tax rulings, general interpretations and tax explanations on an issue and explain their weight. |
| `counterparty_check` | Weryfikacja kontrahenta | Check a company or trader in KRS, the VAT white list and VIES, flag risks and list what registries omit. |
<!-- prompts:end -->

## Data sources

<!-- sources:start -->
| Source | Content | Status | Rate limit | Terms |
|---|---|---|---|---|
| Cellar — Publications Office of the EU (EUR-Lex): acts and CJEU case law | EU acts, EU judgments | beta | 1 req/s | [terms](https://eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html) |
| ELI API — Dziennik Ustaw (Chancellery of the Sejm) | statutes | beta | 1 req/s | [terms](https://api.sejm.gov.pl/eli_pl.html) |
| NBP — average exchange rates (api.nbp.pl) | exchange rates | beta | 1 req/s | [terms](https://api.nbp.pl/) |
| SAOS — court judgments (ICM, University of Warsaw) | judgments | beta | 1 req/s | [terms](https://www.saos.org.pl/) |
| CBOSA — administrative courts (NSA/WSA) | judgments | experimental | 0.5 req/s | [terms](https://orzeczenia.nsa.gov.pl/cbo/query) |
| EUREKA — tax interpretations (Ministry of Finance / KIS) | tax rulings | experimental | 0.5 req/s | [terms](https://www.gov.pl/web/kas/system-informacji-celno-skarbowej-eureka) |
| KIO — National Appeal Chamber (public procurement) | judgments | experimental | 1 req/s | [terms](https://orzeczenia.uzp.gov.pl/Home/Cookies) |
| KRS — National Court Register (open API of the Ministry of Justice) | company registry | experimental | 0.5 req/s | [terms](https://www.gov.pl/web/sprawiedliwosc/uruchomienie-otwartego-api-krajowego-rejestru-sadowego) |
| SN — Supreme Court rulings (sn.pl ruling database) | judgments | experimental | 0.5 req/s | [terms](https://www.sn.pl/pl/informacje/ponowne-wykorzystywanie-informacji-publicznych) |
| TK — Constitutional Tribunal (rulings: trybunal.gov.pl, IPO) | judgments | experimental | 0.5 req/s | [terms](https://trybunal.gov.pl/informacja-publiczna-media/ponowne-wykorzystywanie) |
| UODO — decisions of the President of the Personal Data Protection Office | decisions | experimental | 1 req/s | [terms](https://orzeczenia.uodo.gov.pl/) |
| VIES — EU VAT number validation (European Commission) | company registry | experimental | 1 req/s | [terms](https://ec.europa.eu/taxation_customs/vies/#/disclaimer) |
| Wykaz podatników VAT — VAT white list (Ministry of Finance) | company registry | experimental | 1 req/s | [terms](https://www.gov.pl/web/kas/api-wykazu-podatnikow-vat) |
| Portal Orzeczeń Sądów Powszechnych (common courts portal) | judgments | planned | 0.5 req/s | [terms](https://orzeczenia.ms.gov.pl/) |
| UOKiK — competition and consumer protection decisions | decisions | planned | 0.5 req/s | [terms](https://uokik.gov.pl/) |
<!-- sources:end -->

Nothing is bundled: you build the corpus locally from the official sources, and their terms apply to you
([details and known gaps](docs/sources.md)). Documents missing locally are fetched on demand; live search results are
cached for 24 hours. For larger corpora:

```bash
prawnik-mcp sync --source saos --court-type COMMON --query "przedawnienie" --limit 500
prawnik-mcp sync --source saos --since 2026-01-01 --max-gb 2      # dump API, all courts, resumable
prawnik-mcp sync --source eli --act DU/1964/16 --act DU/2018/1000
prawnik-mcp sync --source cellar --celex 32016R0679
```

### Optional: semantic ranking

```bash
uv tool install "prawnik-mcp[semantic] @ git+https://github.com/LK-Technology/prawnik-mcp"
prawnik-mcp embed            # downloads a ~220 MB model once, embeds the local corpus (about 30 records/s on a laptop)
```

With an index, `search_legal` merges the lexical ranking with the embedding ranking (reciprocal rank fusion).
`PRAWNIK_MCP_EMBED_MODEL` selects another fastembed model (for example `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`);
`PRAWNIK_MCP_SEMANTIC=0` switches it off. Re-run `embed` after `sync`; unchanged records are skipped.

## When to use it, and when not to

Use it to give an assistant primary-source text of Polish civil, consumer, tax, procurement and data-protection law
with version information, and to catch fabricated or misattributed citations.

Skip it if you need commentary or doctrine (LEX, Legalis), a complete and current mirror of all case law,
the wording of a statute on a past date (not reconstructed yet), or anything outside Polish and EU law.

## Limitations

- Statutes: only the latest consolidated text is parsed. Pending amendments are tracked per act, not per article,
  so many event dates return `temporal_unknown` rather than a guess.
- Acts without a consolidated text are stored as originally published and flagged; EU consolidated versions are
  documentation only.
- Search is lexical by default (SQLite FTS5 with simple Polish stemming). Semantic ranking is an optional extra
  (`pip install "prawnik-mcp[semantic]"`, then `prawnik-mcp embed`): it uses a small multilingual model, covers only
  what is stored locally, and its quality on legal Polish has not been measured.
- CBOSA (administrative courts) cannot be searched: its robots.txt disallows the search endpoints, so only
  documents with a known id are fetched. Paste the link of a ruling page (found with a web search such as
  `site:orzeczenia.nsa.gov.pl …`) into `get_legal_document` to fetch it.
- Case law has gaps. Constitutional Tribunal rulings after 2015 are fetched with reasoning from the Tribunal's IPO
  portal (one extra request per ruling; without it, e.g. if IPO is down, only the operative part is stored and the
  record is flagged; dissenting opinions are not included, and search by phrase covers only the operative parts),
  Supreme Court search by phrase returns unranked metadata, administrative courts can
  only be fetched by id. CJEU case law (Court of Justice and General Court, from Cellar) is found by case number,
  ECLI or CELEX, and by words in the title, parties and keywords (only when `kinds` includes `eu_judgment`);
  Cellar has no full-text search of the reasoning, so topic recall is limited. Some documents have no Polish text
  (English or French is then stored and flagged), and cited cases and legislation from Cellar metadata are not yet
  used by the citation graph.
- SAOS search often takes longer than the 8-second live budget. The server keeps the request running and caches
  the result, so asking again a moment later usually works; `prawnik-mcp sync` avoids the problem entirely.
- Source data errors (e.g. judgment dates in the future) are flagged, not corrected. Finality of judgments is mostly unknown.
- `check_citations` verifies that a quote exists in the cited version. It does not verify that the law applies.
- `compute_deadline` only does the arithmetic (event day not counted, weekend and holiday shift); when a term starts,
  postal or electronic delivery and suspensions are left to the user.
- The three letter templates cover narrow consumer situations and do not compute deadlines or interest.
- Legal accuracy has not been measured; see [docs/quality.md](docs/quality.md) for what has been tested and what has not.

## Security and privacy

- The server connects only to the hosts listed in the [source catalog](src/prawnik_mcp/sources/catalog.toml), over
  HTTPS, with per-host rate limits and an identifiable User-Agent. It does not bypass CAPTCHAs or bot challenges,
  and endpoints that a source disallows in robots.txt are not used (CBOSA search).
- There is no telemetry. Case facts are not logged. Everything is stored in the local data directory.
- `PRAWNIK_MCP_OFFLINE=1` disables all network access.
- Your MCP client and model provider still receive whatever you type into the assistant. Running the server
  locally does not make a hosted model local.
- Source texts are treated as data, never as instructions.

Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).

## Development

```bash
git clone https://github.com/LK-Technology/prawnik-mcp && cd prawnik-mcp
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q          # offline; the network is blocked in tests
```

Parser fixes, new sources and evaluation cases are the most useful contributions. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first (no personal data, no invented legal text, polite scraping,
licence-compatible code).

## License

MIT for the code. Legal texts and judgments are not covered by this licence; the terms of each source are listed in
[docs/sources.md](docs/sources.md). Third-party code notices are in [NOTICE](NOTICE).

Developed by [LK Technology](https://lktech.pl).
