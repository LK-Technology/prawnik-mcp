# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **CJEU case law (Cellar):** judgments, orders and Advocate General opinions of the Court of Justice and the General
  Court as `celex:6…` records with the Polish text (XHTML, else legacy HTML; English/French fallback is flagged).
  Lookup by case number (`C-260/18`, `T-123/20`), CELEX and ECLI; title-word search through Virtuoso `bif:contains`
  when `kinds` includes `eu_judgment` (no full-text search of the reasoning exists); `sync --source cellar --celex 6…`.
  Metadata: date, ECLI, parties, keywords, cited cases and legislation (not yet in the citation graph).
- **Sources:** SN (Supreme Court rulings from sn.pl, current; SAOS stops in 2016), TK (Constitutional Tribunal
  rulings from trybunal.gov.pl and, with the reasoning, from IPO; SAOS stops in 2015), EUREKA (tax interpretations), KIO (procurement rulings), UODO (data protection decisions),
  CBOSA (administrative courts; single documents only — robots.txt honoured); EU consolidated versions from Cellar.
- **Hybrid access:** live search across sources with a time budget and 24 h cache, lazy fetch of documents missing
  locally, `PRAWNIK_MCP_OFFLINE=1`.
- **Bulk sync:** `prawnik-mcp sync --source saos|eli|cellar …` with `--query/--court-type/--since/--until/--limit/--max-gb`,
  checkpoint/resume per page (SAOS dump and search modes).
- **Tools:** `get_citations` (citation graph judgment → statute/article/judgment, incoming and outgoing),
  `list_act_versions` (consolidated texts and pending amendments).
- **ELI:** acts without a consolidated text are stored as published text (flagged); parser handles small-print annex
  markers (Kodeks karny), letter superscripts (art. 18^3a), quoted articles in amending acts; online golden test on
  20 major acts.
- Source catalog (`sources/catalog.toml`), connector registry, store migrations (schema v3: source ids, citations,
  sync state, HTTP cache, WAL), README sources and tools tables generated from the catalog and the server
  (checked in CI).
- CI (lint, offline tests on Linux/macOS × Python 3.12/3.13, wheel content check, stdio smoke test), nightly online smoke, release workflow.
- **Prompts:** eight task procedures (case-law research, wording on a date, reply to a payment demand, unfair
  terms, consumer claims, GDPR complaint, UOKiK notice, tax rulings), each with an optional `context` argument;
  README prompts table generated from the server.
- **Company and tax tools:** `lookup_entity` (KRS open API, VAT white list with a persistent quota guard and
  bank-account check, VIES; NIP/REGON/KRS/NRB checksums; personal data masked, free-text PESEL scrubbed),
  `compute_deadline` (Ordynacja podatkowa art. 12, KC art. 111–115, KPA art. 57, statutory days off),
  `exchange_rate` (NBP average rate from the last business day before the event, art. 31a VAT / art. 11a PIT);
  EUREKA now includes general interpretations and tax explanations with an authority note; aliases for tax,
  company and procedural codes; prompt `counterparty_check`. Catalog field `role = "lookup"`; schema v6.
- **Optional semantic ranking:** `pip install "prawnik-mcp[semantic]"` and `prawnik-mcp embed` build a local embedding
  index (fastembed, multilingual MiniLM); `search_legal` fuses it with FTS5 by reciprocal rank fusion. Schema v5.
- `scripts/pii_scan.py` (PESEL, bank accounts, phones, e-mails, local denylist, forbidden paths) and pre-commit hooks.
- Network guard for offline tests (`tests/conftest.py`).

### Fixed
- Live search: results are interleaved across sources instead of concatenated, so one source cannot fill the page.
- EUREKA: multi-word queries use the all-words mode first and results are filtered by the thesis, because the
  any-word mode returned most of the database newest first.
- A source that misses the live time budget finishes in the background and its result is cached for the next call.
- An HTML page served instead of a JSON API response (e.g. SAOS maintenance) is reported as the source being unavailable.
- Documented SAOS coverage: Supreme Court rulings end in 2016, Constitutional Tribunal rulings in 2015.

### Changed
- Templates are package data (`prawnik_mcp/templates`); default data directory is the per-user data dir.
- Tool descriptions, server instructions and docs in English; prompts renamed to `analysis_procedure` and `applicability_review`.

## [0.1.0] - 2026-09-26

### Added
- MCP server (stdio) with `search_legal`, `get_legal_document`, `check_citations`, `get_document_template`, `render_document`, `sources_status`.
- Connectors: ELI (consolidated texts from PDF), SAOS (sample), Cellar (EU acts); SQLite FTS5 store with snapshots.
- Three civil/consumer letter templates with Markdown/DOCX export and an export gate bound to a citation report.
