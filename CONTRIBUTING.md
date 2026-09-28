# Contributing

Thanks for helping! This project handles legal information, so correctness and honesty matter more than features.

## Ground rules

- **Never commit private data.** No case files, real parties, PESEL, bank accounts, phone numbers or e-mails. `data/` is ignored; keep it that way. Run `python scripts/pii_scan.py` (also a pre-commit hook).
- **No invented legal text.** Tests and fixtures use real, public source material (acts, anonymised judgments) or clearly labelled fictional data.
- **Explicit statuses over silent fallbacks.** "Not found in the local corpus" is not "does not exist"; unknown versions are `temporal_unknown`.
- **Be polite to sources.** Respect rate limits and terms, identify the client (User-Agent), never bypass CAPTCHAs or WAFs.
- **Licences.** Only MIT/BSD/Apache-2.0 compatible code. No AGPL/GPL code. Keep original notices for ported code and list them in `NOTICE`.

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install            # optional: pip install pre-commit
.venv/bin/ruff check . && .venv/bin/pytest -q
.venv/bin/prawnik-mcp --data ./data sync --offline   # local corpus from recorded samples
```

Offline tests must not touch the network (enforced in `tests/conftest.py`). Tests that do are marked `@pytest.mark.online` and run nightly with `PRAWNIK_ONLINE=1`.

## Adding a data source

1. Check access and terms (official API > permitted export > permitted page fetching) and document them in `docs/sources.md`.
2. Add a connector with polite HTTP (host allowlist, rate limit, retries) and a parser returning the shared models in `contracts.py`.
3. Record small fixtures (≤1 MB each), run the PII scan on them, and add contract tests that replay them offline.
4. Report coverage and known gaps through `sources_status`.

## Pull requests

Small, focused PRs with tests. Describe user-visible changes in `CHANGELOG.md` under *Unreleased*.
