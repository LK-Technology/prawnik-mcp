"""Tool logic shared by the MCP server, the CLI and the eval runner.

No generative model is used here. Every result is a `ToolResult` with an explicit status.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from prawnik_mcp.contracts import (
    Claim,
    Coverage,
    EvidenceSpan,
    ProvisionVersion,
    ResultStatus,
    SearchHit,
    SourceKind,
    TemporalStatus,
    ToolResult,
    article_of,
    canonical_locator,
)
from prawnik_mcp.store import Store

DEFAULT_LIMIT = 5
MAX_LIMIT = 20
SNIPPET_CHARS = 800
JUDGMENT_PAGE_CHARS = 6000
STALE_AFTER = timedelta(days=30)

# Act aliases -> logical document ids of the MVP corpus.
ACT_ALIASES = {
    "kc": "eli:DU/1964/93",
    "k.c.": "eli:DU/1964/93",
    "kodeks cywilny": "eli:DU/1964/93",
    "kodeksu cywilnego": "eli:DU/1964/93",
    "upk": "eli:DU/2014/827",
    "u.p.k.": "eli:DU/2014/827",
    "ustawa o prawach konsumenta": "eli:DU/2014/827",
    "ustawy o prawach konsumenta": "eli:DU/2014/827",
    "prawach konsumenta": "eli:DU/2014/827",
    "dyrektywa 2011/83": "celex:32011L0083",
    "dyrektywy 2011/83": "celex:32011L0083",
    "2011/83/ue": "celex:32011L0083",
}

KIND_TO_SOURCE = {SourceKind.statute: "eli", SourceKind.judgment: "saos", SourceKind.eu_act: "cellar"}

_STOP = set(
    "a aby ale albo ani by być czy do dla go i ich jak jaki jest jeśli już lub ma może na nie nie o od oraz po "
    "przez przy się są ta tak te to tu w we z za ze że jako który która które co czy mój moja mnie mi".split()
)
_CASE_RE = re.compile(r"\b([IVXL]{1,5}\s+[A-Za-zŁłŻż]{1,6}(?:-[A-Za-z]+)?\s+\d{1,6}/\d{2,4})\b")
_ART_RE = re.compile(r"\bart\.?\s*\d+[a-z]?(?:\s*\^\s*\d+|\(\d+\)|[¹²³⁰-⁹]+)?(?:\s*(?:§|ust\.?|pkt|lit\.?)\s*\w+)*", re.I)


def _now() -> datetime:
    return datetime.now(UTC)


def _coverage(store: Store, kinds: list[SourceKind] | None = None) -> Coverage:
    wanted = {KIND_TO_SOURCE[k] for k in kinds} if kinds else None
    cov = Coverage()
    for s in store.get_sources():
        if wanted and s.source_id not in wanted:
            continue
        if s.access_status in ("ok", "degraded"):
            cov.sources_searched.append(s.source_id)
            if not s.last_successful_sync:
                cov.corpus_note += f" Źródło {s.source_id}: dane z lokalnych próbek offline, bez synchronizacji na żywo."
        else:
            cov.sources_unavailable.append(s.source_id)
    return cov


def _stale_warnings(store: Store) -> list[str]:
    out = []
    for s in store.get_sources():
        if s.last_successful_sync and _now() - s.last_successful_sync.astimezone(UTC) > STALE_AFTER:
            out.append(f"Źródło {s.source_id}: ostatnia udana synchronizacja {s.last_successful_sync:%Y-%m-%d} (stale).")
    return out


def _temporal(store: Store, p: ProvisionVersion, when: date | None) -> tuple[TemporalStatus, list[str]]:
    from prawnik_mcp.evidence.temporal import temporal_status_for

    amendments = None
    for did in (p.version_id, p.document_id):
        d = store.get_document(did)
        if d and isinstance(d.metadata.get("amendments"), list):
            amendments = d.metadata["amendments"]
            break
    return temporal_status_for(p, when, amendments=amendments)


def _snippet(text: str, terms: list[str], size: int = SNIPPET_CHARS) -> str:
    if len(text) <= size:
        return text
    low = text.lower()
    pos = min((i for t in terms if (i := low.find(t.lower())) >= 0), default=0)
    start = max(0, pos - size // 4)
    end = min(len(text), start + size)
    return ("[…] " if start > 0 else "") + text[start:end] + (" […]" if end < len(text) else "")


def _fts_expression(query: str) -> tuple[str, list[str]]:
    words = [w for w in re.findall(r"\w+", query.lower()) if w not in _STOP and len(w) > 1]
    terms = []
    for w in words[:12]:
        if w.isdigit():
            terms.append(f'"{w}"')
        elif len(w) >= 6:
            terms.append(f'"{w[: max(4, len(w) - 3)]}"*')  # crude Polish inflection handling
        else:
            terms.append(f'"{w}"*')
    return " OR ".join(terms), words


def _parse_date(v: Any) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _resolve_act(text: str) -> str | None:
    low = text.lower()
    for alias in sorted(ACT_ALIASES, key=len, reverse=True):
        if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", low):
            return ACT_ALIASES[alias]
    return None


def _latest(provs: list[ProvisionVersion]) -> ProvisionVersion:
    return sorted(provs, key=lambda p: (p.text_state_date or date.min, p.version_id))[-1]


# ---------------------------------------------------------------------------------------------- tools


def search_legal(
    store: Store, query: str, kinds: list[str] | None = None, filters: dict | None = None,
    relevant_date: str | None = None, cursor: str | None = None, limit: int = DEFAULT_LIMIT,
) -> ToolResult:
    filters = filters or {}
    try:
        kind_enums = [SourceKind(k) for k in kinds] if kinds else None
        when = _parse_date(relevant_date)
        offset = int(cursor) if cursor else 0
    except ValueError as e:
        return ToolResult(status=ResultStatus.invalid_input, warnings=[f"Błędne parametry: {e}"])
    if not query or not query.strip():
        return ToolResult(status=ResultStatus.invalid_input, warnings=["Puste zapytanie."])
    limit = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))
    cov = _coverage(store, kind_enums)
    warnings = _stale_warnings(store)
    if when and when > date.today():
        warnings.append("Data zdarzenia jest w przyszłości — wersji przepisów na tę datę nie da się ustalić.")

    if store.stats()["documents"] == 0:
        return ToolResult(status=ResultStatus.source_unavailable, coverage=cov, warnings=warnings + [
            "Lokalny korpus jest pusty. Uruchom `prawnik-mcp sync` (lub `sync --offline`). Brak wyników nie oznacza braku przepisów."])

    hits: list[SearchHit] = []

    # 1. exact identifiers: case numbers
    case = _CASE_RE.search(query)
    if case and (not kind_enums or SourceKind.judgment in kind_enums):
        found = store.find_judgments_by_case_number(case.group(1))
        if not found:
            return ToolResult(status=ResultStatus.not_found, coverage=cov, warnings=warnings + [
                f"Sygnatura {case.group(1)} nie występuje w lokalnym korpusie SAOS (próbka). "
                "To nie dowodzi, że orzeczenie nie istnieje; nie cytuj go bez pobrania ze źródła."])
        for j in found:
            doc = store.get_document(j.document_id)
            hits.append(_judgment_hit(store, j, doc, [case.group(1)]))
        status = ResultStatus.ambiguous if len(hits) > 1 else ResultStatus.ok
        if status is ResultStatus.ambiguous:
            warnings.append("Ta sama sygnatura występuje w kilku dokumentach — rozróżnij po sądzie, dacie i rodzaju.")
        return ToolResult(status=status, data={"hits": [h.model_dump(mode="json") for h in hits], "next_cursor": None},
                          coverage=cov, warnings=warnings)

    # 2. exact identifiers: "art. X <act>"
    art = _ART_RE.search(query)
    act = _resolve_act(query) or filters.get("document_id")
    if art and act:
        loc = canonical_locator(art.group(0))
        if loc:
            res = get_legal_document(store, act, loc, relevant_date)
            if res.status in (ResultStatus.ok, ResultStatus.temporal_unknown):
                p = res.data
                hits.append(SearchHit(
                    document_id=act, kind=SourceKind(p["kind"]), title=p["title"], locator=p["locator"],
                    version_id=p["version_id"], snippet=_snippet(p["text"], []), original_url=p["original_url"],
                    snapshot_id=p["snapshot_id"], fetched_at=p.get("fetched_at"),
                    metadata={"temporal_status": p["temporal_status"], "match": "identifier"}))
                return ToolResult(status=res.status, data={"hits": [h.model_dump(mode="json") for h in hits], "next_cursor": None},
                                  coverage=cov, warnings=warnings + res.warnings)
            return ToolResult(status=res.status, coverage=cov, warnings=warnings + res.warnings)

    # 3. full text (BM25)
    expr, words = _fts_expression(query)
    if not expr:
        return ToolResult(status=ResultStatus.invalid_input, warnings=["Zapytanie nie zawiera słów do wyszukania."])
    fts_kinds = None
    if kind_enums:
        fts_kinds = sorted({"judgment" if k in (SourceKind.judgment, SourceKind.eu_judgment) else "provision" for k in kind_enums})
    rows = store.fts_search(expr, fts_kinds, limit * 4 + 1, offset)
    for ref, kind, document_id, score, _body in rows:
        if len(hits) >= limit:
            break
        if filters.get("document_id") and document_id != filters["document_id"]:
            continue
        if kind == "judgment":
            j = store.get_judgment(document_id)
            if not j or not _judgment_passes(j, filters):
                continue
            hits.append(_judgment_hit(store, j, store.get_document(document_id), words, score))
        else:
            p = store.get_provision(ref)
            doc = store.get_document(document_id)
            if not p or not doc or (kind_enums and doc.kind not in kind_enums):
                continue
            ts, reasons = _temporal(store, p, when)
            snap = store.get_snapshot(p.snapshot_id)
            hits.append(SearchHit(
                document_id=document_id, kind=doc.kind, title=doc.title, locator=p.locator, version_id=p.version_id,
                snippet=_snippet(p.text, words), original_url=doc.original_url, snapshot_id=p.snapshot_id,
                fetched_at=snap.fetched_at if snap else None, score=round(-score, 3),
                metadata={"version_label": p.version_label, "temporal_status": ts.value, "temporal_notes": reasons}))
    more = len(rows) > limit
    status = ResultStatus.ok if hits else ResultStatus.not_found
    if not hits:
        warnings.append("Brak trafień w lokalnym korpusie (KC, upk, dyrektywa 2011/83/UE, próbka SAOS). "
                        "To nie oznacza, że przepis lub orzeczenie nie istnieje.")
    if cov.sources_unavailable:
        warnings.append(f"Niedostępne/niezsynchronizowane źródła: {', '.join(cov.sources_unavailable)} — wynik niepełny.")
    return ToolResult(status=status, coverage=cov, warnings=warnings, data={
        "hits": [h.model_dump(mode="json") for h in hits],
        "next_cursor": str(offset + limit * 4) if more else None,
        "search_scope": "FTS5/BM25 po lokalnym korpusie; bez wyszukiwania semantycznego",
    })


def _judgment_passes(j, filters: dict) -> bool:
    if filters.get("court_type") and j.court_type != filters["court_type"]:
        return False
    df, dt = _parse_date(filters.get("date_from")), _parse_date(filters.get("date_to"))
    if (df or dt) and not j.judgment_date:
        return False
    if df and j.judgment_date < df:
        return False
    if dt and j.judgment_date > dt:
        return False
    return True


def _judgment_hit(store: Store, j, doc, terms: list[str], score: float | None = None) -> SearchHit:
    snap = store.get_snapshot(j.snapshot_id)
    return SearchHit(
        document_id=j.document_id, kind=SourceKind.judgment,
        title=f"{j.court_name}, {j.judgment_type}, {j.judgment_date or 'data nieustalona'}, {', '.join(j.case_numbers)}",
        snippet=_snippet(j.text, terms), original_url=j.original_url or (doc.original_url if doc else ""),
        snapshot_id=j.snapshot_id, fetched_at=snap.fetched_at if snap else None,
        score=round(-score, 3) if score is not None else None,
        metadata={"court": j.court_name, "case_numbers": j.case_numbers, "judgment_date": str(j.judgment_date),
                  "finality": j.finality, "data_quality_flags": j.data_quality_flags,
                  "note": "Fragment może pochodzić ze stanowiska strony, nie z oceny sądu — sprawdź kontekst."})


def _extract_unit(article_text: str, loc: str) -> str | None:
    """Best-effort extraction of § / ust. / pkt from an article text. None if not reliable."""
    m = re.search(r"(§\s*(\S+)|ust\.\s*(\S+))(?:\s+pkt\s+(\S+))?", loc)
    if not m:
        return None
    text = article_text
    if m.group(2):
        pat = rf"(?:^|\n|\s)§\s*{re.escape(m.group(2))}\.\s"
        nxt = r"(?:\n|\s)§\s*\d+[a-z]?(?:\^\d+)?\.\s"
    else:
        pat = rf"(?:^|\n|^Art\.\s*\S+\.)\s*{re.escape(m.group(3))}\.\s"
        nxt = r"\n\s*\d+[a-z]?(?:\^\d+)?\.\s"
    s = re.search(pat, text)
    if not s:
        return None
    rest = text[s.start():]
    e = re.search(nxt, rest[len(s.group(0)):])
    unit = rest[: len(s.group(0)) + e.start()] if e else rest
    if m.group(4):
        ps = re.search(rf"(?:^|\n)\s*{re.escape(m.group(4))}\)\s", unit)
        if not ps:
            return None
        prest = unit[ps.start():]
        pe = re.search(r"\n\s*\d+[a-z]?(?:\^\d+)?\)\s", prest[len(ps.group(0)):])
        unit = prest[: len(ps.group(0)) + pe.start()] if pe else prest
    return unit.strip()


def get_legal_document(
    store: Store, document_id: str, locator: str | None = None, as_of: str | None = None,
    snapshot_id: str | None = None, cursor: str | None = None,
) -> ToolResult:
    try:
        when = _parse_date(as_of)
        offset = int(cursor) if cursor else 0
    except ValueError as e:
        return ToolResult(status=ResultStatus.invalid_input, warnings=[f"Błędne parametry: {e}"])
    cov = _coverage(store)
    doc = store.get_document(document_id)
    if not doc:
        return ToolResult(status=ResultStatus.not_found, coverage=cov, warnings=[
            f"Dokument {document_id} nie występuje w lokalnym korpusie. Nie oznacza to, że nie istnieje; "
            "nie cytuj go z pamięci."])
    snap = store.get_snapshot(doc.snapshot_id)
    base = {"document_id": doc.document_id, "kind": doc.kind.value, "title": doc.title,
            "publication": doc.publication, "original_url": doc.original_url,
            "fetched_at": snap.fetched_at.isoformat() if snap else None}

    if doc.kind == SourceKind.judgment:
        j = store.get_judgment(document_id)
        if not j:
            return ToolResult(status=ResultStatus.not_found, coverage=cov, warnings=["Brak treści orzeczenia w korpusie."])
        page = j.text[offset: offset + JUDGMENT_PAGE_CHARS]
        nxt = offset + JUDGMENT_PAGE_CHARS if offset + JUDGMENT_PAGE_CHARS < len(j.text) else None
        return ToolResult(status=ResultStatus.ok, coverage=cov, data={
            **base, **j.model_dump(mode="json", exclude={"text"}), "text": page,
            "text_range": [offset, offset + len(page)], "text_length": len(j.text),
            "next_cursor": str(nxt) if nxt else None,
        }, warnings=([f"Tekst podzielony na strony; pobierz resztę z cursor={nxt}."] if nxt else [])
            + [f"Uwaga jakości danych: {f}" for f in j.data_quality_flags])

    if not locator:
        versions = store.list_versions(document_id)
        return ToolResult(status=ResultStatus.ok, coverage=cov, data={
            **base, "versions": versions, "metadata": doc.metadata,
            "hint": "Podaj locator, np. 'art. 27', aby pobrać dokładny tekst."})

    loc = canonical_locator(locator)
    if not loc:
        return ToolResult(status=ResultStatus.invalid_input, warnings=[f"Nierozpoznany lokalizator: {locator!r}."])
    art = article_of(loc)
    provs = store.get_provisions(document_id, art)
    if snapshot_id:
        provs = [p for p in provs if p.snapshot_id == snapshot_id]
    if not provs:
        return ToolResult(status=ResultStatus.not_found, coverage=cov, warnings=[
            f"{art} nie znaleziono w lokalnej wersji {document_id}"
            + (f" (snapshot {snapshot_id})" if snapshot_id else "")
            + ". Możliwe przyczyny: przepis nie istnieje, błąd parsowania (np. indeks górny), inny snapshot."])
    p = _latest(provs)
    warnings: list[str] = []
    text = p.text
    if loc != art:
        unit = _extract_unit(p.text, loc)
        if unit:
            text = unit
        else:
            warnings.append(f"Nie wyodrębniono jednostki {loc}; zwrócono cały {art}.")
    ts, reasons = _temporal(store, p, when)
    warnings += reasons
    if p.excluded_provisions:
        warnings.append("Obwieszczenie TJ wymienia przepisy nieobjęte tekstem jednolitym (m.in. przejściowe) — sprawdź excluded_provisions.")
    status = ResultStatus.temporal_unknown if ts == TemporalStatus.unknown else ResultStatus.ok
    return ToolResult(status=status, coverage=cov, warnings=warnings, data={
        **base, "locator": loc if text is not p.text else art, "requested_locator": loc, "text": text,
        "version_id": p.version_id, "version_label": p.version_label,
        "text_state_date": str(p.text_state_date) if p.text_state_date else None,
        "temporal_status": ts.value, "temporal_notes": reasons,
        "pending_changes": p.pending_changes, "excluded_provisions": p.excluded_provisions,
        "snapshot_id": p.snapshot_id, "page_hint": p.page_hint,
        "other_versions": sorted({x.version_id for x in provs} - {p.version_id}),
    })


def sources_status(store: Store) -> ToolResult:
    sources = store.get_sources()
    warnings = _stale_warnings(store)
    if not sources:
        warnings.append("Brak zsynchronizowanych źródeł. Uruchom `prawnik-mcp sync`.")
    return ToolResult(status=ResultStatus.ok, warnings=warnings, coverage=_coverage(store), data={
        "sources": [s.model_dump(mode="json") for s in sources],
        "counts": store.stats(),
        "supported_area": "Wąskie sprawy cywilne i konsumenckie: płatności, zakupy, reklamacje, odstąpienie od umowy na odległość.",
        "not_supported": ["pozwy, apelacje, kasacje", "obliczanie terminów procesowych i przedawnienia",
                          "podatki", "sprawy karne, rodzinne, migracyjne", "nieruchomości",
                          "pełna historia brzmień przepisów (tylko bieżący TJ)"],
    })


def check_citations_tool(
    store: Store, claims: list[dict], evidence: list[dict], relevant_date: str | None = None,
    binding: dict | None = None, client_review: dict | None = None,
) -> ToolResult:
    from prawnik_mcp.evidence.citations import check_citations

    try:
        cl = [Claim.model_validate(c) for c in claims]
        ev = [EvidenceSpan.model_validate(e) for e in evidence]
        when = _parse_date(relevant_date)
    except (ValidationError, ValueError) as e:
        return ToolResult(status=ResultStatus.invalid_input, warnings=[f"Błędne dane wejściowe: {e}"])
    if binding and "binding_hash" not in binding and binding.get("template_id"):
        from prawnik_mcp.documents.render import binding_hash
        from prawnik_mcp.documents.templates import get_template

        t = get_template(binding["template_id"])
        if t is None:
            return ToolResult(status=ResultStatus.invalid_input, warnings=[f"Nieznany szablon {binding['template_id']!r}."])
        binding = {"binding_hash": binding_hash(binding["template_id"], t["version"], binding.get("facts") or {},
                                                binding.get("draft"))}
    ids = [e.evidence_id for e in ev]
    if len(ids) != len(set(ids)):
        return ToolResult(status=ResultStatus.invalid_input, warnings=["Zduplikowane evidence_id."])
    report = check_citations(store, cl, ev, relevant_date=when, binding=binding, client_review=client_review)
    store.save_report(report.model_dump_json(), report.report_id)
    warnings = []
    if report.critical_errors:
        warnings.append(f"Błędy krytyczne: {len(report.critical_errors)}. Eksport wypełnionego pisma będzie zablokowany.")
    warnings.append(report.note)
    return ToolResult(status=ResultStatus.ok, data=report.model_dump(mode="json"), warnings=warnings, coverage=_coverage(store))


def get_document_template(template_id: str) -> ToolResult:
    from prawnik_mcp.documents.templates import get_template, list_templates

    if not template_id or template_id == "list":
        return ToolResult(status=ResultStatus.ok, data={"templates": list_templates()})
    t = get_template(template_id)
    if t is None:
        return ToolResult(status=ResultStatus.not_found, warnings=[f"Nieznany szablon {template_id!r}."],
                          data={"templates": [x.get("template_id") for x in list_templates()]})
    return ToolResult(status=ResultStatus.ok, data=t if isinstance(t, dict) else t.model_dump(mode="json"),
                      warnings=["Szablon eksperymentalny — nieoceniony przez prawnika."])


def render_document_tool(store: Store, template_id: str, facts: dict, draft: dict | None = None,
                         report_id: str | None = None, out_dir: Path | None = None) -> ToolResult:
    from prawnik_mcp.documents.render import render_document

    return render_document(store, template_id, facts or {}, draft, report_id, out_dir or store.data_dir / "exports")
