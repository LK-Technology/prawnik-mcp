# Quality report

**Experimental.** No legal validation has been performed: no lawyer has reviewed the code, templates or evaluation
cases. What follows is technical verification only.

## Automated checks

| Check | Result (2026-09-28) |
|---|---|
| Offline test suite (`pytest -m "not online"`, network blocked) | 252 passed |
| Connector contract tests (recorded real responses, mocked transport) | ELI, SAOS, Cellar, EUREKA, KIO, UODO, CBOSA |
| Online golden test of the ELI parser on 20 major acts (KC, KPC, KK, KPK, KP, KSH, VAT, Ordynacja, KPA, PPSA, …) | 20/20 pass: articles found, numbering monotonic, bounded superscript warnings |
| Full path through MCP (search → exact text → `check_citations` → `render_document`) for all 3 templates | pass; changed facts → `blocked`; unfaithful quote → critical error and `blocked` |
| Live search smoke (2026-09-28) | SAOS, ELI, KIO (≈6 s), UODO (<1 s), EUREKA (<1 s); Cellar lookup by identifier |
| Clean wheel install + stdio smoke test | pass (CI job `package`) |
| PII scan / gitleaks | 0 findings |

## Real client

Claude Code (`claude mcp add`): headless sessions used all tools on fictional cases — search, exact provisions,
citation checks, citation graph, act versions, letter rendering. One-off runs are not an evaluation of answer quality.

## Not verified

- **Legal accuracy.** Zero critical legal errors, the share of claims supported by their sources, and the rate of
  useful answers are not measured. Measuring them needs model runs and a lawyer-labelled evaluation set.
- **Retrieval.** Recall@k on hand-labelled relevant sources is not measured.
- **Evaluation set.** There are 24 AI-generated candidate cases (`evals/`). They are not a gold standard; no held-out
  set exists.
- **Other clients.** Claude Desktop, Cursor and GPT clients are expected to work over stdio but have not been tested.
- **Source terms.** Reuse terms of SAOS, Cellar, EUREKA, KIO, UODO and CBOSA are only partly verified; see
  [sources.md](sources.md).

## Known technical limits

- **Statute history.**
  - Only the latest consolidated text per act is parsed; older wordings are not reconstructed.
  - Pending changes are tracked per act, not per article, so many event dates return `temporal_unknown`.
- **Acts without a consolidated text** are stored as the original publication and flagged. They are
  `temporal_unknown` for any date after publication.
- **EU law.**
  - Cellar consolidated versions are documentation only.
  - Old acts may lack a Polish XHTML manifestation (the source answers 404).
  - There is no CJEU case law yet.
- **Search** is lexical (FTS5 with simple Polish stemming), with no semantic search. Live search in Cellar works by
  identifier only.
- **Data quality.**
  - Source data errors are flagged, not corrected: future dates in SAOS and KIO, missing dates.
  - Finality of judgments is mostly unknown.
- **CBOSA** cannot be searched automatically (robots.txt); only documents with a known id can be fetched.
