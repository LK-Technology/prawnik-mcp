"""TK (Trybunał Konstytucyjny) connector: Constitutional Tribunal rulings from trybunal.gov.pl.

SAOS holds TK rulings only up to 2015; the Tribunal's own services are the current source. The main TYPO3
site (trybunal.gov.pl) publishes every ruling as an article with the **operative part only**; the full text
with the reasoning (uzasadnienie) is on the Tribunal's portal IPO (ipo.trybunal.gov.pl), which `fetch` adds
(see below). The official collection (otkzu.trybunal.gov.pl) is not used. All endpoints verified live
2026-09-28:

- ruling: `GET /postepowanie-i-orzeczenia/{wyroki|postanowienia}/art/{slug}` (1 request per ruling; the raw
  page is the snapshot; unknown slugs answer 404);
- case number: `GET /s/{sig}` (the site's short link: `sk-20-25`, older cases `p-3512`; unknown → 404),
  whose "Wyrok"/"Postanowienie" groups link to the ruling articles;
- phrase search: `GET /wyszukiwarka?tx_solr[q]=...&tx_solr[filter][i]=category:Wyrok|Postanowienie&tx_solr[page]=N`
  (EXT:solr, relevance order, 10 hits per page);
- listings: `GET /postepowanie-i-orzeczenia/{wyroki|postanowienia}`, newest first, pager links carry a TYPO3
  `cHash` and are followed as-is (wyroki back to 2002, postanowienia similar).

Full text (verified live 2026-09-28, robots.txt of IPO answers 404, i.e. no restrictions):

- `GET https://ipo.trybunal.gov.pl/ipo/Sprawa?pokaz=dokumenty&sygnatura=K%201/20` – the deep link the ruling
  articles themselves carry. One request, no cookies/ViewState/JavaScript needed: the JSF page is rendered
  server-side, one tab per ruling of the case, each with the full text (komparycja, tenor, uzasadnienie,
  then the dissenting opinions). The tab is matched to the article by kind (wyrok/postanowienie) and date.
  The server answers **HTTP/2 only**: an HTTP/1.1 request is accepted and never answered (this is why earlier
  recon "did not respond"), so `PoliteClient` negotiates HTTP/2 (`httpx[http2]`);
- unknown case numbers answer 200 with a redirect to `/ipo/exception/sprawaId.xhtml`; any IPO failure leaves
  the operative-part record and flags it `ipo_full_text_unavailable`.

Document ids are `tk:<section>/<slug>` (e.g. `tk:wyroki/11300-planowanie-rodziny-...`): the slug is the only
stable key the site exposes on every page (the tt_news uid shows only in search results and older slugs, and
no uid-only URL works without a cHash). Polite rate: catalog `rate_per_s` (0.5 req/s).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime, scope_key
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient, UpstreamError
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.tk import (
    COURT_NAME,
    COURT_TYPE,
    DOC_TYPES_PL,
    ID_RE,
    PARSER_VERSION,
    SECTIONS,
    TEXT_SCOPE,
    TEXT_SCOPE_FULL,
    IpoDocument,
    TkCasePage,
    TkLink,
    TkSearchPage,
    as_signature,
    case_slugs,
    case_url,
    doc_id,
    ipo_case_url,
    listing_url,
    normalize_signature,
    parse_case_page,
    parse_ipo_case,
    parse_listing,
    parse_ruling_page,
    parse_search,
    parse_tk_ruling,
    path_to_id,
    ruling_url,
    search_url,
    select_ipo_document,
)
from prawnik_mcp.store import Store

SOURCE_ID = "tk"
ACCEPT_HTML = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
IPO_CONTENT_TYPE = "text/html; charset=UTF-8"
MAX_IPO_CASE_NUMBERS = 2  # joined cases list several numbers; try at most the first two on IPO
CONTENT_TYPE = "text/html; charset=utf-8"
MAX_SEARCH_PAGES = 3  # live search budget: at most 3 Solr pages
QUIET_FLAGS = {"reasoning_not_included"}  # rulings without reasoning; an IPO failure has its own flag
_JTYPE_SECTION = {"SENTENCE": "wyroki", "WYROK": "wyroki", "DECISION": "postanowienia",
                  "POSTANOWIENIE": "postanowienia"}


def _iso(d: Any) -> date | None:
    if d is None or d == "":
        return None
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def _in_window(d: date | None, lo: date | None, hi: date | None) -> bool:
    """Without a window everything passes; with one, an unknown date does not."""
    if not (lo or hi):
        return True
    if d is None:
        return False
    return (not lo or d >= lo) and (not hi or d <= hi)


def search_page(client: PoliteClient, url: str) -> TkSearchPage:
    return parse_search(client.get(url, accept=ACCEPT_HTML).content)


def case_page(client: PoliteClient, signature: str) -> TkCasePage | None:
    """Case page for a canonical case number: the current short-link form first, then the older compact
    form (1–2 requests). None when both answer 404 (the site does not know the case)."""
    for slug in case_slugs(signature):
        try:
            r = client.get(case_url(slug), accept=ACCEPT_HTML)
        except NotFoundUpstream:
            continue
        return parse_case_page(r.content)
    return None


def _listing_for(store: Store, did: str) -> dict | None:
    doc = store.get_document(did)
    return (doc.metadata.get("listing") or None) if doc else None


@dataclass
class IpoFetch:
    """Outcome of the IPO lookup for one ruling: the matching tab plus the raw page to snapshot, or an error."""

    doc: IpoDocument | None = None
    raw: bytes | None = None
    url: str | None = None
    fetched_at: datetime | None = None
    case_number: str | None = None
    error: str | None = None
    fetched_now: bool = False
    notes: list[str] = field(default_factory=list)


def _match_ipo(raw: bytes, sig: str, section: str, jdate: date | None) -> IpoDocument:
    """Validate an IPO case page against the wanted case and ruling; ValueError says why it cannot be used."""
    case = parse_ipo_case(raw)
    if normalize_signature(case.signature) != sig:
        raise ValueError(f"IPO returned case {case.signature!r}, wanted {sig!r}")
    doc = select_ipo_document(case, section, jdate)
    if doc is None:
        raise ValueError(f"no single {section} tab dated {jdate} in the IPO case {sig} "
                         f"({'; '.join(d.label for d in case.documents)})")
    return doc


def lookup_ipo(store: Store | None, client: PoliteClient | None, page: dict, section: str, *, force: bool = False,
               pages: dict[str, tuple[bytes, datetime]] | None = None) -> IpoFetch:
    """Find the full text of a ruling on IPO (1 request per case number tried, usually 1).

    Sources of the IPO page, in order: recorded `pages` (offline fixtures, by case number), the stored snapshot
    of the same URL (unless `force`), the live portal (needs `client`). Never raises for upstream trouble:
    the error is returned so the caller can keep the operative-part record and flag it."""
    sigs = page["case_numbers"][:MAX_IPO_CASE_NUMBERS]
    if not sigs:
        return IpoFetch(error="no case number on the ruling page; IPO lookup impossible")
    errors: list[str] = []
    for sig in sigs:
        url = ipo_case_url(sig)
        raw: bytes | None = None
        fetched_at: datetime | None = None
        now = False
        if pages is not None:
            if sig not in pages:
                continue
            raw, fetched_at = pages[sig]
        else:
            snap = store.find_snapshot_by_url(url) if (store and not force) else None
            raw = store.read_snapshot_bytes(snap.snapshot_id) if (snap and store) else None
            fetched_at = snap.fetched_at if snap else None
            if raw is None:
                if client is None:
                    continue
                try:
                    r = client.get(url, accept=ACCEPT_HTML)
                except NotFoundUpstream:
                    errors.append(f"{sig}: 404 on IPO")
                    continue
                except UpstreamError as e:
                    errors.append(f"{sig}: {e.reason}")
                    continue
                raw, fetched_at, now = r.content, r.fetched_at, True
        try:
            doc = _match_ipo(raw, sig, section, page["judgment_date"])
        except ValueError as e:
            errors.append(f"{sig}: {e}")
            continue
        return IpoFetch(doc=doc, raw=raw, url=url, fetched_at=fetched_at, case_number=sig, fetched_now=now)
    return IpoFetch(error="; ".join(errors) if errors else None)


def ingest_ruling(store: Store, section: str, slug: str, content: bytes, url: str, fetched_at: datetime | None,
                  listing: dict | None = None, content_type: str = CONTENT_TYPE,
                  ipo: IpoFetch | None = None) -> Judgment:
    parse_ruling_page(content)  # validate before anything is written (a 200 without the article = layout change)
    snap = store.put_snapshot(SOURCE_ID, url, content, content_type, parser_version=PARSER_VERSION,
                              fetched_at=fetched_at)
    ipo_doc, ipo_info = None, {}
    if ipo and ipo.doc and ipo.raw is not None and ipo.url:
        isnap = store.put_snapshot(SOURCE_ID, ipo.url, ipo.raw, IPO_CONTENT_TYPE, parser_version=PARSER_VERSION,
                                   fetched_at=ipo.fetched_at)
        ipo_doc = ipo.doc
        ipo_info = {"url": ipo.url, "case_number": ipo.case_number, "snapshot_id": isnap.snapshot_id,
                    "sha256": isnap.sha256, "fetched_at": isnap.fetched_at}
    elif ipo and ipo.error:
        ipo_info = {"error": ipo.error}
    judgment, doc = parse_tk_ruling(content, section, slug, snapshot_id=snap.snapshot_id, sha256=snap.sha256,
                                    fetched_at=snap.fetched_at, listing=listing, ipo=ipo_doc, ipo_info=ipo_info)
    store.upsert_document(doc)
    store.upsert_judgment(judgment, title=doc.title)
    return judgment


def fetch_ruling(store: Store, client: PoliteClient, section: str, slug: str, *, force: bool = False,
                 listing: dict | None = None) -> tuple[Judgment, bool]:
    """Returns (judgment, fetched_now). Stored snapshots are re-parsed instead of re-fetched.

    Requests: 1 for the article + 1 for the IPO case page (full text with reasoning). If IPO fails, the
    operative-part record is stored and flagged `ipo_full_text_unavailable`; a later fetch retries IPO only.
    An unknown slug gives `NotFoundUpstream` (the site answers 404); nothing is stored."""
    url = ruling_url(section, slug)
    listing = listing or _listing_for(store, doc_id(section, slug))
    content, fetched_at, ctype, now = None, None, CONTENT_TYPE, False
    if not force:
        snap = store.find_snapshot_by_url(url)
        content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
        if snap and content is not None:
            fetched_at, ctype = snap.fetched_at, snap.content_type
    if content is None:
        r = client.get(url, accept=ACCEPT_HTML)
        content, fetched_at, ctype, now = r.content, r.fetched_at, r.content_type, True
    page = parse_ruling_page(content)  # validate before any IPO request (layout change / wrong page)
    ipo = lookup_ipo(store, client, page, section, force=force)
    return (ingest_ruling(store, section, slug, content, url, fetched_at, listing, ctype, ipo),
            now or ipo.fetched_now)


def _fixture_dir(fixtures: Path) -> Path:
    return fixtures if fixtures.name == SOURCE_ID else fixtures / SOURCE_ID


def _hit(lk: TkLink, total: int | None) -> RemoteHit:
    jtype = SECTIONS[lk.section]  # type: ignore[index]
    d = lk.date.isoformat() if lk.date else None
    title = ", ".join([COURT_NAME, DOC_TYPES_PL[jtype], d or "data niepewna",
                       ", ".join(lk.case_numbers) or "sygnatura w treści orzeczenia"])
    if lk.title:
        title += f" – {lk.title}"
    return RemoteHit(
        document_id=lk.document_id, kind="judgment", title=title, snippet=(lk.snippet or "")[:800],
        original_url=ruling_url(lk.section, lk.slug),  # type: ignore[arg-type]
        metadata={"court": COURT_NAME, "court_type": COURT_TYPE, "case_numbers": lk.case_numbers,
                  "judgment_date": d, "judgment_date_raw": lk.date_raw, "judgment_type": jtype,
                  "subject": lk.title or None, "tt_news_uid": lk.uid, "text_scope": TEXT_SCOPE, "text_scope_after_fetch": TEXT_SCOPE_FULL,
                  "total": total,
                  "source": SOURCE_ID})


# --------------------------------------------------------------------------- connector


class TkConnector(BaseConnector):
    """Constitutional Tribunal rulings (wyroki, postanowienia): trybunal.gov.pl (operative part) + IPO (full text)."""

    source_id = "tk"
    supports_search = True
    supports_fetch = True

    # ------------------------------------------------------------------ search
    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Live search (metadata + snippet only; `fetch` stores the text).

        A TK case number (filter `case_number`, or a query that is exactly one, e.g. "K 1/20") is resolved
        through the case page `/s/<sig>` (1–2 requests; exact, with dates). Anything else goes to the site's
        Solr search restricted to rulings (up to 3 pages); its hits carry a title and snippet, and a case
        number/date only when the snippet shows the ruling header. Solr indexes what the site publishes:
        the operative part, not the reasoning (`fetch` adds the reasoning from IPO).

        filters: case_number (non-TK numbers return []), judgment_type (SENTENCE|DECISION), date_from/date_to
        (ruling date, applied locally: hits without a known date are dropped when a window is set),
        court_type (anything but CONSTITUTIONAL_TRIBUNAL returns [])."""
        f = filters or {}
        if f.get("court_type") and str(f["court_type"]).upper() != COURT_TYPE:
            return []
        lo, hi = _iso(f.get("date_from")), _iso(f.get("date_to"))
        section = _JTYPE_SECTION.get(str(f.get("judgment_type") or "").upper())
        if f.get("case_number"):
            sig = normalize_signature(str(f["case_number"]))
            if not sig:
                return []  # not a TK case number
        else:
            sig = as_signature(query)
        if sig:
            cp = case_page(client, sig)
            if cp is None:
                return []
            return [_hit(lk, len(cp.rulings)) for lk in cp.rulings
                    if (not section or lk.section == section) and _in_window(lk.date, lo, hi)][:limit]
        if not (query or "").strip():
            return []
        sections = (section,) if section else tuple(SECTIONS)
        hits: list[RemoteHit] = []
        page = 1
        while len(hits) < limit and page <= MAX_SEARCH_PAGES:
            res = search_page(client, search_url(query, sections=sections, page=page))
            for lk in res.items:
                if len(hits) >= limit:
                    break
                if (not section or lk.section == section) and _in_window(lk.date, lo, hi):
                    hits.append(_hit(lk, res.total))
            if page >= res.pages or not res.items:
                break
            page += 1
        return hits

    # ------------------------------------------------------------------ fetch
    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        m = ID_RE.fullmatch(document_id[3:]) if document_id.startswith("tk:") else None
        if not m:
            return None
        j, _ = fetch_ruling(store, client, m.group(1), m.group(2), force=force)
        return j.document_id

    # ------------------------------------------------------------------ default sample
    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        """Small sample from catalog defaults: `queries = [{listing = "wyroki"} | {query} | {case_number}]`,
        `max_total`."""
        res = SourceSyncResult(source_id=self.source_id)
        d = self.info.defaults
        queries = d.get("queries") or []
        if not queries:
            res.warnings.append("no default scope in the catalog ([sources.tk.defaults]); nothing fetched")
            res.ok = True
            return res
        max_total = min(limit or d.get("max_total", 10), d.get("max_total", 10))
        fetched, reused, seen = 0, 0, set()
        for q in queries:
            try:
                links = self._links_for(client, q)
            except Exception as e:  # noqa: BLE001 - other queries may still work
                res.errors.append(f"scope {q}: {type(e).__name__}: {e}")
                continue
            for lk in links:
                if len(seen) >= max_total:
                    break
                if lk.document_id in seen:
                    continue
                seen.add(lk.document_id)
                try:
                    j, now = fetch_ruling(store, client, lk.section, lk.slug, force=force,  # type: ignore[arg-type]
                                          listing=lk.listing())
                except NotFoundUpstream:
                    res.warnings.append(f"{lk.document_id}: listed but not available upstream; not stored")
                    continue
                except Exception as e:  # noqa: BLE001 - one bad ruling must not stop the sample
                    res.errors.append(f"{lk.document_id}: {type(e).__name__}: {e}")
                    continue
                fetched, reused = fetched + now, reused + (not now)
                self._flag_warning(res, j)
        res.counts = {"fetched": fetched, "reused_checkpoint": reused}
        success = bool(fetched or reused)
        res.ok = success and not res.errors
        self.record(store, success=success, partial=bool(res.errors), offline=False)
        return res

    def _links_for(self, client: PoliteClient, q: dict) -> list[TkLink]:
        if q.get("case_number"):
            sig = normalize_signature(str(q["case_number"]))
            cp = case_page(client, sig) if sig else None
            return cp.rulings if cp else []
        if q.get("query"):
            return search_page(client, search_url(str(q["query"]))).items
        section = q.get("listing", "wyroki")
        if section not in SECTIONS:
            raise ValueError(f"listing must be one of {tuple(SECTIONS)}")
        return parse_listing(client.get(listing_url(section), accept=ACCEPT_HTML).content, section).items

    @staticmethod
    def _flag_warning(res: SourceSyncResult, j: Judgment) -> None:
        flags = [f for f in j.data_quality_flags if f not in QUIET_FLAGS]
        if flags:
            res.warnings.append(f"{j.document_id}: {', '.join(flags)}")

    # ------------------------------------------------------------------ offline
    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        """Build from recorded ruling pages `tk/ruling_*.html` (id taken from the page's canonical link);
        `tk/listing_*.html` and `tk/case_*.html` add listing metadata (date/case-number cross-check);
        `tk/ipo_*.html` (recorded IPO case pages) add the full text with reasoning to the matching rulings."""
        res = SourceSyncResult(source_id=self.source_id)
        base = _fixture_dir(fixtures)
        listings: dict[str, dict] = {}
        for p in sorted(base.glob("listing_*.html")) + sorted(base.glob("case_*.html")):
            if p.stem.endswith("_404"):
                continue  # recorded "unknown case" answer, not a listing
            try:
                if p.name.startswith("listing_"):
                    section = "postanowienia" if "postanowienia" in p.name else "wyroki"
                    links = parse_listing(p.read_bytes(), section).items
                else:
                    links = parse_case_page(p.read_bytes()).rulings
                for lk in links:
                    if lk.document_id:
                        listings.setdefault(lk.document_id, lk.listing())
            except Exception as e:  # noqa: BLE001 - listing metadata is optional
                res.warnings.append(f"{p.name}: {type(e).__name__}: {e}")
        ipo_pages: dict[str, tuple[bytes, datetime]] = {}
        for p in sorted(base.glob("ipo_*.html")):  # recorded IPO case pages, keyed by the case number they show
            try:
                raw = p.read_bytes()
                ipo_pages[normalize_signature(parse_ipo_case(raw).signature) or ""] = (raw, mtime(p))
            except Exception as e:  # noqa: BLE001 - IPO pages are optional
                res.warnings.append(f"{p.name}: {type(e).__name__}: {e}")
        n = 0
        for p in sorted(base.glob("ruling_*.html")):
            try:
                raw = p.read_bytes()
                m = re.search(rb'<link rel="canonical" href="([^"]+)"', raw)
                sid = path_to_id(m.group(1).decode()) if m else None
                if not sid:
                    raise ValueError("no canonical ruling URL in the page")
                ipo = lookup_ipo(None, None, parse_ruling_page(raw), sid[0], pages=ipo_pages) if ipo_pages else None
                j = ingest_ruling(store, sid[0], sid[1], raw, ruling_url(*sid), mtime(p), listings.get(doc_id(*sid)),
                                  ipo=ipo)
                n += 1
                self._flag_warning(res, j)
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
        res.counts = {"judgments": n}
        res.ok = n > 0 and not res.errors
        self.record(store, success=n > 0, partial=bool(res.errors), offline=True)
        return res

    # ------------------------------------------------------------------ bulk
    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Paged import with checkpoint/resume. Three scopes, chosen by `params`:

        - listing (default): the site's ruling lists, newest first. params: section (wyroki|postanowienia;
          default both, wyroki first), since/until (ruling date as listed, ISO; paging of a section stops at
          the first page ending before `since`). Cursor = "<section>|<next pager URL>"; new rulings shift
          items to later pages, so a resumed run re-sees (and skips) some items but does not miss any;
        - query: Solr phrase search restricted to rulings, relevance order (cursor = page number). The order
          is not stable across index updates: a resumed run can miss items – use --no-resume to redo;
          since/until are not applied (hits have no reliable date);
        - case_number: the rulings of one case (one page).
        A page is committed only when all its items were handled."""
        res = SourceSyncResult(source_id=self.source_id)
        mode = "case" if params.get("case_number") else "query" if params.get("query") else "listing"
        scope = scope_key(self.source_id, {"mode": mode, **params})
        state = store.get_sync_state(self.source_id, scope) if limits.resume else None
        if state and state["done"]:
            res.ok = True
            res.warnings.append(f"scope {scope} already complete ({state['items']} items); use --no-resume to redo")
            return res
        lo, hi = (_iso(params.get("since")), _iso(params.get("until"))) if mode != "query" else (None, None)
        if mode == "query" and (params.get("since") or params.get("until")):
            res.warnings.append("since/until are ignored for a query scope (search hits carry no reliable date)")
        sections = [params["section"]] if params.get("section") else list(SECTIONS)
        if any(s not in SECTIONS for s in sections):
            res.errors.append(f"section must be one of {tuple(SECTIONS)}")
            return res
        sig = normalize_signature(str(params["case_number"])) if mode == "case" else None
        if mode == "case" and not sig:
            res.errors.append(f"not a TK case number: {params['case_number']!r}")
            return res

        def load(cursor: str) -> tuple[list[TkLink], str | None, str]:
            """One page of the scope -> (items, next cursor or None when the scope ends, progress label)."""
            if mode == "case":
                cp = case_page(client, sig)  # type: ignore[arg-type]
                if cp is None:
                    raise NotFoundUpstream(case_url(case_slugs(sig)[0]), "sprawa nieznana na trybunal.gov.pl", 404)  # type: ignore[arg-type]
                return cp.rulings, None, f"case {sig}"
            if mode == "query":
                page = int(cursor or 1)
                sp = search_page(client, search_url(str(params["query"]), page=page))
                nxt = str(page + 1) if sp.items and page < sp.pages else None
                return sp.items, nxt, f"search page {page}/{sp.pages}"
            section, _, url = (cursor or f"{sections[0]}|").partition("|")
            lp = parse_listing(client.get(url or listing_url(section), accept=ACCEPT_HTML).content, section)
            ended = not lp.items or lp.next_url is None or bool(
                lo and lp.items[-1].date and lp.items[-1].date < lo)
            if not ended:
                nxt: str | None = f"{section}|{lp.next_url}"
            else:
                i = sections.index(section)
                nxt = f"{sections[i + 1]}|" if i + 1 < len(sections) else None
            return lp.items, nxt, f"{section} page {lp.current_page}/{lp.last_page}"

        cursor = (state["cursor"] if state and state["cursor"] else "") or ""
        items_total = state["items"] if state else 0
        stored_now = pages_done = 0
        while True:
            if limits.limit is not None and stored_now >= limits.limit:
                break
            if limits.max_bytes is not None and store.data_size_bytes() >= limits.max_bytes:
                res.warnings.append("stopped: data directory reached --max-gb")
                break
            try:
                items, nxt, label = load(cursor)
            except Exception as e:  # noqa: BLE001 - keep checkpoint, report
                res.errors.append(f"{cursor or 'first page'}: {type(e).__name__}: {e}")
                break
            page_complete = True
            with store.batch():
                for lk in items:
                    if limits.limit is not None and stored_now >= limits.limit:
                        page_complete = False  # resume must revisit the rest of this page
                        break
                    if not _in_window(lk.date, lo, hi):
                        continue
                    if limits.resume and store.get_judgment(lk.document_id) is not None:  # type: ignore[arg-type]
                        continue
                    try:
                        fetch_ruling(store, client, lk.section, lk.slug, force=not limits.resume,  # type: ignore[arg-type]
                                     listing=lk.listing())
                        stored_now += 1
                    except NotFoundUpstream:
                        res.warnings.append(f"{lk.document_id}: listed but 404 upstream; not stored")
                    except Exception as e:  # noqa: BLE001 - one bad record must not stop the batch
                        res.errors.append(f"{lk.document_id}: {type(e).__name__}: {e}")
                last = page_complete and nxt is None
                if page_complete:
                    items_total += len(items)
                    pages_done += 1
                    cursor = nxt or cursor
                store.set_sync_state(self.source_id, scope, cursor=cursor, done=last, items=items_total,
                                     bytes_=store.data_size_bytes())
            if progress:
                progress(f"tk: {label} {'done' if page_complete else 'interrupted by --limit'}, "
                         f"stored {stored_now} in this run")
            if not page_complete or last:
                break
        res.counts = {"stored": stored_now, "pages_done": pages_done}
        res.ok = not res.errors
        self.record(store, success=stored_now > 0 or res.ok, partial=bool(res.errors), offline=False)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get(self.source_id, {}).get("judgments", 0)
        return f"{n} TK rulings stored locally (sample; full text with reasoning from IPO where available, else operative part; trybunal.gov.pl)", []
