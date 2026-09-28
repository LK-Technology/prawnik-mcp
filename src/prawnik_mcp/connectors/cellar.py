"""Cellar (publications.europa.eu) connector: EU act XHTML as published in the OJ.

EUR-Lex web pages are not used (they answer bots with a 202 challenge); Cellar's
REST content negotiation is. The text is the ORIGINAL publication (no consolidation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from prawnik_mcp.connectors.http import PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import LegalDocument, ProvisionVersion, SourceKind, TemporalStatus
from prawnik_mcp.parsers.cellar import PARSER_VERSION, parse_cellar_xhtml
from prawnik_mcp.store import Store

CELLAR = "https://publications.europa.eu/resource/celex"
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


def ingest_xhtml(store: Store, celex: str, content: bytes, url: str, fetched_at: datetime | None = None,
                 *, fetched_now: bool = True) -> CellarIngest:
    snap = store.put_snapshot(SOURCE_ID, url, content, "application/xhtml+xml",
                              parser_version=PARSER_VERSION, fetched_at=fetched_at)
    parsed = parse_cellar_xhtml(content)
    if not parsed.articles:
        raise SourceUnavailable(url, "odpowiedź Cellar nie zawiera artykułów (nieoczekiwany format)")
    doc_id = f"celex:{celex}"
    version_id = f"{doc_id}:oj"
    label = "tekst pierwotny, Dz.U. UE " + (parsed.oj_reference or "?") + (f" z {parsed.oj_date}" if parsed.oj_date else "")
    provs = [
        ProvisionVersion(
            provision_id=f"{doc_id}#{a.locator}@{version_id}",
            document_id=doc_id, locator=a.locator, text=a.text,
            version_id=version_id, version_label=label,
            temporal_basis=TemporalStatus.original_publication,
            snapshot_id=snap.snapshot_id,
        )
        for a in parsed.articles
    ]
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
            "consolidated": False,
            "note": "Tekst w brzmieniu z Dz.U. UE (bez konsolidacji); późniejsze zmiany dyrektywy nie są uwzględnione.",
            "article_headings": {a.locator: a.heading for a in parsed.articles},
            "parse_warnings": parsed.warnings,
            "parser_version": PARSER_VERSION,
        },
    )
    store.upsert_document(doc)
    store.replace_provisions(doc_id, version_id, provs, title=doc.title)
    return CellarIngest(doc_id, version_id, len(provs), snap.snapshot_id, fetched_now, parsed.warnings)


def sync_celex(store: Store, client: PoliteClient, celex: str, *, force: bool = False) -> CellarIngest:
    url = celex_url(celex)
    if not force:
        snap = store.find_snapshot_by_url(url)
        content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
        if snap and content is not None:
            return ingest_xhtml(store, celex, content, url, snap.fetched_at, fetched_now=False)
    r = client.get(url, accept=ACCEPT, headers={"Accept-Language": LANG})
    if b"<html" not in r.content[:2000].lower():
        raise SourceUnavailable(url, f"nieoczekiwany typ odpowiedzi Cellar: {r.content_type}", r.status)
    # snapshot keyed by the requested (canonical) URL so resume works; final URL kept in metadata
    res = ingest_xhtml(store, celex, r.content, url, r.fetched_at)
    doc = store.get_document(res.document_id)
    if doc and r.url != url:
        doc.metadata["cellar_final_url"] = r.url
        store.upsert_document(doc)
    return res
