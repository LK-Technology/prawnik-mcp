# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- CI (lint, offline tests on Linux/macOS × Python 3.12/3.13, wheel content check, stdio smoke test), nightly online smoke, release workflow.
- `scripts/pii_scan.py` (PESEL, bank accounts, phones, e-mails, local denylist, forbidden paths) and pre-commit hooks.
- Network guard for offline tests (`tests/conftest.py`).

### Changed
- Templates are package data (`prawnik_mcp/templates`); default data directory is the per-user data dir.
- Tool descriptions, server instructions and docs in English; prompts renamed to `analysis_procedure` and `applicability_review`.

## [0.1.0] - 2026-09-26

### Added
- MCP server (stdio) with `search_legal`, `get_legal_document`, `check_citations`, `get_document_template`, `render_document`, `sources_status`.
- Connectors: ELI (consolidated texts from PDF), SAOS (sample), Cellar (EU acts); SQLite FTS5 store with snapshots.
- Three civil/consumer letter templates with Markdown/DOCX export and an export gate bound to a citation report.
