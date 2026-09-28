# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems or accidental exposure of personal data.
Use GitHub's private vulnerability reporting ("Report a vulnerability" on the Security tab).
We aim to acknowledge reports within 7 days.

## Scope

- SSRF, redirect or allowlist bypasses in `connectors/http.py`.
- Path traversal in the local store, report or export paths.
- Prompt-injection paths where source text could be executed as instructions.
- Leaks of case facts into logs or files outside the configured data directory.
- Personal data committed to the repository or shipped in packages.

## Data handling

The server stores everything locally in the data directory (`$PRAWNIK_MCP_DATA` or the per-user data dir). It does not send case facts anywhere; your MCP client and model provider may.
