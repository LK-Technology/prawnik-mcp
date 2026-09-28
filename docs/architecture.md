# Architecture

```
public source ──polite HTTP──▶ raw snapshot (sha256, URL, fetched_at, parser version)
                                   │
                                   ▼
                        parser (per source) ──▶ SQLite: documents · provisions · judgments
                                                   · citations · FTS5 index · sync state · cache
                                   ▲                          │
live search / lazy fetch ──────────┘                          ▼
                                                        MCP tools (stdio)
```

- **Connectors** (`src/prawnik_mcp/connectors/`): one module per source, registered in `registry.py` and
  described in `sources/catalog.toml`. The catalog sets the hosts the HTTP client may contact, the per-host rate
  limit, terms, attribution and maturity.
- **HTTP** (`connectors/http.py`):
  - host allowlist, re-checked on every redirect;
  - SSRF guard and response size cap;
  - identifiable User-Agent, per-host rate limit, retries with backoff honouring `Retry-After`;
  - anti-bot challenges are never bypassed.
- **Store** (`store.py`, `migrations.py`):
  - one SQLite file in WAL mode;
  - raw snapshots on disk, addressed by hash;
  - schema versions managed through `PRAGMA user_version`.
- **Versions.** Polish acts are read from the latest *obwieszczenie* (consolidated text), which gives:
  - the state-of-law date;
  - amendments that are included but not yet in force (`pending_changes`);
  - transitional provisions the notice leaves out.

  Acts with no consolidated text are stored as their published text and flagged. For EU acts, both the Official
  Journal text and the latest consolidated version are kept.
- **Evidence** (`evidence/`):
  - `check_citations` compares quotes with the stored text of the cited version (exact, then normalised);
  - it checks case numbers against court and date;
  - it produces a report id bound to the facts of a letter.
- **Live access** (`live.py`):
  - searches connectors in parallel under a time budget;
  - caches results for 24 h;
  - fetches documents the local store lacks.
- **The MCP boundary.** The server cannot make a client follow the analysis procedure or show the report. It blocks
  only what it controls: exporting a filled letter.
