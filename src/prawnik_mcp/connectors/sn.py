"""SN (Sąd Najwyższy) connector: Supreme Court rulings straight from the sn.pl ruling database.

SAOS mirrors SN rulings only up to 2016; sn.pl has current ones (and older ones back to at least 1999).
No documented public API: sn.pl is Joomla, and its search page (`/pl/wyszukiwarka-orzeczen`) calls a
com_ajax plugin at `GET /pl/index.php?option=com_ajax&plugin=snproxy&format=json&task=...`. That path is
not disallowed by robots.txt (which blocks /api/, /components/, /modules/, /plugins/ and other Joomla
system paths; none of those is used). Tasks used, all verified 2026-09-28:
- `searchOrzeczenia` (tresc/q, sygnatura, data_wydania_od/do, izba, forma_orzeczenia, strona,
  rozmiar_strony ∈ {10, 25, 50, 100}): metadata only, newest first, no total count, no snippet;
  `sygnatura` is a case-insensitive substring match, so case-number hits are filtered exactly here;
- `detailsOrzeczenie&id=`: metadata (chamber, bench, rapporteur, modification date);
- `OrzeczeniePlikHtml&id=`: base64 HTML rendering of the ruling PDF (the stored text).
An unknown id is answered with HTTP 200 and a 404 problem object; it is raised as `NotFoundUpstream`.
Each ruling costs 2 requests; both raw JSON answers are kept as snapshots. The site sits behind an
Imperva (Incapsula) WAF: a challenge or an HTML page instead of JSON surfaces as `SourceUnavailable`
and is never bypassed. Polite rate: catalog `rate_per_s` (0.5 req/s).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from prawnik_mcp.connectors.base import BaseConnector, BulkLimits, RemoteHit, SourceSyncResult, mtime, scope_key
from prawnik_mcp.connectors.http import FetchResult, NotFoundUpstream, PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.sn import (
    COURT_NAME,
    COURT_TYPE,
    ID_RE,
    MIN_PLAUSIBLE,
    PAGE_SIZES,
    PARSER_VERSION,
    SnNotFound,
    SnSearchItem,
    SnSearchPage,
    SnUpstreamError,
    ajax_url,
    decode_html_payload,
    details_url,
    doc_type,
    html_url,
    normalize_signature,
    page_url,
    parse_details,
    parse_search,
    parse_sn_ruling,
    same_case,
)
from prawnik_mcp.store import Store

SOURCE_ID = "sn"
ACCEPT = "application/json, text/javascript;q=0.9, */*;q=0.5"
CONTENT_TYPE = "application/json; charset=utf-8"
MAX_SEARCH_PAGES = 3  # live search budget: at most 3 GETs
CASE_PAGE_SIZE = 25  # case-number lookups: substring matches of other divisions ("II"/"III") share the page
BULK_PAGE_SIZE = 100  # largest page the form offers
MAX_WINDOW_PAGES = 100  # 10 000 hits per bulk window; beyond that the window must be narrowed
MAX_CONSECUTIVE_ERRORS = 5  # bulk stops (window not committed) instead of hammering a failing upstream
COMMIT_EVERY = 10  # bulk: rulings per transaction (2 requests each, so one transaction lasts ~40 s)


def _iso(d: Any) -> date | None:
    if d is None or d == "":
        return None
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def search_url(*, query: str | None = None, case_number: str | None = None, date_from: Any = None,
               date_to: Any = None, chamber: str | None = None, form: str | None = None, judge: str | None = None,
               page: int = 1, page_size: int = 10) -> str:
    """`task=searchOrzeczenia` URL with the parameters the search page sends (verified 2026-09-28).

    The phrase goes both as `q` (read by the proxy) and `tresc` (the backend name), as the page does;
    `izba` / `forma_orzeczenia` take the exact option texts of the form (e.g. "Izba Cywilna", "uchwała SN")."""
    if page_size not in PAGE_SIZES:
        raise ValueError(f"page_size must be one of {PAGE_SIZES}")
    lo, hi = _iso(date_from), _iso(date_to)
    return ajax_url("searchOrzeczenia", q=query, tresc=query, sygnatura=case_number, forma_orzeczenia=form,
                    data_wydania_od=lo.isoformat() if lo else None, data_wydania_do=hi.isoformat() if hi else None,
                    izba=chamber, sedzia_w_skladzie=judge, strona=page, rozmiar_strony=page_size)


def _get(client: PoliteClient, url: str, validate: Callable[[bytes], Any], not_found: str) -> tuple[FetchResult, Any]:
    """GET + parse; maps the proxy's problem objects and layout changes to upstream errors."""
    r = client.get(url, accept=ACCEPT)
    try:
        return r, validate(r.content)
    except SnNotFound:
        raise NotFoundUpstream(url, not_found, 404) from None
    except SnUpstreamError as e:
        raise SourceUnavailable(url, f"serwer wyszukiwarki SN zwrócił błąd (status {e.status}: {e.title})",
                                e.status) from None
    except ValueError as e:
        raise SourceUnavailable(url, f"nieoczekiwana odpowiedź wyszukiwarki SN: {e}", r.status) from None


def search_page(client: PoliteClient, url: str) -> SnSearchPage:
    return _get(client, url, parse_search, "wyszukiwarka SN zwróciła 404")[1]


def ingest_ruling(store: Store, internal_id: str, details: bytes, details_url_: str, details_at: datetime | None,
                  html: bytes, html_url_: str, html_at: datetime | None,
                  details_type: str = CONTENT_TYPE, html_type: str = CONTENT_TYPE) -> Judgment:
    parse_details(details)  # validate both answers before anything is written
    decode_html_payload(html)
    dsnap = store.put_snapshot(SOURCE_ID, details_url_, details, details_type,
                               parser_version=PARSER_VERSION, fetched_at=details_at)
    hsnap = store.put_snapshot(SOURCE_ID, html_url_, html, html_type,
                               parser_version=PARSER_VERSION, fetched_at=html_at)
    judgment, doc = parse_sn_ruling(details, html, internal_id, snapshot_id=hsnap.snapshot_id, sha256=hsnap.sha256,
                                    fetched_at=hsnap.fetched_at, details_snapshot_id=dsnap.snapshot_id)
    store.upsert_document(doc)
    store.upsert_judgment(judgment, title=doc.title)
    return judgment


def fetch_ruling(store: Store, client: PoliteClient, internal_id: str, *, force: bool = False) -> tuple[Judgment, bool]:
    """Returns (judgment, fetched_now). Stored snapshots of both answers are re-parsed, not re-fetched.

    Nothing is stored unless both answers were fetched and parsed: an unknown id gives `NotFoundUpstream`
    (404 problem object on details), and so does a ruling without an HTML text file."""
    durl, hurl = details_url(internal_id), html_url(internal_id)
    if not force:
        ds, hs = store.find_snapshot_by_url(durl), store.find_snapshot_by_url(hurl)
        dbytes = store.read_snapshot_bytes(ds.snapshot_id) if ds else None
        hbytes = store.read_snapshot_bytes(hs.snapshot_id) if hs else None
        if ds and hs and dbytes is not None and hbytes is not None:
            j = ingest_ruling(store, internal_id, dbytes, durl, ds.fetched_at, hbytes, hurl, hs.fetched_at,
                              ds.content_type, hs.content_type)
            return j, False
    # details first: an unknown id stops here, before the (larger) text request
    d, _ = _get(client, durl, parse_details, "orzeczenie o tym identyfikatorze nie istnieje w bazie SN (404)")
    h, _ = _get(client, hurl, decode_html_payload,
                "baza SN nie ma treści HTML tego orzeczenia (404); PDF nie jest przetwarzany; nie zapisano")
    j = ingest_ruling(store, internal_id, d.content, durl, d.fetched_at, h.content, hurl, h.fetched_at,
                      d.content_type, h.content_type)
    return j, True


def list_window(client: PoliteClient, start: date, end: date, params: dict, case_number: str | None,
                ) -> tuple[list[SnSearchItem], bool]:
    """All hits of one issue-date window, oldest first, de-duplicated. Returns (items, truncated)."""
    items: list[SnSearchItem] = []
    seen: set[str] = set()
    for page in range(1, MAX_WINDOW_PAGES + 1):
        res = search_page(client, search_url(
            query=params.get("query"), case_number=case_number, date_from=start, date_to=end,
            chamber=params.get("chamber"), form=params.get("form"), page=page, page_size=BULK_PAGE_SIZE))
        for it in res.items:
            if it.internal_id in seen or (case_number and not same_case(it.case_number, case_number)):
                continue
            seen.add(it.internal_id)
            items.append(it)
        if res.count < BULK_PAGE_SIZE:
            return items[::-1], False
    return items[::-1], True


def _windows(start: date, until: date, days: int) -> Iterator[tuple[date, date]]:
    while start <= until:
        end = min(start + timedelta(days=days - 1), until)
        yield start, end
        start = end + timedelta(days=1)


def _fixture_dir(fixtures: Path) -> Path:
    return fixtures if fixtures.name == SOURCE_ID else fixtures / SOURCE_ID


# --------------------------------------------------------------------------- connector


class SnConnector(BaseConnector):
    """Supreme Court rulings (wyroki, postanowienia, uchwały, zarządzenia) from the sn.pl ruling database."""

    source_id = "sn"
    supports_search = True
    supports_fetch = True
    live_phrase_search = False  # metadata only, newest first: see BaseConnector

    # ------------------------------------------------------------------ search
    def search(self, client: PoliteClient, query: str, *, limit: int = 5,
               filters: dict | None = None) -> list[RemoteHit]:
        """Live search (metadata only, newest first, no snippet; `fetch` stores the full text).

        filters: case_number (-> sygnatura; exact match applied locally; strings that are not SN case
        numbers return [] without a request), date_from/date_to (issue date), chamber (-> izba, exact form
        text such as "Izba Cywilna"), judgment_form (-> forma_orzeczenia, e.g. "uchwała SN"), judge (-> sedzia
        w składzie), judgment_type (SENTENCE|DECISION|RESOLUTION|REGULATION, applied locally), court_type
        (anything but SUPREME returns [])."""
        f = filters or {}
        if f.get("court_type") and str(f["court_type"]).upper() != COURT_TYPE:
            return []
        sig = None
        if f.get("case_number"):
            sig = normalize_signature(str(f["case_number"]))
            if not sig:
                return []  # not an SN case number
        want_type = str(f["judgment_type"]).upper() if f.get("judgment_type") else None
        size = CASE_PAGE_SIZE if sig else next((s for s in PAGE_SIZES if s >= limit), PAGE_SIZES[-1])
        hits: list[RemoteHit] = []
        seen: set[str] = set()
        for page in range(1, MAX_SEARCH_PAGES + 1):
            res = search_page(client, search_url(
                query=None if sig else (query or None), case_number=sig, date_from=f.get("date_from"),
                date_to=f.get("date_to"), chamber=f.get("chamber"), form=f.get("judgment_form"),
                judge=f.get("judge"), page=page, page_size=size))
            for it in res.items:
                if len(hits) >= limit:
                    break
                jtype = doc_type(it.form)
                if (it.internal_id in seen or (sig and not same_case(it.case_number, sig))
                        or (want_type and jtype != want_type)):
                    continue
                seen.add(it.internal_id)
                d = it.judgment_date.isoformat() if it.judgment_date else "data niepewna"
                hits.append(RemoteHit(
                    document_id=f"sn:{it.internal_id}", kind="judgment",
                    title=f"{COURT_NAME}, {it.form or 'orzeczenie'}, {d}, {it.case_number or '(brak sygnatury)'}",
                    snippet="", original_url=page_url(it.internal_id),
                    metadata={"court": COURT_NAME, "court_type": COURT_TYPE,
                              "case_numbers": [it.case_number] if it.case_number else [],
                              "judgment_date": it.judgment_date.isoformat() if it.judgment_date else None,
                              "judgment_date_raw": it.judgment_date_raw, "judgment_type": jtype,
                              "document_type_pl": it.form, "sn_id": it.internal_id,
                              "match": "case_number" if sig else "full_text", "order": "date_desc",
                              "source": SOURCE_ID}))
            if len(hits) >= limit or res.count < size:
                break
        return hits

    # ------------------------------------------------------------------ fetch
    def fetch(self, store: Store, client: PoliteClient, document_id: str, *, force: bool = False) -> str | None:
        if not document_id.startswith("sn:") or not ID_RE.fullmatch(document_id[3:]):
            return None
        j, _ = fetch_ruling(store, client, document_id[3:], force=force)
        return j.document_id

    # ------------------------------------------------------------------ default sample
    def sync_defaults(self, store: Store, client: PoliteClient, *, force: bool = False,
                      limit: int | None = None) -> SourceSyncResult:
        """Small sample from catalog defaults: `queries = [{query, since, until, chamber, form}]`, `max_total`."""
        res = SourceSyncResult(source_id=self.source_id)
        d = self.info.defaults
        queries = d.get("queries") or []
        if not queries:
            res.warnings.append("no default scope in the catalog ([sources.sn.defaults]); nothing fetched")
            res.ok = True
            return res
        max_total = min(limit or d.get("max_total", 10), d.get("max_total", 10))
        fetched, reused, seen = 0, 0, set()
        for q in queries:
            try:
                page = search_page(client, search_url(query=q.get("query"), date_from=q.get("since"),
                                                      date_to=q.get("until"), chamber=q.get("chamber"),
                                                      form=q.get("form"), page_size=10))
            except Exception as e:  # noqa: BLE001 - other queries may still work
                res.errors.append(f"search {q}: {type(e).__name__}: {e}")
                continue
            for it in page.items:
                if len(seen) >= max_total:
                    break
                if it.internal_id in seen:
                    continue
                seen.add(it.internal_id)
                try:
                    j, now = fetch_ruling(store, client, it.internal_id, force=force)
                except NotFoundUpstream:
                    res.warnings.append(f"sn:{it.internal_id}: listed in search but not available upstream; not stored")
                    continue
                except Exception as e:  # noqa: BLE001 - one bad ruling must not stop the sample
                    res.errors.append(f"sn:{it.internal_id}: {type(e).__name__}: {e}")
                    continue
                fetched, reused = fetched + now, reused + (not now)
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
        res.counts = {"fetched": fetched, "reused_checkpoint": reused}
        success = bool(fetched or reused)
        res.ok = success and not res.errors
        self.record(store, success=success, partial=bool(res.errors), offline=False)
        return res

    # ------------------------------------------------------------------ offline
    def sync_offline(self, store: Store, fixtures: Path) -> SourceSyncResult:
        """Build from recorded pairs `sn/details_<id>.json` + `sn/html_<id>.json` (raw com_ajax answers)."""
        res = SourceSyncResult(source_id=self.source_id)
        n = 0
        for p in sorted(_fixture_dir(fixtures).glob("details_*.json")):
            iid = p.stem[len("details_"):]
            if not ID_RE.fullmatch(iid):
                continue
            h = p.with_name(f"html_{iid}.json")
            if not h.exists():
                continue
            try:
                j = ingest_ruling(store, iid, p.read_bytes(), details_url(iid), mtime(p),
                                  h.read_bytes(), html_url(iid), mtime(h))
                n += 1
                if j.data_quality_flags:
                    res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
        res.counts = {"judgments": n}
        res.ok = n > 0 and not res.errors
        self.record(store, success=n > 0, partial=bool(res.errors), offline=True)
        return res

    # ------------------------------------------------------------------ bulk
    def sync_bulk(self, store: Store, client: PoliteClient, params: dict, limits: BulkLimits,
                  progress=None) -> SourceSyncResult:
        """Import by issue-date windows, oldest window first, with checkpoint/resume.

        params: since/until (issue date, ISO; `since` required unless case_number is given; `until`
        defaults to today), query (full text), case_number (exact, SN format), chamber (izba), form
        (forma_orzeczenia), window_days (default 7; 92 with a query; one window for a case number).
        The API has no total and sorts newest first, so each window is listed completely (≤100 hits per
        request) before its rulings are fetched. Cursor = start date of the next window; a window is
        committed only when all its items were handled. Resume re-lists the current window and skips
        rulings already stored. Rulings published late for an already committed window are not revisited.
        """
        res = SourceSyncResult(source_id=self.source_id)
        scope = scope_key(self.source_id, {"mode": "windows", **params})
        state = store.get_sync_state(self.source_id, scope) if limits.resume else None
        if state and state["done"]:
            res.ok = True
            res.warnings.append(f"scope {scope} already complete ({state['items']} items); use --no-resume to redo")
            return res
        sig = None
        if params.get("case_number"):
            sig = normalize_signature(str(params["case_number"]))
            if not sig:
                res.errors.append(f"not an SN case number: {params['case_number']!r}")
                return res
        since = _iso(params.get("since")) or (MIN_PLAUSIBLE if sig else None)
        if since is None:
            res.errors.append("since (issue date, ISO) is required for an SN bulk scope unless case_number is given")
            return res
        until = _iso(params.get("until")) or date.today()
        days = int(params.get("window_days") or ((until - since).days + 1 if sig else 92 if params.get("query") else 7))
        start = _iso(state["cursor"]) if state and state["cursor"] else since
        items_total = state["items"] if state else 0
        stored_now, windows_done, failed_in_row = 0, 0, 0
        for lo, hi in _windows(start, until, max(days, 1)):
            if limits.limit is not None and stored_now >= limits.limit:
                break
            if limits.max_bytes is not None and store.data_size_bytes() >= limits.max_bytes:
                res.warnings.append("stopped: data directory reached --max-gb")
                break
            try:
                items, truncated = list_window(client, lo, hi, params, sig)
            except Exception as e:  # noqa: BLE001 - keep checkpoint, report
                res.errors.append(f"window {lo}..{hi}: {type(e).__name__}: {e}")
                break
            if truncated:
                res.errors.append(f"window {lo}..{hi}: more than {MAX_WINDOW_PAGES * BULK_PAGE_SIZE} hits; "
                                  "use a smaller window_days")
                break
            complete = True
            for i in range(0, len(items), COMMIT_EVERY):
                with store.batch():
                    for it in items[i:i + COMMIT_EVERY]:
                        if limits.limit is not None and stored_now >= limits.limit:
                            complete = False  # resume must revisit the rest of this window
                            break
                        if limits.resume and store.get_judgment(f"sn:{it.internal_id}") is not None:
                            continue
                        try:
                            fetch_ruling(store, client, it.internal_id, force=not limits.resume)
                            stored_now += 1
                            failed_in_row = 0
                        except NotFoundUpstream:
                            res.warnings.append(f"sn:{it.internal_id}: listed in search but 404 upstream; not stored")
                        except Exception as e:  # noqa: BLE001 - one bad record must not stop the batch
                            res.errors.append(f"sn:{it.internal_id}: {type(e).__name__}: {e}")
                            failed_in_row += 1
                            if failed_in_row >= MAX_CONSECUTIVE_ERRORS:
                                complete = False
                                res.errors.append(f"stopped after {failed_in_row} consecutive failures; "
                                                  "window not committed")
                                break
                if not complete:
                    break
            if complete:
                items_total += len(items)
                windows_done += 1
                nxt = hi + timedelta(days=1)
                store.set_sync_state(self.source_id, scope, cursor=nxt.isoformat(), done=nxt > until,
                                     items=items_total, bytes_=store.data_size_bytes())
            if progress:
                progress(f"sn: window {lo}..{hi} {'done' if complete else 'interrupted'} ({len(items)} hits), "
                         f"stored {stored_now} in this run")
            if not complete:
                break
        res.counts = {"stored": stored_now, "windows_done": windows_done}
        res.ok = not res.errors
        self.record(store, success=stored_now > 0 or res.ok, partial=bool(res.errors), offline=False)
        return res

    def coverage(self, store: Store) -> tuple[str, list[str]]:
        n = store.stats_by_source().get(self.source_id, {}).get("judgments", 0)
        return f"{n} SN rulings stored locally (sample; sn.pl ruling database)", []
