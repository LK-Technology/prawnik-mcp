# Quality report — v0.1.0 (2026-09-26)

**Experimental release.** No legal validation has been performed: no lawyer has reviewed the code, the templates or the evaluation cases.

## Verified

| Check | Result |
|---|---|
| `pytest -m "not online"` (no network, no LLM key) | 189 passed, 1 skipped (the online smoke test runs only with `PRAWNIK_ONLINE=1`) |
| Full path over MCP with an in-process client, for all 3 templates: search, exact text, claims, `check_citations`, `render_document` | pass. Changing the facts after the check → `blocked`. An unfaithful quote → critical error and `blocked`. |
| stdio server run as a subprocess, listing and calling tools | pass |
| Live sync | Civil Code, consumer rights act, Directive 2011/83/EU, 30 SAOS judgments. A second run reuses the stored snapshots. |
| `evals/run_offline.py` on the offline corpus | 19 machine checks pass. 1 is not run, because it needs network fault injection. |
| Real client | Claude Code (`claude mcp add` → Connected). One fictional case in a headless session used all 6 tools. |
| DOCX look | Checked visually on macOS Quick Look previews only, not in Word or LibreOffice. |

Footprint of the offline corpus (1,407 provisions, 9 judgments):
- sync takes about 4.5 s;
- the SQLite database is about 9 MB, plus 2.5 MB of snapshots;
- a search takes under 1 ms.

## Not verified

- **Evaluation set:** 80 labelled cases (40 dev + 40 held-out). Only 24 AI-generated candidate cases exist.
- **Legal accuracy:** zero critical legal errors, 100% support of claims by their sources, and the useful-answer rate have not been measured. That would need model runs and a lawyer.
- **Retrieval:** Recall@10 on hand-labelled relevant sources has not been measured.
- **Clients:** a second real client (Claude Desktop, Cursor, a GPT client) has not been tested.
- **Comparison:** no-MCP vs plain retrieval vs the full workflow has not been measured.
- **Data terms:** the reuse terms of SAOS and Cellar are only partly checked.

## Known technical limits

- **Statute versions:**
  - Only the latest consolidated text is held; there is no history of wordings. `valid_from` and `valid_to` are empty.
  - Pending changes are tracked per act, not per article. As a result, most event dates return `temporal_unknown`.
- **EU law:** directives are stored in their original Official Journal wording, and there is no CJEU case law yet.
- **SAOS:** only a small topical sample. Finality of judgments is unknown. Data errors are flagged, not corrected.
- **Parsing and search:**
  - Superscript restoration is heuristic and was checked on two PDFs only.
  - Full-text search truncates word endings instead of lemmatising, and there is no semantic search.
  - Extracting a § / ust. / pkt from an article is heuristic. When it fails, the tool returns the whole article with a warning.
