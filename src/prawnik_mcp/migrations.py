"""SQLite schema migrations for the local store, driven by `PRAGMA user_version`.

Version 1 is the original 0.1.0 schema (created with `CREATE ... IF NOT EXISTS`, user_version 0).
Each migration runs once, in its own transaction, and bumps user_version.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS sources (source_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (snapshot_id TEXT PRIMARY KEY, source_id TEXT, url TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, kind TEXT, snapshot_id TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provisions (
    provision_id TEXT PRIMARY KEY, document_id TEXT, locator TEXT, version_id TEXT,
    snapshot_id TEXT, json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS provisions_doc ON provisions(document_id, locator);
CREATE TABLE IF NOT EXISTS judgments (document_id TEXT PRIMARY KEY, snapshot_id TEXT, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS case_numbers (case_number_norm TEXT, document_id TEXT);
CREATE INDEX IF NOT EXISTS case_numbers_idx ON case_numbers(case_number_norm);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    ref UNINDEXED, kind UNINDEXED, document_id UNINDEXED, title, body,
    tokenize = 'unicode61 remove_diacritics 2');
"""


def _source_case_sql(column: str) -> str:
    """SQL CASE mapping a document id prefix to its catalog source id."""
    from prawnik_mcp import sources

    whens = " ".join(
        f"WHEN {column} LIKE '{s.id_prefix}%' THEN '{s.source_id}'" for s in sources.catalog().values()
    )
    return f"CASE {whens} ELSE NULL END"


def _v2(db: sqlite3.Connection) -> None:
    """source_id columns, url index, sync checkpoints, HTTP cache, FTS with source_id."""
    for table in ("documents", "provisions", "judgments"):
        db.execute(f"ALTER TABLE {table} ADD COLUMN source_id TEXT")
        db.execute(f"UPDATE {table} SET source_id = {_source_case_sql('document_id')}")
        db.execute(f"CREATE INDEX IF NOT EXISTS {table}_source ON {table}(source_id)")
    db.execute("CREATE INDEX IF NOT EXISTS snapshots_url ON snapshots(url)")
    db.execute("""CREATE TABLE IF NOT EXISTS sync_state (
        source_id TEXT NOT NULL, scope TEXT NOT NULL, cursor TEXT, done INTEGER NOT NULL DEFAULT 0,
        items INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        PRIMARY KEY (source_id, scope))""")
    db.execute("""CREATE TABLE IF NOT EXISTS http_cache (
        key TEXT PRIMARY KEY, source_id TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, body TEXT NOT NULL)""")
    db.execute("""CREATE VIRTUAL TABLE fts_v2 USING fts5(
        ref UNINDEXED, kind UNINDEXED, document_id UNINDEXED, source_id UNINDEXED, title, body,
        tokenize = 'unicode61 remove_diacritics 2')""")
    db.execute(f"""INSERT INTO fts_v2(ref, kind, document_id, source_id, title, body)
        SELECT ref, kind, document_id, {_source_case_sql('document_id')}, title, body FROM fts""")
    db.execute("DROP TABLE fts")
    db.execute("ALTER TABLE fts_v2 RENAME TO fts")


def _v3(db: sqlite3.Connection) -> None:
    """Citation edges (judgment -> statute / judgment), backfilled from stored document metadata."""
    from prawnik_mcp.citation_graph import edges_for
    from prawnik_mcp.contracts import LegalDocument

    db.execute("""CREATE TABLE IF NOT EXISTS citations (
        src TEXT NOT NULL, target TEXT NOT NULL, target_locator TEXT, kind TEXT NOT NULL, raw TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS citations_src ON citations(src)")
    db.execute("CREATE INDEX IF NOT EXISTS citations_target ON citations(target, target_locator)")
    for (js,) in db.execute("SELECT json FROM documents").fetchall():
        for e in edges_for(LegalDocument.model_validate_json(js)):
            db.execute("INSERT INTO citations VALUES (?,?,?,?,?)", (e.src, e.target, e.locator, e.kind, e.raw))


def _v4(db: sqlite3.Connection) -> None:
    """Rebuild citation edges (parser fixes: 'Nr N poz.' without comma, 'art. 171(1)')."""
    db.execute("DELETE FROM citations")
    _v3(db)


def _v5(db: sqlite3.Connection) -> None:
    """Embeddings for the optional semantic search (empty unless `prawnik-mcp embed` was run)."""
    db.execute("""CREATE TABLE IF NOT EXISTS embeddings (
        ref TEXT NOT NULL, chunk INTEGER NOT NULL, model TEXT NOT NULL, kind TEXT, document_id TEXT,
        digest TEXT NOT NULL, text TEXT NOT NULL, vec BLOB NOT NULL, PRIMARY KEY (ref, chunk, model))""")
    db.execute("CREATE INDEX IF NOT EXISTS embeddings_model ON embeddings(model)")


def _v6(db: sqlite3.Connection) -> None:
    """Per-day request counters for quota-limited registry APIs (VAT white list: search, check).

    `day` is the Europe/Warsaw calendar day (the upstream quota resets at 0:00 Polish time)."""
    db.execute("""CREATE TABLE IF NOT EXISTS registry_quota (
        day TEXT NOT NULL, endpoint TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, endpoint))""")


MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {2: _v2, 3: _v3, 4: _v4, 5: _v5, 6: _v6}
LATEST = max(MIGRATIONS)


def migrate(db: sqlite3.Connection) -> int:
    """Bring the database to LATEST. Returns the resulting schema version."""
    db.executescript(SCHEMA_V1)
    version = db.execute("PRAGMA user_version").fetchone()[0] or 1
    for target in sorted(v for v in MIGRATIONS if v > version):
        try:
            db.execute("BEGIN")
            MIGRATIONS[target](db)
            db.execute(f"PRAGMA user_version = {target}")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        version = target
    return version
