"""CBOSA connector (orzeczenia.nsa.gov.pl): NSA + 16 WSA judgments, HTML only. EXPERIMENTAL.

Only single judgments are fetched: `GET /doc/{HEX}` (10 hex characters), which robots.txt allows.
robots.txt (read 2026-09-28) disallows `/cbo/search` and `/cbo/find` for every user agent, so this
connector never calls them: no live search (`supports_search = False`) and no search-driven bulk sync.
Judgment ids come from users, from related-judgment links on stored pages, or from the catalog
default list (`[sources.cbosa.defaults] doc_ids`).

Ported, with attribution, from mcp-nsa (https://github.com/matematicsolutions/mcp-nsa,
MIT License, Copyright (c) 2026 MateMatic / Wieslaw Mazur): the /doc/ URL scheme, the HTML label
contract (see parsers/cbosa.py) and the ban report below. Re-implemented in Python.

Politeness and risk: mcp-nsa reported a full IP ban (403 on every path) after continuous traffic at
2 req/s, while 0.5 req/s ran clean. Every CBOSA request of this connector instance (the registry holds
one per process) goes through one gate that keeps >= 2 s between requests, whatever PoliteClient
instance is used, on top of the client's own per-host delay. A 403 is surfaced as `SourceUnavailable`
and stops the run; afterwards this instance refuses all CBOSA requests for `ban_cooldown_s` (1 h)
without touching the network.

NSA states that the database serves informational and educational purposes only and is not an
official collection. Page text is stored as data and never interpreted as instructions.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime
from prawnik_mcp.connectors.http import FetchResult, NotFoundUpstream, PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.cbosa import DOC_ID_RE, PARSER_VERSION, doc_url, is_judgment_page, parse_cbosa_judgment
from prawnik_mcp.store import Store

SOURCE_ID = "cbosa"
HOST = "orzeczenia.nsa.gov.pl"
ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
MIN_INTERVAL_S = 2.0  # <= 0.5 req/s
BAN_COOLDOWN_S = 3600.0
MAX_DEFAULT_IDS = 20
ROBOTS_NOTE = "CBOSA robots.txt disallows /cbo/search and /cbo/find; only single /doc/{hex} fetches are supported"


def ingest_judgment_bytes(store: Store, content: bytes, doc_id: str, fetched_at: datetime | None = None,
                          content_type: str = "text/html; charset=UTF-8") -> Judgment:
    snap = store.put_snapshot(SOURCE_ID, doc_url(doc_id), content, content_type,
                              parser_version=PARSER_VERSION, fetched_at=fetched_at)
    judgment, doc = parse_cbosa_judgment(content, doc_id, snap.snapshot_id, snap.sha256, snap.fetched_at)
    store.upsert_document(doc)
    store.upsert_judgment(judgment, title=doc.title)
    return judgment


class CbosaConnector(BaseConnector):
    """NSA/WSA judgments from CBOSA, fetched one by one from /doc/{HEX} at <= 0.5 req/s."""

    source_id = "cbosa"
    supports_search = False  # robots.txt disallows /cbo/search and /cbo/find
    supports_fetch = True

    def __init__(self, *, min_interval_s: float = MIN_INTERVAL_S, ban_cooldown_s: float = BAN_COOLDOWN_S):
        self.min_interval_s = min_interval_s
        self.ban_cooldown_s = ban_cooldown_s
        self._gate = threading.Lock()
        self._last: float | None = None
        self._blocked_until = 0.0

    # ------------------------------------------------------------------ transport
    def _get(self, client: PoliteClient, url: str) -> FetchResult:
        """One CBOSA request through the instance-wide gate (spacing, 403 circuit breaker)."""
        with self._gate:
            now = time.monotonic()
            if now < self._blocked_until:
                raise SourceUnavailable(url, f"CBOSA odpowiedziała wcześniej 403 (możliwa blokada IP); wstrzymano "
                                        f"zapytania na {self._blocked_until - now:.0f} s — bez ponawiania", 403)
            delay = max(self.min_interval_s, client.host_delays.get(HOST, client.min_delay))
            if self._last is not None and (gap := now - self._last) < delay:
                time.sleep(delay - gap)
            try:
                return client.get(url, accept=ACCEPT)
            except SourceUnavailable as e:
                if e.http_status == 403:
                    self._blocked_until = time.monotonic() + self.ban_cooldown_s
                raise
            finally:
                self._last = time.monotonic()

    # ------------------------------------------------------------------ search (not supported)
    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict[str, Any] | None = None) -> list[RemoteHit]:
        raise NotImplementedError(ROBOTS_NOTE)

    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        return SourceSyncResult(source_id=self.source_id, ok=False, warnings=[ROBOTS_NOTE])

    # ------------------------------------------------------------------ fetch
    def fetch_judgment(self, store: Store, client: PoliteClient, doc_id: str, *,
                       force: bool = False) -> tuple[Judgment, bool]:
        """Returns (judgment, fetched_now). An already stored page is re-parsed, not re-fetched."""
        doc_id = doc_id.upper()
        if not DOC_ID_RE.fullmatch(doc_id):
            raise ValueError(f"invalid CBOSA document id {doc_id!r}")
        url = doc_url(doc_id)
        if not force:
            snap = store.find_snapshot_by_url(url)
            content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
            if snap and content is not None:
                judgment, doc = parse_cbosa_judgment(content, doc_id, snap.snapshot_id, snap.sha256, snap.fetched_at)
                store.upsert_document(doc)
                store.upsert_judgment(judgment, title=doc.title)
                return judgment, False
        r = self._get(client, url)
        if not is_judgment_page(r.content):
            # CBOSA's answer for a missing id is unverified; never store a block/error page as a judgment
            raise SourceUnavailable(r.url, "strona bez metadanych orzeczenia (brak dokumentu, blokada albo "
                                    "zmiana szablonu — nie rozstrzygamy)", r.status)
        return ingest_judgment_bytes(store, r.content, doc_id, r.fetched_at, r.content_type), True

    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        m = re.fullmatch(r"cbosa:([0-9A-Fa-f]{10})", document_id)
        if not m:
            return None
        j, _ = self.fetch_judgment(store, client, m.group(1), force=force)
        return j.document_id

    # ------------------------------------------------------------------ sync
    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        """Default scope: the judgment ids listed in the catalog (`defaults.doc_ids`), via /doc/ only.

        Checkpoint/resume: ids whose page is already snapshotted are re-parsed, not re-fetched."""
        res = SourceSyncResult(source_id=self.source_id)
        cap = min(limit or MAX_DEFAULT_IDS, MAX_DEFAULT_IDS)
        ids = [str(i).upper() for i in self.info.defaults.get("doc_ids", [])][:cap]
        if not ids:
            res.ok = True
            res.warnings.append("no default CBOSA scope in the catalog; use get_legal_document with a cbosa:<hex> id")
            return res
        fetched = reused = 0
        for doc_id in ids:
            try:
                _, now = self.fetch_judgment(store, client, doc_id, force=force)
                fetched, reused = fetched + now, reused + (not now)
            except SourceUnavailable as e:
                res.errors.append(f"cbosa:{doc_id}: {e}")
                break  # 403/block/network: stop, do not hammer the host
            except (NotFoundUpstream, ValueError) as e:
                res.errors.append(f"cbosa:{doc_id}: {e}")
        res.counts = {"fetched": fetched, "reused_checkpoint": reused}
        success = bool(fetched or reused)
        res.ok = success and not res.errors
        self.record(store, success=success, partial=bool(res.errors), offline=False)
        return res

    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        """Recorded judgment pages: `<fixtures>/cbosa/doc_<HEX>.html` (or `<fixtures>/doc_<HEX>.html`)."""
        res = SourceSyncResult(source_id=self.source_id)
        d = fixtures / "cbosa" if (fixtures / "cbosa").is_dir() else fixtures
        manifest: dict[str, Any] = {}
        if (d / "manifest.json").exists():
            try:
                manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            except ValueError as e:
                res.warnings.append(f"manifest.json: {e}")
        n = 0
        for p in sorted(d.glob("doc_*.html")):
            doc_id = p.stem[4:]
            if not DOC_ID_RE.fullmatch(doc_id):
                continue
            try:
                raw_ts = (manifest.get(p.name) or {}).get("fetched_at")
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")) if raw_ts else mtime(p)
                j = ingest_judgment_bytes(store, p.read_bytes(), doc_id, ts)
                n += 1
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
            except Exception as e:  # noqa: BLE001 - one bad sample must not stop the rest
                res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
        res.counts = {"judgments": n}
        res.ok = n > 0 and not res.errors
        self.record(store, success=n > 0, partial=bool(res.errors), offline=True)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get(self.source_id, {}).get("judgments", 0)
        return f"{n} CBOSA judgments (NSA/WSA) stored locally; CBOSA is not an official collection", []
