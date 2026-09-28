"""Tool logic shared by the MCP server, the CLI and the eval runner.

No generative model is used here. Every result is a `ToolResult` with an explicit status.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from prawnik_mcp import sources
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

def _act_aliases() -> dict[str, str]:
    """Act aliases -> logical document ids (from the source catalog)."""
    return sources.act_aliases()


def _kind_sources(kinds: list[SourceKind]) -> set[str]:
    return {sid for k in kinds for sid in sources.sources_for_kind(k.value)}


# Document kinds stored as full-text records (Judgment model): court judgments, authority decisions,
# tax interpretations. They share case-number lookup, paging and the judgment hit format.
RECORD_KINDS = (SourceKind.judgment, SourceKind.eu_judgment, SourceKind.decision, SourceKind.tax_ruling)

_STOP = set(
    "a aby ale albo ani by być czy do dla go i ich jak jaki jest jeśli już lub ma może na nie nie o od oraz po "
    "przez przy się są ta tak te to tu w we z za ze że jako który która które co czy mój moja mnie mi".split()
)
_CASE_RE = re.compile(
    r"\b("
    r"[IVXL]{1,5}\s+[A-Za-zŁłŻżŚśĆć]{1,6}(?:-[A-Za-z]+)?(?:/[A-Z][a-zł]{0,2})?\s+\d{1,6}/\d{2,4}"  # I ACa 772/13, II SA/Wa 1553/24
    r"|KIO(?:/[A-Z]{1,3})?\s+\d{1,5}/\d{2,4}"  # KIO 1234/24
    r"|[A-Z]{2,5}\.\d{3,4}\.\d{1,5}\.\d{4}"  # UODO: DKN.5130.2215.2020
    r"|\d{4}-[A-Z0-9]{3,8}(?:-\d)?(?:\.\d+)*\.\d{4}(?:\.\d+)?(?:\.[A-Z]{1,4})?"  # KIS: 0114-KDIP1-2.4012.123.2024.1.AB
    r")\b")
_ART_RE = re.compile(r"\bart\.?\s*\d+[a-z]?(?:\s*\^\s*\d+|\(\d+\)|[¹²³⁰-⁹]+)?(?:\s*(?:§|ust\.?|pkt|lit\.?)\s*\w+)*", re.I)


def _now() -> datetime:
    return datetime.now(UTC)


def _coverage(store: Store, kinds: list[SourceKind] | None = None) -> Coverage:
    wanted = _kind_sources(kinds) if kinds else None
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


_DZU_RE = re.compile(r"\bDz\.?\s*U\.?\s*(?:z\s*)?(\d{4})\s*(?:r\.)?\s*,?\s*poz\.?\s*(\d{1,5})", re.I)
_ELI_RE = re.compile(r"\b(DU|MP)/(\d{4})/(\d{1,5})\b")


def _resolve_act(text: str) -> str | None:
    """Act id from an alias ('upk', 'kc'), a Dz.U. / ELI reference or an EU identifier."""
    low = text.lower()
    aliases = _act_aliases()
    for alias in sorted(aliases, key=len, reverse=True):
        if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", low):
            return aliases[alias]
    if m := _ELI_RE.search(text):
        return f"eli:{m.group(1)}/{m.group(2)}/{m.group(3)}"
    if m := _DZU_RE.search(text):
        return f"eli:DU/{m.group(1)}/{m.group(2)}"
    from prawnik_mcp.connectors.cellar import celex_candidates

    cands = celex_candidates(text)
    return f"celex:{cands[0]}" if len(cands) == 1 else None


def _latest(provs: list[ProvisionVersion]) -> ProvisionVersion:
    return sorted(provs, key=lambda p: (p.text_state_date or date.min, p.version_id))[-1]


# ---------------------------------------------------------------------------------------------- tools


def search_legal(
    store: Store, query: str, kinds: list[str] | None = None, filters: dict | None = None,
    relevant_date: str | None = None, cursor: str | None = None, limit: int = DEFAULT_LIMIT,
    live: bool | None = None,
) -> ToolResult:
    """Local FTS first; live source search when `live=True`, or automatically when local hits are fewer
    than `limit` (`live=None`). `live=False` or PRAWNIK_MCP_OFFLINE=1 keeps it strictly local."""
    from prawnik_mcp import live as live_mod

    filters = filters or {}
    use_live = live is not False and live_mod.live_enabled()
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

    empty = store.stats()["documents"] == 0
    if empty and not use_live:
        return ToolResult(status=ResultStatus.source_unavailable, coverage=cov, warnings=warnings + [
            "Lokalny korpus jest pusty. Uruchom `prawnik-mcp sync` (lub `sync --offline`). Brak wyników nie oznacza braku przepisów."])
    if empty:
        warnings.append("Lokalny korpus jest pusty — wyniki wyłącznie z wyszukiwania na żywo.")

    hits: list[SearchHit] = []

    # 1. exact identifiers: case numbers
    case = _CASE_RE.search(query)
    if case and (not kind_enums or any(k in RECORD_KINDS for k in kind_enums)):
        found = store.find_judgments_by_case_number(case.group(1))
        if not found and use_live:
            remote, lw, unavailable = live_mod.live_search(
                store, query, kinds={k.value for k in RECORD_KINDS}, filters={**filters, "case_number": case.group(1)},
                limit=limit)
            cov.sources_unavailable += [u for u in unavailable if u not in cov.sources_unavailable]
            if remote:
                rh = [_remote_hit(sid, h, origin) for sid, h, origin in remote]
                status = ResultStatus.ambiguous if len(rh) > 1 else ResultStatus.ok
                return ToolResult(status=status, coverage=cov, warnings=warnings + lw + [
                    "Wynik z wyszukiwania na żywo (nie zapisany lokalnie). Pełny tekst i snapshot: get_legal_document."]
                    + (["Ta sama sygnatura występuje w kilku dokumentach — rozróżnij po sądzie, dacie i rodzaju."]
                       if len(rh) > 1 else []),
                    data={"hits": [h.model_dump(mode="json") for h in rh], "next_cursor": None})
            warnings += lw
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
            res = get_legal_document(store, act, loc, relevant_date, live=live if use_live else False)
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
        fts_kinds = sorted({"judgment" if k in RECORD_KINDS else "provision" for k in kind_enums})
    rows = [] if empty else store.fts_search(expr, fts_kinds, limit * 4 + 1, offset)
    for ref, kind, document_id, score, _body in rows:
        if len(hits) >= limit:
            break
        if filters.get("document_id") and document_id != filters["document_id"]:
            continue
        if kind == "judgment":
            j = store.get_judgment(document_id)
            jdoc = store.get_document(document_id)
            if not j or not _judgment_passes(j, filters) or (kind_enums and jdoc and jdoc.kind not in kind_enums):
                continue
            hits.append(_judgment_hit(store, j, jdoc, words, score))
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
                metadata={"version_label": p.version_label, "temporal_status": ts.value, "temporal_notes": reasons,
                          "origin": "local"}))
    more = len(rows) > limit
    if use_live and offset == 0 and (live is True or len(hits) < limit):
        remote, lw, unavailable = live_mod.live_search(
            store, query, kinds={k.value for k in kind_enums} if kind_enums else None, filters=filters, limit=limit)
        warnings += lw
        cov.sources_unavailable += [u for u in unavailable if u not in cov.sources_unavailable]
        seen = {h.document_id for h in hits}
        for sid, h, origin in remote:
            if h.document_id not in seen and len(hits) < limit + (limit if live is True else 0):
                hits.append(_remote_hit(sid, h, origin))
                seen.add(h.document_id)
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


def _remote_hit(source_id: str, h, origin: str) -> SearchHit:
    try:
        kind = SourceKind(h.kind)
    except ValueError:
        kind = SourceKind.statute
    flags = []
    jd = str(h.metadata.get("judgment_date") or h.metadata.get("date") or "")
    if jd[:10] > date.today().isoformat():
        flags.append(f"date_in_future:{jd[:10]}")  # source data error; do not rely on this date
    return SearchHit(
        document_id=h.document_id, kind=kind, title=h.title, snippet=_snippet(h.snippet, []),
        original_url=h.original_url, snapshot_id="", metadata={
            **h.metadata, "origin": origin, "source_id": source_id, "data_quality_flags": flags,
            "note": "Nie zapisane lokalnie — przed cytowaniem pobierz przez get_legal_document (snapshot, wersja)."})


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
        document_id=j.document_id, kind=doc.kind if doc else SourceKind.judgment,
        title=f"{j.court_name}, {j.judgment_type}, {j.judgment_date or 'data nieustalona'}, {', '.join(j.case_numbers)}",
        snippet=_snippet(j.text, terms), original_url=j.original_url or (doc.original_url if doc else ""),
        snapshot_id=j.snapshot_id, fetched_at=snap.fetched_at if snap else None,
        score=round(-score, 3) if score is not None else None,
        metadata={"court": j.court_name, "case_numbers": j.case_numbers, "judgment_date": str(j.judgment_date),
                  "finality": j.finality, "data_quality_flags": j.data_quality_flags, "origin": "local",
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
    snapshot_id: str | None = None, cursor: str | None = None, live: bool | None = None,
) -> ToolResult:
    """Exact text from the local store; a document missing locally is fetched from its source first
    (unless `live=False` or PRAWNIK_MCP_OFFLINE=1)."""
    from prawnik_mcp import live as live_mod

    notes: list[str] = []
    if live is not False and live_mod.live_enabled() and store.get_document(document_id) is None:
        stored, note = live_mod.lazy_fetch(store, document_id)
        notes += [note] if note else []
        if stored:
            notes.append(f"{document_id}: pobrano ze źródła na żądanie i zapisano lokalnie (snapshot).")
    res = _get_legal_document_local(store, document_id, locator, as_of, snapshot_id, cursor)
    res.warnings = notes + res.warnings
    return res


def _get_legal_document_local(
    store: Store, document_id: str, locator: str | None, as_of: str | None,
    snapshot_id: str | None, cursor: str | None,
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

    if doc.kind in RECORD_KINDS:
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
    synced = store.get_sources()
    warnings = _stale_warnings(store)
    if not synced:
        warnings.append("Brak zsynchronizowanych źródeł. Uruchom `prawnik-mcp sync`.")
    by_source = store.stats_by_source()
    catalog = [{
        "source_id": s.source_id, "name": s.name, "maturity": s.maturity, "implemented": s.implemented,
        "kinds": list(s.kinds), "terms_url": s.terms_url, "rate_per_s": s.rate_per_s,
        "local_counts": by_source.get(s.source_id, {}),
    } for s in sources.catalog().values()]
    return ToolResult(status=ResultStatus.ok, warnings=warnings, coverage=_coverage(store), data={
        "sources": [s.model_dump(mode="json") for s in synced],
        "catalog": catalog,
        "sync_state": store.list_sync_state(),
        "counts": store.stats(),
        "schema_version": store.schema_version,
        "search_and_retrieval": "Polish and EU statutes and judgments from the implemented sources (see catalog).",
        "letter_templates_and_analysis": "Only narrow civil/consumer matters: payment demand, consumer complaint, "
                                         "withdrawal from a distance contract.",
        "not_supported": ["drafting pleadings (pozwy, apelacje, kasacje)", "procedural deadlines and limitation periods",
                          "full history of statute wordings (latest consolidated text only)",
                          "sources marked maturity=research in the catalog"],
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


def get_citations(store: Store, document_id: str, direction: str = "both", locator: str | None = None,
                  limit: int = 20, cursor: str | None = None) -> ToolResult:
    """Outgoing citations of a document (statutes and judgments it cites) and incoming citations
    (local documents citing it). Unresolved targets are kept with an explicit reason."""
    if direction not in ("both", "outgoing", "incoming"):
        return ToolResult(status=ResultStatus.invalid_input, warnings=["direction: both | outgoing | incoming"])
    try:
        offset = int(cursor) if cursor else 0
    except ValueError:
        return ToolResult(status=ResultStatus.invalid_input, warnings=["Błędny cursor."])
    loc = canonical_locator(locator) if locator else None
    limit = max(1, min(limit, 100))
    data: dict[str, Any] = {"document_id": document_id}
    warnings: list[str] = []
    doc = store.get_document(document_id)
    if direction in ("both", "outgoing"):
        if not doc:
            warnings.append(f"{document_id} nie występuje lokalnie — powołań wychodzących nie da się ustalić "
                            "(pobierz dokument przez get_legal_document).")
        grouped: dict[tuple[str, str], dict] = {}
        for e in store.citations_from(document_id):
            key = (e["kind"], e["target"])
            g = grouped.setdefault(key, {"target": e["target"], "kind": e["kind"], "locators": [], "raw": e["raw"]})
            if e["target_locator"] and e["target_locator"] not in g["locators"]:
                g["locators"].append(e["target_locator"])
        out = []
        for g in grouped.values():
            if g["target"].startswith("case:"):
                g["status"], g["reason"] = "unresolved", "no_source_id (sygnatura bez identyfikatora w źródle)"
            else:
                tdoc = store.get_document(g["target"])
                g["status"] = "in_corpus" if tdoc else "out_of_corpus"
                if tdoc:
                    g["title"] = tdoc.title
                else:
                    g["reason"] = "out_of_corpus (można pobrać przez get_legal_document)"
            out.append(g)
        data["outgoing"] = out
    if direction in ("both", "incoming"):
        rows, total = store.citations_to(document_id, loc, limit=limit, offset=offset)
        incoming = []
        for r in rows:
            j = store.get_judgment(r["src"])
            d = store.get_document(r["src"])
            incoming.append({"document_id": r["src"], "locators": r["locators"],
                             "title": d.title if d else None,
                             "court": j.court_name if j else None,
                             "judgment_date": str(j.judgment_date) if j and j.judgment_date else None,
                             "case_numbers": j.case_numbers if j else []})
        data["incoming"] = incoming
        data["incoming_total"] = total
        data["next_cursor"] = str(offset + limit) if offset + limit < total else None
        warnings.append("Powołania przychodzące obejmują tylko lokalny korpus (zsynchronizowane orzeczenia); "
                        "brak powołań nie oznacza, że przepis nie był stosowany w orzecznictwie.")
    has_any = bool(data.get("outgoing")) or bool(data.get("incoming"))
    return ToolResult(status=ResultStatus.ok if (doc or has_any) else ResultStatus.not_found, data=data,
                      warnings=warnings, coverage=_coverage(store))


def list_act_versions(store: Store, document_id: str, live: bool | None = None) -> ToolResult:
    """Timeline of a Polish act: consolidated texts (TJ) announced, which of them are parsed locally,
    and amending acts with their ELI dates (future dates = pending)."""
    if not document_id.startswith("eli:"):
        return ToolResult(status=ResultStatus.out_of_scope,
                          warnings=["list_act_versions obsługuje akty polskie (eli:DU/…); dla aktów UE brak osi wersji."])
    from prawnik_mcp import live as live_mod

    notes: list[str] = []
    if store.get_document(document_id) is None and live is not False and live_mod.live_enabled():
        stored, note = live_mod.lazy_fetch(store, document_id)
        notes += [note] if note else []
    doc = store.get_document(document_id)
    if not doc:
        return ToolResult(status=ResultStatus.not_found, warnings=notes + [
            f"{document_id} nie występuje lokalnie. Nie oznacza to, że akt nie istnieje."])
    md = doc.metadata
    today = date.today().isoformat()
    amendments = sorted(md.get("amendments") or [], key=lambda a: a.get("date") or "", reverse=True)
    for a in amendments:
        a["pending"] = bool(a.get("date") and a["date"] > today)
    parsed = {v.get("eli") for v in md.get("consolidated_versions") or []}
    return ToolResult(status=ResultStatus.ok, warnings=notes + [
        "Lokalnie dostępny jest tylko tekst najnowszego obwieszczenia TJ; wcześniejsze brzmienia nie są odtwarzane.",
        "Daty zmian pochodzą z odsyłaczy ELI (jedna data na akt zmieniający); część przepisów może wchodzić w życie w innych terminach."],
        data={
            "document_id": document_id, "title": doc.title, "publication": doc.publication,
            "status": md.get("status"), "in_force": md.get("in_force"), "entry_into_force": md.get("entry_into_force"),
            "current_version_id": md.get("current_version_id"),
            "consolidated_texts": [{"eli": e, "parsed_locally": e in parsed} for e in md.get("consolidated_text_refs") or []],
            "parsed_versions": [{k: v.get(k) for k in ("version_id", "publication", "state_date", "announcement_date",
                                                        "pending_changes", "provision_count")}
                                for v in md.get("consolidated_versions") or []],
            "amendments": amendments,
            "pending_amendments": [a for a in amendments if a["pending"]],
        })
