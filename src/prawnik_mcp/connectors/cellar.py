"""Cellar (publications.europa.eu) connector: EU act XHTML as published in the OJ.

EUR-Lex web pages are not used (they answer bots with a 202 challenge); Cellar's
REST content negotiation is. The text is the ORIGINAL publication (no consolidation).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime
from prawnik_mcp.connectors.http import PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import LegalDocument, ProvisionVersion, SourceKind, TemporalStatus
from prawnik_mcp.parsers.cellar import PARSER_VERSION, parse_cellar_xhtml
from prawnik_mcp.store import Store

CELLAR = "https://publications.europa.eu/resource/celex"
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
EURLEX_HUMAN = "https://eur-lex.europa.eu/legal-content/PL/TXT/?uri=CELEX:{celex}"
SOURCE_ID = "cellar"
ACCEPT = "application/xhtml+xml"
LANG = "pol"


def celex_url(celex: str) -> str:
    return f"{CELLAR}/{celex}"


@dataclass
class CellarIngest:
    document_id: str
    version_id: str
    provisions: int
    snapshot_id: str
    fetched_now: bool
    warnings: list[str] = field(default_factory=list)


_CONS_RE = re.compile(r"^0(\d{4}[A-Z]\d{4})-(\d{4})(\d{2})(\d{2})$")


def consolidated_versions(client: PoliteClient, celex: str) -> list[str]:
    """CELEX numbers of consolidated versions of an act ("02011L0083-20260927"), newest first (SPARQL)."""
    base = "0" + celex[1:]
    q = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> SELECT DISTINCT ?c WHERE { "
         "?w cdm:resource_legal_id_celex ?c . FILTER(STRSTARTS(STR(?c), \"" + base + "-\")) } LIMIT 200")
    r = client.get(f"{SPARQL}?{urlencode({'query': q})}", accept="application/sparql-results+json")
    vals = [b["c"]["value"] for b in json.loads(r.content).get("results", {}).get("bindings", [])]
    return sorted((v for v in vals if _CONS_RE.match(v)), reverse=True)


def ingest_xhtml(store: Store, celex: str, content: bytes, url: str, fetched_at: datetime | None = None,
                 *, fetched_now: bool = True, consolidated_celex: str | None = None) -> CellarIngest:
    snap = store.put_snapshot(SOURCE_ID, url, content, "application/xhtml+xml",
                              parser_version=PARSER_VERSION, fetched_at=fetched_at)
    parsed = parse_cellar_xhtml(content)
    if not parsed.articles:
        raise SourceUnavailable(url, "odpowiedź Cellar nie zawiera artykułów (nieoczekiwany format)")
    doc_id = f"celex:{celex}"
    state_date = None
    if consolidated_celex:
        m = _CONS_RE.match(consolidated_celex)
        state_date = date(int(m.group(2)), int(m.group(3)), int(m.group(4))) if m else None
        version_id = f"{doc_id}:consolidated:{state_date}"
        label = (f"wersja skonsolidowana {consolidated_celex} (stan na {state_date}); "
                 "konsolidacja UE ma charakter dokumentacyjny — moc prawną mają teksty w Dz.U. UE")
        basis = TemporalStatus.consolidated_text
    else:
        version_id = f"{doc_id}:oj"
        label = "tekst pierwotny, Dz.U. UE " + (parsed.oj_reference or "?") + (f" z {parsed.oj_date}" if parsed.oj_date else "")
        basis = TemporalStatus.original_publication
    provs = [
        ProvisionVersion(
            provision_id=f"{doc_id}#{a.locator}@{version_id}",
            document_id=doc_id, locator=a.locator, text=a.text,
            version_id=version_id, version_label=label, text_state_date=state_date,
            temporal_basis=basis, snapshot_id=snap.snapshot_id,
        )
        for a in parsed.articles
    ]
    prev = store.get_document(doc_id)
    if prev and consolidated_celex is None and prev.metadata.get("consolidated"):
        # keep the consolidated document metadata; only add the OJ version's provisions
        store.replace_provisions(doc_id, version_id, provs, title=prev.title)
        return CellarIngest(doc_id, version_id, len(provs), snap.snapshot_id, fetched_now, parsed.warnings)
    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.eu_act, celex=celex, title=parsed.title,
        publication=f"Dz.U. UE {parsed.oj_reference}" if parsed.oj_reference else None,
        original_url=EURLEX_HUMAN.format(celex=celex),
        snapshot_id=snap.snapshot_id, sha256=snap.sha256,
        metadata={
            "current_version_id": version_id,
            "cellar_url": url,
            "oj_reference": parsed.oj_reference,
            "oj_date": parsed.oj_date,
            "language": LANG,
            "consolidated": bool(consolidated_celex),
            "consolidated_celex": consolidated_celex,
            "note": ("Wersja skonsolidowana z Cellar (dokumentacyjna); obok zachowany tekst z Dz.U. UE."
                     if consolidated_celex else
                     "Tekst w brzmieniu z Dz.U. UE (bez konsolidacji); późniejsze zmiany aktu nie są uwzględnione."),
            "article_headings": {a.locator: a.heading for a in parsed.articles},
            "parse_warnings": parsed.warnings,
            "parser_version": PARSER_VERSION,
        },
    )
    store.upsert_document(doc)
    store.replace_provisions(doc_id, version_id, provs, title=doc.title)
    return CellarIngest(doc_id, version_id, len(provs), snap.snapshot_id, fetched_now, parsed.warnings)


def _fetch_xhtml(store: Store, client: PoliteClient, url: str, *, force: bool) -> tuple[bytes, datetime, bool, str]:
    if not force:
        snap = store.find_snapshot_by_url(url)
        content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
        if snap and content is not None:
            return content, snap.fetched_at, False, url
    r = client.get(url, accept=ACCEPT, headers={"Accept-Language": LANG})
    if b"<html" not in r.content[:2000].lower():
        raise SourceUnavailable(url, f"nieoczekiwany typ odpowiedzi Cellar: {r.content_type}", r.status)
    return r.content, r.fetched_at, True, r.url


def sync_celex(store: Store, client: PoliteClient, celex: str, *, force: bool = False,
               consolidated: bool = True) -> CellarIngest:
    """OJ text of the act, plus (by default) its latest consolidated version as a separate version."""
    url = celex_url(celex)
    content, fetched_at, fresh, final_url = _fetch_xhtml(store, client, url, force=force)
    # snapshot keyed by the requested (canonical) URL so resume works; final URL kept in metadata
    res = ingest_xhtml(store, celex, content, url, fetched_at, fetched_now=fresh)
    if not consolidated:
        return res
    try:
        versions = consolidated_versions(client, celex)
    except Exception as e:  # noqa: BLE001 - consolidation is optional; OJ text is stored
        res.warnings.append(f"lista wersji skonsolidowanych niedostępna: {type(e).__name__}")
        return res
    if not versions:
        return res
    latest = versions[0]
    try:
        c_content, c_at, c_fresh, _ = _fetch_xhtml(store, client, celex_url(latest), force=force)
        cres = ingest_xhtml(store, celex, c_content, celex_url(latest), c_at, fetched_now=c_fresh,
                            consolidated_celex=latest)
    except Exception as e:  # noqa: BLE001
        res.warnings.append(f"{latest}: wersja skonsolidowana niedostępna ({type(e).__name__})")
        return res
    doc = store.get_document(res.document_id)
    if doc:
        doc.metadata["consolidated_versions_available"] = versions
        if final_url != url:
            doc.metadata["cellar_final_url"] = final_url
        store.upsert_document(doc)
    cres.warnings += res.warnings
    return cres


# --------------------------------------------------------------------------- connector


_EU_NUM_RE = re.compile(
    r"(?:(?P<kind>dyrektyw|rozporządze|decyzj)\w*\D{0,40}?)?(?P<eu>\((?:UE|WE|EU|EC)\)\s*)?"
    r"(?P<a>\d{2,4})/(?P<b>\d{1,4})(?P<suffix>/(?:UE|WE|EWG|EU|EC|EEC))?", re.I)
_CELEX_RE = re.compile(r"\b(3\d{4}[LRD]\d{4})\b")


def celex_candidates(query: str) -> list[str]:
    """CELEX numbers implied by an identifier in the query ('dyrektywa 2011/83/UE', '32016R0679', 'RODO')."""
    q = query.strip()
    out = _CELEX_RE.findall(q.upper())
    if re.search(r"\brodo\b|\bgdpr\b", q, re.I):
        out.append("32016R0679")
    for m in _EU_NUM_RE.finditer(q):
        if not (m.group("kind") or m.group("eu") or m.group("suffix")):
            continue  # a bare "12/2020" is too ambiguous (contract numbers, case numbers...)
        a, b = m.group("a"), m.group("b")
        # new numbering (2015+) is YYYY/N for regulations ("(UE) 2016/679"); directives are YYYY/N/UE
        if len(a) == 4:
            year, num = a, b
        elif len(b) == 4:
            year, num = b, a
        elif len(a) == 2:  # pre-1999 numbering: 93/13/EWG -> 1993, no. 13
            year, num = str(1900 + int(a) if int(a) >= 50 else 2000 + int(a)), b
        else:
            year, num = None, None
        if not year or not (1950 <= int(year) <= 2100):
            continue
        kind = (m.group("kind") or "").lower()
        letters = ["L"] if kind.startswith("dyrektyw") else ["R"] if kind.startswith("rozporz") else \
            ["D"] if kind.startswith("decyzj") else ["L", "R"]
        out += [f"3{year}{letter}{int(num):04d}" for letter in letters]
    return list(dict.fromkeys(out))


class CellarConnector(BaseConnector):
    """EU acts by CELEX number from the Publications Office (original OJ text)."""

    source_id = "cellar"
    supports_fetch = True
    supports_search = True

    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Identifier-based lookup only (full-text SPARQL over Cellar is too slow for interactive use)."""
        return [RemoteHit(document_id=f"celex:{c}", kind="eu_act", title=f"CELEX {c}",
                          snippet="Akt UE rozpoznany po identyfikatorze; pełny tekst: get_legal_document.",
                          original_url=EURLEX_HUMAN.format(celex=c), metadata={"celex": c, "source": "cellar"})
                for c in celex_candidates(query)[:limit]]

    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        res = SourceSyncResult(source_id=self.source_id)
        for celex in list(self.info.defaults.get("celex", []))[: limit or None]:
            try:
                ing = sync_celex(store, client, celex, force=force)
                res.counts[celex] = ing.provisions
                res.warnings += ing.warnings
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{celex}: {e}")
        res.ok = not res.errors
        self.record(store, success=bool(res.counts), partial=bool(res.errors), offline=False)
        return res

    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        res = SourceSyncResult(source_id=self.source_id)
        for p in sorted(fixtures.glob("celex_*.xhtml")):
            celex = p.stem.split("_", 1)[1]
            try:
                ing = ingest_xhtml(store, celex, p.read_bytes(), celex_url(celex), mtime(p))
                res.counts[celex] = ing.provisions
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{celex}: {type(e).__name__}: {e}")
        res.ok = bool(res.counts) and not res.errors
        self.record(store, success=bool(res.counts), partial=bool(res.errors), offline=True)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        docs = [d for d in store.list_documents() if d.document_id.startswith("celex:")]
        parts = [f"{d.document_id} ({len(store.get_provisions(d.document_id))} articles, original OJ text)" for d in docs]
        return ("; ".join(parts) or "no local data"), [f"{d.document_id}: original OJ publication" for d in docs]

    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Sync explicit CELEX numbers (`params["celex"]`) or identifiers recognised in `params["query"]`."""
        res = SourceSyncResult(source_id=self.source_id)
        ids = [c.removeprefix("celex:") for c in params.get("celex") or []]
        if params.get("query"):
            ids += celex_candidates(params["query"])
        for celex in list(dict.fromkeys(ids))[: limits.limit or None]:
            try:
                ing = sync_celex(store, client, celex, force=not limits.resume)
                res.counts[celex] = ing.provisions
                res.warnings += ing.warnings
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{celex}: {e}")
            if progress:
                progress(f"cellar: {celex} done")
        res.ok = not res.errors
        self.record(store, success=bool(res.counts), partial=bool(res.errors), offline=False)
        return res

    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        if not document_id.startswith("celex:"):
            return None
        return sync_celex(store, client, document_id.removeprefix("celex:"), force=force).document_id
