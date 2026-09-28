"""Local store: raw snapshot files + SQLite (metadata, provisions, judgments, FTS5).

Connectors write through this API only.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prawnik_mcp import sources
from prawnik_mcp.contracts import (
    Judgment,
    LegalDocument,
    ProvisionVersion,
    Snapshot,
    SourceRecord,
    sha256_bytes,
    snapshot_id_for,
)
from prawnik_mcp.migrations import SCHEMA_V1 as SCHEMA  # noqa: F401  (kept for backwards compatibility)


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
        # isolation_level=None: explicit transactions only (see _tx), so batches really batch.
        self.db = sqlite3.connect(self.data_dir / "prawnik.sqlite3", check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        from prawnik_mcp.migrations import migrate

        self.schema_version = migrate(self.db)
        self._depth = 0

    # ------------------------------------------------------------------ transactions
    @contextmanager
    def _tx(self) -> Iterator[None]:
        """Atomic unit of work; nested calls join the outer transaction (see batch())."""
        outer = self._depth == 0
        if outer:
            self.db.execute("BEGIN")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if outer:
                self.db.execute("ROLLBACK")
            raise
        self._depth -= 1
        if outer:
            self.db.execute("COMMIT")

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Group many writes into one transaction (bulk sync): `with store.batch(): ...`."""
        with self._tx():
            yield

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
        with self._tx():
            self.db.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
                (sid, source_id, url, snap.model_dump_json()),
            )
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
        with self._tx():
            self.db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (rec.source_id, rec.model_dump_json()))

    def get_sources(self) -> list[SourceRecord]:
        return [SourceRecord.model_validate_json(r[0]) for r in self.db.execute("SELECT json FROM sources ORDER BY source_id")]

    def get_source(self, source_id: str) -> SourceRecord | None:
        row = self.db.execute("SELECT json FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return SourceRecord.model_validate_json(row[0]) if row else None

    # ------------------------------------------------------------------ documents
    def upsert_document(self, doc: LegalDocument) -> None:
        with self._tx():
            self.db.execute(
                "INSERT OR REPLACE INTO documents(document_id, kind, snapshot_id, json, source_id) VALUES (?,?,?,?,?)",
                (doc.document_id, doc.kind.value, doc.snapshot_id, doc.model_dump_json(),
                 sources.source_for_document(doc.document_id)),
            )

    def get_document(self, document_id: str) -> LegalDocument | None:
        row = self.db.execute("SELECT json FROM documents WHERE document_id=?", (document_id,)).fetchone()
        return LegalDocument.model_validate_json(row[0]) if row else None

    def list_documents(self) -> list[LegalDocument]:
        return [LegalDocument.model_validate_json(r[0]) for r in self.db.execute("SELECT json FROM documents")]

    def delete_document(self, document_id: str) -> None:
        """Propagate a source-side removal/correction to all indexes."""
        with self._tx():
            for table in ("documents", "provisions", "judgments", "case_numbers", "fts"):
                self.db.execute(f"DELETE FROM {table} WHERE document_id=?", (document_id,))

    # ------------------------------------------------------------------ provisions
    def replace_provisions(self, document_id: str, version_id: str, provisions: list[ProvisionVersion], title: str) -> None:
        """Replace all provisions of one version atomically (re-parse / correction safe)."""
        sid = sources.source_for_document(document_id)
        with self._tx():
            old = [r[0] for r in self.db.execute(
                "SELECT provision_id FROM provisions WHERE document_id=? AND version_id=?", (document_id, version_id))]
            for pid in old:
                self.db.execute("DELETE FROM fts WHERE ref=?", (pid,))
            self.db.execute("DELETE FROM provisions WHERE document_id=? AND version_id=?", (document_id, version_id))
            for p in provisions:
                self.db.execute(
                    "INSERT OR REPLACE INTO provisions(provision_id, document_id, locator, version_id, snapshot_id, json, source_id)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (p.provision_id, p.document_id, p.locator, p.version_id, p.snapshot_id, p.model_dump_json(), sid),
                )
                self.db.execute(
                    "INSERT INTO fts(ref, kind, document_id, source_id, title, body) VALUES (?,?,?,?,?,?)",
                    (p.provision_id, "provision", p.document_id, sid, f"{title} {p.locator}", p.text),
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
        sid = sources.source_for_document(j.document_id)
        with self._tx():
            self.db.execute("INSERT OR REPLACE INTO judgments(document_id, snapshot_id, json, source_id) VALUES (?,?,?,?)",
                            (j.document_id, j.snapshot_id, j.model_dump_json(), sid))
            self.db.execute("DELETE FROM case_numbers WHERE document_id=?", (j.document_id,))
            for cn in j.case_numbers:
                self.db.execute("INSERT INTO case_numbers VALUES (?,?)", (normalize_case_number(cn), j.document_id))
            self.db.execute("DELETE FROM fts WHERE ref=?", (j.document_id,))
            self.db.execute(
                "INSERT INTO fts(ref, kind, document_id, source_id, title, body) VALUES (?,?,?,?,?,?)",
                (j.document_id, "judgment", j.document_id, sid, title, j.text),
            )

    def get_judgment(self, document_id: str) -> Judgment | None:
        row = self.db.execute("SELECT json FROM judgments WHERE document_id=?", (document_id,)).fetchone()
        return Judgment.model_validate_json(row[0]) if row else None

    def find_judgments_by_case_number(self, case_number: str) -> list[Judgment]:
        ids = [r[0] for r in self.db.execute(
            "SELECT document_id FROM case_numbers WHERE case_number_norm=?", (normalize_case_number(case_number),))]
        return [j for i in ids if (j := self.get_judgment(i))]

    # ------------------------------------------------------------------ search
    def fts_search(self, match: str, kinds: list[str] | None, limit: int, offset: int,
                   source_ids: list[str] | None = None) -> list[tuple[str, str, str, float, str]]:
        """Returns (ref, kind, document_id, bm25, snippet_text). `match` is an FTS5 expression."""
        q = ("SELECT ref, kind, document_id, bm25(fts, 5.0, 1.0) AS s, body FROM fts WHERE fts MATCH ?")
        args: list = [match]
        if kinds:
            q += " AND kind IN ({})".format(",".join("?" * len(kinds)))
            args += kinds
        if source_ids:
            q += " AND source_id IN ({})".format(",".join("?" * len(source_ids)))
            args += source_ids
        q += " ORDER BY s LIMIT ? OFFSET ?"
        args += [limit, offset]
        return list(self.db.execute(q, args))

    def stats(self) -> dict:
        return {
            t: self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("sources", "snapshots", "documents", "provisions", "judgments")
        }

    def stats_by_source(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for table in ("documents", "provisions", "judgments"):
            for sid, n in self.db.execute(f"SELECT source_id, count(*) FROM {table} GROUP BY source_id"):
                out.setdefault(sid or "unknown", {})[table] = n
        return out

    # ------------------------------------------------------------------ sync checkpoints
    def get_sync_state(self, source_id: str, scope: str) -> dict | None:
        row = self.db.execute(
            "SELECT cursor, done, items, bytes, updated_at FROM sync_state WHERE source_id=? AND scope=?",
            (source_id, scope)).fetchone()
        if not row:
            return None
        return {"cursor": row[0], "done": bool(row[1]), "items": row[2], "bytes": row[3], "updated_at": row[4]}

    def list_sync_state(self, source_id: str | None = None) -> list[dict]:
        q, args = "SELECT source_id, scope, cursor, done, items, bytes, updated_at FROM sync_state", []
        if source_id:
            q, args = q + " WHERE source_id=?", [source_id]
        return [dict(zip(("source_id", "scope", "cursor", "done", "items", "bytes", "updated_at"), r, strict=True))
                for r in self.db.execute(q + " ORDER BY source_id, scope", args)]

    def set_sync_state(self, source_id: str, scope: str, *, cursor: str | None, done: bool, items: int, bytes_: int) -> None:
        with self._tx():
            self.db.execute(
                "INSERT OR REPLACE INTO sync_state(source_id, scope, cursor, done, items, bytes, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (source_id, scope, cursor, int(done), items, bytes_, datetime.now(UTC).isoformat()))

    # ------------------------------------------------------------------ HTTP / search cache
    def cache_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT body, expires_at FROM http_cache WHERE key=?", (key,)).fetchone()
        if not row or row[1] < datetime.now(UTC).isoformat():
            return None
        return row[0]

    def cache_put(self, key: str, body: str, *, ttl: timedelta, source_id: str | None = None) -> None:
        now = datetime.now(UTC)
        with self._tx():
            self.db.execute("INSERT OR REPLACE INTO http_cache(key, source_id, created_at, expires_at, body) VALUES (?,?,?,?,?)",
                            (key, source_id, now.isoformat(), (now + ttl).isoformat(), body))

    def data_size_bytes(self) -> int:
        """Size of the SQLite database plus stored raw snapshots."""
        total = 0
        for p in [self.data_dir / "prawnik.sqlite3", self.data_dir / "prawnik.sqlite3-wal"]:
            total += p.stat().st_size if p.exists() else 0
        snaps = self.data_dir / "snapshots"
        if snaps.exists():
            total += sum(f.stat().st_size for f in snaps.rglob("*") if f.is_file())
        return total

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
