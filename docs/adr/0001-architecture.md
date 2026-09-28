# ADR 0001 — Own small connectors with provenance; no proxying of third-party MCP servers

Date: 2026-09-26. Status: accepted.

## Context

Two open-source Polish legal MCP servers existed:

| Project | What it is | Assessment |
|---|---|---|
| `matematicsolutions/prawo-pl-mcp` (Apache-2.0) | A thin proxy that spawns about 10 connectors via `npx -y` / `uvx`, with unpinned versions. | Running unreviewed, unpinned packages is unacceptable for a tool that handles legal matters. It also does not keep snapshots, hashes or statute versions. |
| `ItsRaelx/LawForAi` (MIT) | Thin httpx clients for ELI, SAOS, Cellar and the Sejm API. | Useful as knowledge of the endpoints. It has no User-Agent, no rate limiting or retries, and no provenance. It depends on PyMuPDF, which is AGPL. |

## Decision

Write small, auditable connectors (ELI, SAOS, Cellar to start) with:
- **provenance:** a snapshot with sha256, URL and fetch time for every response;
- **polite HTTP:** a host allowlist, SSRF guard, rate limiting and `Retry-After` handling;
- **explicit versioning of statutes:** which consolidated text a quote comes from, its state-of-law date, and pending changes.

The parameter mapping learned from LawForAi is credited in `NOTICE`. `pypdf` (BSD) is used instead of PyMuPDF.

## Consequences

- Every answer can cite the exact snapshot and version of the text it was taken from.
- New sources cost one connector module each. Maintained MIT/Apache connectors can be ported with attribution; AGPL code cannot be used.
