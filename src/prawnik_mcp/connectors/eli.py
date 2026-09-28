"""ELI (api.sejm.gov.pl) connector: logical act -> latest consolidated text (TJ) PDF -> provisions.

Only consolidated texts announced in Dziennik Ustaw (obwieszczenie Marszałka Sejmu) are
used as provision text. The act's own `text.html` is the ORIGINAL 1964/2014 text and the
`U` PDFs are unofficial; neither is used as current wording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime
from prawnik_mcp.connectors.http import PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import LegalDocument, ProvisionVersion, Snapshot, SourceKind, TemporalStatus
from prawnik_mcp.parsers.eli_pdf import (
    PARSER_VERSION,
    ConsolidatedText,
    parse_consolidated_pdf,
    parse_published_act_pdf,
)
from prawnik_mcp.store import Store

ELI_API = "https://api.sejm.gov.pl/eli/acts"
SOURCE_ID = "eli"
TJ_REF = "Inf. o tekście jednolitym"
AMEND_REF = "Akty zmieniające"


def act_url(eli_id: str) -> str:
    return f"{ELI_API}/{eli_id}"


def pdf_url(eli_id: str) -> str:
    return f"{ELI_API}/{eli_id}/text.pdf"


def eli_key(eli_id: str) -> tuple[int, int]:
    _, y, p = eli_id.split("/")
    return int(y), int(p)


def latest_consolidated(meta: dict) -> str | None:
    refs = (meta.get("references") or {}).get(TJ_REF) or []
    ids = [r["id"] for r in refs if r.get("id", "").startswith("DU/")]
    return max(ids, key=eli_key) if ids else None


def display_address(eli_id: str) -> str:
    _, y, p = eli_id.split("/")
    return f"Dz.U. {y} poz. {p}"


@dataclass
class ActIngest:
    document_id: str
    version_id: str
    provisions: int
    snapshot_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def ingest_consolidated(
    store: Store,
    logical_meta: dict,
    logical_snap: Snapshot,
    tj_meta: dict,
    tj_meta_snap: Snapshot,
    pdf_snap: Snapshot,
    pdf_bytes: bytes,
) -> ActIngest:
    """Parse a TJ PDF and write the logical act document + its provisions."""
    logical_id = logical_meta["ELI"]
    tj_id = tj_meta["ELI"]
    target = [r.get("id") for r in (tj_meta.get("references") or {}).get("Tekst jednolity dla aktu", [])]
    if logical_id not in target:
        raise ValueError(f"{tj_id} nie jest tekstem jednolitym aktu {logical_id} (wg ELI: {target})")
    parsed: ConsolidatedText = parse_consolidated_pdf(pdf_bytes)
    h = parsed.header
    warnings = list(parsed.warnings)

    state_date = h.state_date
    meta_state = tj_meta.get("legalStatusDate")
    if meta_state and state_date and meta_state != state_date.isoformat():
        warnings.append(f"data stanu prawnego w PDF ({state_date}) różni się od metadanych ELI ({meta_state})")
    if state_date is None and meta_state:
        state_date = date.fromisoformat(meta_state)
        warnings.append("data stanu prawnego wzięta z metadanych ELI (nie odczytano jej z PDF)")

    amendments = [
        {"id": r["id"], "date": r.get("date")}
        for r in (logical_meta.get("references") or {}).get(AMEND_REF, [])
    ]
    amend_dates = {a["id"]: a["date"] for a in amendments}

    pending: list[str] = []
    pending_struct: list[dict] = []
    for inc in h.included_amendments:
        d = amend_dates.get(inc.eli_id)
        if d and state_date and date.fromisoformat(d) > state_date:
            pending.append(f"{display_address(inc.eli_id)} – data w ELI: {d} (po dniu stanu prawnego {state_date})")
            pending_struct.append({"id": inc.eli_id, "date": d, "basis": "eli_references"})
    for src, d in h.later_entry_dates:
        if state_date and d > state_date:
            label = f"{display_address(src)} – część przepisów wchodzi w życie {d.isoformat()} (wg przepisu cytowanego w obwieszczeniu)"
            if label not in pending:
                pending.append(label)
                pending_struct.append({"id": src, "date": d.isoformat(), "basis": "obwieszczenie_text"})

    version_id = f"eli:{tj_id}"
    publication = display_address(tj_id)
    version_label = f"tekst jednolity {publication}" + (f", stan prawny na {state_date.isoformat()}" if state_date else "")
    doc_id = f"eli:{logical_id}"

    provs: list[ProvisionVersion] = []
    article_footnotes: dict[str, list[str]] = {}
    for a in parsed.articles + parsed.annexes:
        provs.append(ProvisionVersion(
            provision_id=f"{doc_id}#{a.locator}@{version_id}",
            document_id=doc_id, locator=a.locator, text=a.text,
            version_id=version_id, version_label=version_label,
            text_state_date=state_date, valid_from=None, valid_to=None,
            temporal_basis=TemporalStatus.consolidated_text,
            pending_changes=pending, excluded_provisions=h.excluded_provisions,
            snapshot_id=pdf_snap.snapshot_id, page_hint=a.page_hint, warnings=a.warnings,
        ))
        notes = [f"{n}) {parsed.footnotes[n]}" for n in a.footnote_refs if n in parsed.footnotes]
        if notes:
            article_footnotes[a.locator] = notes

    cv_entry = {
        "version_id": version_id,
        "eli": tj_id,
        "publication": publication,
        "announcement_date": h.announcement_date.isoformat() if h.announcement_date else None,
        "publication_date": h.publication_date.isoformat() if h.publication_date else None,
        "state_date": state_date.isoformat() if state_date else None,
        "base_text": h.base_text,
        "included_amendments": [{"id": i.eli_id, "date": amend_dates.get(i.eli_id), "description": i.description}
                                for i in h.included_amendments],
        "pending_changes": pending_struct,
        "excluded_provisions": h.excluded_provisions,
        "pdf_url": pdf_url(tj_id),
        "pdf_snapshot_id": pdf_snap.snapshot_id,
        "meta_snapshot_id": tj_meta_snap.snapshot_id,
        "parser_version": PARSER_VERSION,
        "page_count": parsed.page_count,
        "provision_count": len(provs),
    }
    tj_refs = [r["id"] for r in (logical_meta.get("references") or {}).get(TJ_REF, [])]
    prev = store.get_document(doc_id)
    versions = [v for v in (prev.metadata.get("consolidated_versions", []) if prev else []) if v.get("version_id") != version_id]
    versions.append(cv_entry)
    versions.sort(key=lambda v: eli_key(v["eli"]), reverse=True)

    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.statute, eli=logical_id,
        title=logical_meta.get("title", logical_id),
        publication=logical_meta.get("displayAddress"),
        original_url=act_url(logical_id),
        snapshot_id=pdf_snap.snapshot_id, sha256=pdf_snap.sha256,
        metadata={
            "current_version_id": version_id,
            "consolidated_versions": versions,
            "consolidated_text_refs": sorted(tj_refs, key=eli_key, reverse=True),
            "amendments": amendments,
            "in_force": logical_meta.get("inForce"),
            "status": logical_meta.get("status"),
            "entry_into_force": logical_meta.get("entryIntoForce"),
            "change_date": logical_meta.get("changeDate"),
            "meta_snapshot_id": logical_snap.snapshot_id,
            "article_footnotes": article_footnotes,
            "parse_warnings": warnings,
            "valid_from_note": "valid_from/valid_to nieustalone (brak zweryfikowanej historii przepisów)",
        },
    )
    store.upsert_document(doc)
    store.replace_provisions(doc_id, version_id, provs, title=doc.title)
    return ActIngest(doc_id, version_id, len(provs),
                     [logical_snap.snapshot_id, tj_meta_snap.snapshot_id, pdf_snap.snapshot_id], warnings)


def ingest_published(store: Store, logical_meta: dict, logical_snap: Snapshot, pdf_snap: Snapshot,
                     pdf_bytes: bytes) -> ActIngest:
    """Store the act's own published text when ELI has no consolidated text for it.

    The wording is the ORIGINAL publication. If ELI lists amending acts, the text may be outdated and
    the temporal status for later event dates is `unknown` (never silently treated as current)."""
    logical_id = logical_meta["ELI"]
    doc_id = f"eli:{logical_id}"
    parsed = parse_published_act_pdf(pdf_bytes)
    warnings = list(parsed.warnings)
    amendments = [{"id": r["id"], "date": r.get("date")}
                  for r in (logical_meta.get("references") or {}).get(AMEND_REF, [])]
    promulgation = logical_meta.get("promulgation")
    pub_date = date.fromisoformat(promulgation) if promulgation else None
    version_id = f"eli:{logical_id}:published"
    version_label = f"tekst ogłoszony {display_address(logical_id)}"
    pending = [f"{display_address(a['id'])} – data w ELI: {a.get('date')}" for a in amendments]
    if amendments:
        version_label += f"; akt zmieniany {len(amendments)}× — brak tekstu jednolitego w ELI"
        warnings.append(f"Akt był zmieniany ({len(amendments)} aktów zmieniających), a ELI nie zawiera tekstu "
                        "jednolitego: tekst ogłoszony może nie odpowiadać obowiązującemu brzmieniu.")
    provs = [ProvisionVersion(
        provision_id=f"{doc_id}#{a.locator}@{version_id}", document_id=doc_id, locator=a.locator, text=a.text,
        version_id=version_id, version_label=version_label, text_state_date=pub_date,
        temporal_basis=TemporalStatus.original_publication, pending_changes=pending,
        snapshot_id=pdf_snap.snapshot_id, page_hint=a.page_hint, warnings=a.warnings,
    ) for a in parsed.articles + parsed.annexes]
    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.statute, eli=logical_id, title=logical_meta.get("title", logical_id),
        publication=logical_meta.get("displayAddress"), original_url=act_url(logical_id),
        snapshot_id=pdf_snap.snapshot_id, sha256=pdf_snap.sha256,
        metadata={
            "current_version_id": version_id, "published_text_only": True,
            "consolidated_versions": [], "consolidated_text_refs": [], "amendments": amendments,
            "in_force": logical_meta.get("inForce"), "status": logical_meta.get("status"),
            "entry_into_force": logical_meta.get("entryIntoForce"), "change_date": logical_meta.get("changeDate"),
            "meta_snapshot_id": logical_snap.snapshot_id, "parse_warnings": warnings, "parser_version": PARSER_VERSION,
        },
    )
    store.upsert_document(doc)
    store.replace_provisions(doc_id, version_id, provs, title=doc.title)
    return ActIngest(doc_id, version_id, len(provs), [logical_snap.snapshot_id, pdf_snap.snapshot_id], warnings)


def _fetch_pdf(store: Store, client: PoliteClient, url: str, *, force: bool) -> tuple[Snapshot, bytes]:
    existing = None if force else store.find_snapshot_by_url(url)
    pdf_bytes = store.read_snapshot_bytes(existing.snapshot_id) if existing else None
    if existing and pdf_bytes is not None:
        return existing, pdf_bytes
    r = client.get(url, accept="application/pdf")
    if not r.content.startswith(b"%PDF"):
        raise SourceUnavailable(url, "odpowiedź nie jest plikiem PDF", r.status)
    return store.put_snapshot(SOURCE_ID, url, r.content, "application/pdf", parser_version=PARSER_VERSION,
                              fetched_at=r.fetched_at), r.content


def sync_act(store: Store, client: PoliteClient, logical_id: str, *, force: bool = False) -> ActIngest:
    """Online: logical act metadata -> latest TJ -> its metadata and PDF -> parse -> store."""
    r = client.get(act_url(logical_id), accept="application/json")
    logical_meta = json.loads(r.content)
    logical_snap = store.put_snapshot(SOURCE_ID, r.url, r.content, r.content_type, fetched_at=r.fetched_at)
    tj_id = latest_consolidated(logical_meta)
    if not tj_id:
        if not logical_meta.get("textPDF"):
            raise ValueError(f"ELI nie udostępnia tekstu (PDF) aktu {logical_id}")
        pdf_snap, pdf_bytes = _fetch_pdf(store, client, pdf_url(logical_id), force=force)
        return ingest_published(store, logical_meta, logical_snap, pdf_snap, pdf_bytes)
    r2 = client.get(act_url(tj_id), accept="application/json")
    tj_meta = json.loads(r2.content)
    tj_snap = store.put_snapshot(SOURCE_ID, r2.url, r2.content, r2.content_type, fetched_at=r2.fetched_at)

    url = pdf_url(tj_id)
    existing = None if force else store.find_snapshot_by_url(url)
    pdf_bytes = store.read_snapshot_bytes(existing.snapshot_id) if existing else None
    if existing and pdf_bytes is not None:
        pdf_snap = existing  # checkpoint: PDF of this TJ already stored, only re-parse
    else:
        r3 = client.get(url, accept="application/pdf")
        if not r3.content.startswith(b"%PDF"):
            raise SourceUnavailable(url, "odpowiedź nie jest plikiem PDF", r3.status)
        pdf_bytes = r3.content
        pdf_snap = store.put_snapshot(SOURCE_ID, url, pdf_bytes, "application/pdf",
                                      parser_version=PARSER_VERSION, fetched_at=r3.fetched_at)
    return ingest_consolidated(store, logical_meta, logical_snap, tj_meta, tj_snap, pdf_snap, pdf_bytes)


def ingest_from_files(store: Store, logical_meta_bytes: bytes, tj_meta_bytes: bytes, pdf_bytes: bytes,
                      fetched_at: datetime | None = None) -> ActIngest:
    """Offline: same pipeline from locally saved API responses (fixtures)."""
    logical_meta = json.loads(logical_meta_bytes)
    tj_meta = json.loads(tj_meta_bytes)
    ls = store.put_snapshot(SOURCE_ID, act_url(logical_meta["ELI"]), logical_meta_bytes, "application/json", fetched_at=fetched_at)
    ts = store.put_snapshot(SOURCE_ID, act_url(tj_meta["ELI"]), tj_meta_bytes, "application/json", fetched_at=fetched_at)
    ps = store.put_snapshot(SOURCE_ID, pdf_url(tj_meta["ELI"]), pdf_bytes, "application/pdf",
                            parser_version=PARSER_VERSION, fetched_at=fetched_at)
    return ingest_consolidated(store, logical_meta, ls, tj_meta, ts, ps, pdf_bytes)



# --------------------------------------------------------------------------- connector


class EliConnector(BaseConnector):
    """Polish statutes from the Sejm ELI API (latest consolidated text per act)."""

    source_id = "eli"
    supports_fetch = True
    supports_search = True

    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Live act search by title words (Dziennik Ustaw). Consolidated-text notices are skipped."""
        f = filters or {}
        params = {"title": query, "publisher": f.get("publisher", "DU"), "limit": str(max(limit * 3, 10))}
        if f.get("in_force"):
            params["inForce"] = "1"
        if f.get("act_type"):
            params["type"] = f["act_type"]
        r = client.get(f"{ELI_API}/search?{urlencode(params)}", accept="application/json")
        hits = []
        for it in json.loads(r.content).get("items") or []:
            if it.get("type") == "Obwieszczenie" and "jednolitego tekstu" in (it.get("title") or ""):
                continue
            hits.append(RemoteHit(
                document_id=f"eli:{it['ELI']}", kind="statute", title=it.get("title", ""),
                snippet=f"{it.get('displayAddress', '')}; status: {it.get('status', '')}; w mocy: {it.get('inForce', '')}",
                original_url=f"https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id={it.get('address', '')}",
                metadata={"eli": it["ELI"], "type": it.get("type"), "status": it.get("status"),
                          "in_force": it.get("inForce"), "promulgation": it.get("promulgation"), "source": "eli"}))
            if len(hits) >= limit:
                break
        return hits

    def default_acts(self) -> dict[str, str]:
        return dict(self.info.defaults.get("acts", {}))

    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        res = SourceSyncResult(source_id=self.source_id)
        for logical in list(self.default_acts())[: limit or None]:
            try:
                ing = sync_act(store, client, logical, force=force)
                res.counts[logical] = ing.provisions
                res.warnings += [f"{logical}: {w}" for w in ing.warnings]
            except Exception as e:  # noqa: BLE001 - one act must not stop the others
                res.errors.append(f"{logical}: {e}")
        res.ok = not res.errors
        self.record(store, success=bool(res.counts), partial=bool(res.errors), offline=False)
        return res

    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        res = SourceSyncResult(source_id=self.source_id)
        metas = {p: json.loads(p.read_bytes()) for p in sorted(fixtures.glob("eli_*.meta.json"))}
        by_eli = {m.get("ELI"): p for p, m in metas.items()}
        for p, m in metas.items():
            targets = [r["id"] for r in (m.get("references") or {}).get("Tekst jednolity dla aktu", [])]
            if not targets:
                continue
            pdf = fixtures / p.name.replace(".meta.json", ".pdf")
            logical_p = by_eli.get(targets[0])
            if not pdf.exists() or logical_p is None:
                res.errors.append(f"{m.get('ELI')}: missing PDF or metadata of act {targets[0]} in samples")
                continue
            if latest_consolidated(metas[logical_p]) != m["ELI"]:
                res.warnings.append(f"{m['ELI']}: not the latest consolidated text per metadata; skipped")
                continue
            try:
                ing = ingest_from_files(store, logical_p.read_bytes(), p.read_bytes(), pdf.read_bytes(),
                                        fetched_at=mtime(pdf))
                res.counts[targets[0]] = ing.provisions
                res.warnings += [f"{targets[0]}: {w}" for w in ing.warnings]
            except Exception as e:  # noqa: BLE001 - parsing problem of one act must not stop the others
                res.errors.append(f"{targets[0]}: {type(e).__name__}: {e}")
        res.ok = bool(res.counts) and not res.errors
        self.record(store, success=bool(res.counts), partial=bool(res.errors), offline=True)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        parts, ivs = [], []
        for d in store.list_documents():
            if not d.document_id.startswith("eli:") or not d.metadata.get("consolidated_versions"):
                continue
            cur = d.metadata["consolidated_versions"][0]
            iv = f"{d.title}: TJ {cur.get('publication')}, stan prawny na {cur.get('state_date')}"
            parts.append(f"{iv} ({cur.get('provision_count', 0)} units)")
            ivs.append(iv)
        return ("; ".join(parts) or "no local data"), ivs

    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Sync explicit acts (`params["acts"]`: ['DU/2018/1000', ...]) or acts found by title (`params["query"]`)."""
        res = SourceSyncResult(source_id=self.source_id)
        acts = [a.removeprefix("eli:") for a in params.get("acts") or []]
        if params.get("query"):
            acts += [h.document_id.removeprefix("eli:") for h in
                     self.search(client, params["query"], limit=limits.limit or 10, filters=params)]
        for logical in list(dict.fromkeys(acts))[: limits.limit or None]:
            if limits.max_bytes is not None and store.data_size_bytes() >= limits.max_bytes:
                res.warnings.append("stopped: data directory reached --max-gb")
                break
            try:
                ing = sync_act(store, client, logical, force=not limits.resume)
                res.counts[logical] = ing.provisions
                res.warnings += [f"{logical}: {w}" for w in ing.warnings]
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{logical}: {e}")
            if progress:
                progress(f"eli: {logical} done")
        res.ok = not res.errors
        self.record(store, success=bool(res.counts), partial=bool(res.errors), offline=False)
        return res

    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        if not document_id.startswith("eli:"):
            return None
        sync_act(store, client, document_id.removeprefix("eli:"), force=force)
        return document_id
