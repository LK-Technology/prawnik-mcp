"""Local store: raw snapshot files + SQLite (metadata, provisions, judgments, FTS5).

Connectors write through this API only.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from prawnik_mcp.contracts import (
    Judgment,
    LegalDocument,
    ProvisionVersion,
    Snapshot,
    SourceRecord,
    sha256_bytes,
    snapshot_id_for,
)

SCHEMA = """
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


def default_data_dir() -> Path:
    """$PRAWNIK_MCP_DATA, else the per-user data dir (e.g. ~/Library/Application Support/prawnik-mcp)."""
    env = os.environ.get("PRAWNIK_MCP_DATA")
    if env:
        return Path(env)
    from platformdirs import user_data_dir

    return Path(user_data_dir("prawnik-mcp", appauthor=False))


def normalize_case_number(s: str) -> str:
    return " ".join(s.upper().replace(" ", " ").split())


class Store:
    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.data_dir / "prawnik.sqlite3", check_same_thread=False)
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ snapshots
    def put_snapshot(
        self, source_id: str, url: str, content: bytes, content_type: str,
        parser_version: str | None = None, fetched_at: datetime | None = None,
    ) -> Snapshot:
        digest = sha256_bytes(content)
        sid = snapshot_id_for(source_id, digest)
        rel = Path("snapshots") / source_id / f"{digest}.raw"
        path = self.data_dir / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        existing = self.get_snapshot(sid)
        if existing:
            return existing
        snap = Snapshot(
            snapshot_id=sid, source_id=source_id, url=url,
            fetched_at=fetched_at or datetime.now(UTC), sha256=digest,
            content_type=content_type, size_bytes=len(content),
            parser_version=parser_version, raw_path=str(rel),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
            (sid, source_id, url, snap.model_dump_json()),
        )
        self.db.commit()
        return snap

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        row = self.db.execute("SELECT json FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        return Snapshot.model_validate_json(row[0]) if row else None

    def find_snapshot_by_url(self, url: str) -> Snapshot | None:
        row = self.db.execute("SELECT json FROM snapshots WHERE url=? ORDER BY rowid DESC LIMIT 1", (url,)).fetchone()
        return Snapshot.model_validate_json(row[0]) if row else None

    def read_snapshot_bytes(self, snapshot_id: str) -> bytes | None:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            return None
        p = self.data_dir / snap.raw_path
        return p.read_bytes() if p.exists() else None

    # ------------------------------------------------------------------ sources
    def upsert_source(self, rec: SourceRecord) -> None:
        self.db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (rec.source_id, rec.model_dump_json()))
        self.db.commit()

    def get_sources(self) -> list[SourceRecord]:
        return [SourceRecord.model_validate_json(r[0]) for r in self.db.execute("SELECT json FROM sources ORDER BY source_id")]

    def get_source(self, source_id: str) -> SourceRecord | None:
        row = self.db.execute("SELECT json FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return SourceRecord.model_validate_json(row[0]) if row else None

    # ------------------------------------------------------------------ documents
    def upsert_document(self, doc: LegalDocument) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?,?,?,?)",
            (doc.document_id, doc.kind.value, doc.snapshot_id, doc.model_dump_json()),
        )
        self.db.commit()

    def get_document(self, document_id: str) -> LegalDocument | None:
        row = self.db.execute("SELECT json FROM documents WHERE document_id=?", (document_id,)).fetchone()
        return LegalDocument.model_validate_json(row[0]) if row else None

    def list_documents(self) -> list[LegalDocument]:
        return [LegalDocument.model_validate_json(r[0]) for r in self.db.execute("SELECT json FROM documents")]

    def delete_document(self, document_id: str) -> None:
        """Propagate a source-side removal/correction to all indexes."""
        self.db.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
        self.db.execute("DELETE FROM provisions WHERE document_id=?", (document_id,))
        self.db.execute("DELETE FROM judgments WHERE document_id=?", (document_id,))
        self.db.execute("DELETE FROM case_numbers WHERE document_id=?", (document_id,))
        self.db.execute("DELETE FROM fts WHERE document_id=?", (document_id,))
        self.db.commit()

    # ------------------------------------------------------------------ provisions
    def replace_provisions(self, document_id: str, version_id: str, provisions: list[ProvisionVersion], title: str) -> None:
        """Replace all provisions of one version atomically (re-parse / correction safe)."""
        with self.db:
            old = [r[0] for r in self.db.execute(
                "SELECT provision_id FROM provisions WHERE document_id=? AND version_id=?", (document_id, version_id))]
            for pid in old:
                self.db.execute("DELETE FROM fts WHERE ref=?", (pid,))
            self.db.execute("DELETE FROM provisions WHERE document_id=? AND version_id=?", (document_id, version_id))
            for p in provisions:
                self.db.execute(
                    "INSERT OR REPLACE INTO provisions VALUES (?,?,?,?,?,?)",
                    (p.provision_id, p.document_id, p.locator, p.version_id, p.snapshot_id, p.model_dump_json()),
                )
                self.db.execute(
                    "INSERT INTO fts(ref, kind, document_id, title, body) VALUES (?,?,?,?,?)",
                    (p.provision_id, "provision", p.document_id, f"{title} {p.locator}", p.text),
                )

    def get_provisions(self, document_id: str, locator: str | None = None, version_id: str | None = None) -> list[ProvisionVersion]:
        q, args = "SELECT json FROM provisions WHERE document_id=?", [document_id]
        if locator:
            q += " AND locator=?"
            args.append(locator)
        if version_id:
            q += " AND version_id=?"
            args.append(version_id)
        return [ProvisionVersion.model_validate_json(r[0]) for r in self.db.execute(q, args)]

    def get_provision(self, provision_id: str) -> ProvisionVersion | None:
        row = self.db.execute("SELECT json FROM provisions WHERE provision_id=?", (provision_id,)).fetchone()
        return ProvisionVersion.model_validate_json(row[0]) if row else None

    def list_versions(self, document_id: str) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT DISTINCT version_id FROM provisions WHERE document_id=?", (document_id,))]

    # ------------------------------------------------------------------ judgments
    def upsert_judgment(self, j: Judgment, title: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO judgments VALUES (?,?,?)", (j.document_id, j.snapshot_id, j.model_dump_json()))
            self.db.execute("DELETE FROM case_numbers WHERE document_id=?", (j.document_id,))
            for cn in j.case_numbers:
                self.db.execute("INSERT INTO case_numbers VALUES (?,?)", (normalize_case_number(cn), j.document_id))
            self.db.execute("DELETE FROM fts WHERE ref=?", (j.document_id,))
            self.db.execute(
                "INSERT INTO fts(ref, kind, document_id, title, body) VALUES (?,?,?,?,?)",
                (j.document_id, "judgment", j.document_id, title, j.text),
            )

    def get_judgment(self, document_id: str) -> Judgment | None:
        row = self.db.execute("SELECT json FROM judgments WHERE document_id=?", (document_id,)).fetchone()
        return Judgment.model_validate_json(row[0]) if row else None

    def find_judgments_by_case_number(self, case_number: str) -> list[Judgment]:
        ids = [r[0] for r in self.db.execute(
            "SELECT document_id FROM case_numbers WHERE case_number_norm=?", (normalize_case_number(case_number),))]
        return [j for i in ids if (j := self.get_judgment(i))]

    # ------------------------------------------------------------------ search
    def fts_search(self, match: str, kinds: list[str] | None, limit: int, offset: int) -> list[tuple[str, str, str, float, str]]:
        """Returns (ref, kind, document_id, bm25, snippet_text). `match` is an FTS5 expression."""
        q = ("SELECT ref, kind, document_id, bm25(fts, 5.0, 1.0) AS s, body FROM fts WHERE fts MATCH ?")
        args: list = [match]
        if kinds:
            q += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += kinds
        q += " ORDER BY s LIMIT ? OFFSET ?"
        args += [limit, offset]
        return list(self.db.execute(q, args))

    def stats(self) -> dict:
        return {
            t: self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("sources", "snapshots", "documents", "provisions", "judgments")
        }

    # ------------------------------------------------------------------ reports
    def save_report(self, report_json: str, report_id: str) -> None:
        d = self.data_dir / "reports"
        d.mkdir(exist_ok=True)
        (d / f"{report_id}.json").write_text(report_json, encoding="utf-8")

    def load_report_json(self, report_id: str) -> str | None:
        if not report_id.replace("-", "").isalnum():
            return None
        p = self.data_dir / "reports" / f"{report_id}.json"
        return p.read_text(encoding="utf-8") if p.exists() else None

    def dump_manifest(self) -> str:
        """Data manifest for quality reports: snapshot ids, hashes, urls."""
        rows = [json.loads(r[0]) for r in self.db.execute("SELECT json FROM snapshots ORDER BY snapshot_id")]
        return json.dumps(rows, ensure_ascii=False, indent=1, default=str)
