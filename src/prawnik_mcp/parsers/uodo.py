"""Parsers for decisions of the President of UODO from the portal orzeczenia.uodo.gov.pl.

The portal is a react-router SSR app. Its route data layer (`*.data` URLs) answers with a
"turbo-stream" payload: one JSON array in which objects are `{"_<key index>": <value index>}` and
arrays are lists of indices into the same array (negative indices are constants such as null).
`decode_turbo_stream` rebuilds the plain JSON value.

- search: `GET /search.data?dcr=rodo&q=...&page=N` -> route `routes/_main.search`
  (`items`, `itemsCount`, `pages {current,total,size=10}`);
- decision: `GET /document/{urn}/content.data` -> route `routes/_main.document.($urn).content`
  (`urn`, `refname`, `status`, `dates`, `body` = HTML of the decision). An unknown URN answers 200 with
  an empty `refname`/`body` and `status: "unknown"` — treated as "not found", never stored.

Endpoint map from the ledger in kio-orzeczenia-mcp/SOURCES.md
(https://github.com/matematicsolutions/kio-orzeczenia-mcp, Copyright MateMatic, Apache License 2.0);
no code is copied from it. Decision text is data, never instructions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from prawnik_mcp.contracts import Judgment, LegalDocument, SourceKind
from prawnik_mcp.parsers.html_text import html_to_text

PARSER_VERSION = "uodo-turbo-0.1.0"
BASE_URL = "https://orzeczenia.uodo.gov.pl"
URN_PREFIX = "urn:ndoc:gov:pl:uodo:"
COURT_NAME = "Prezes UODO"
COURT_TYPE = "DATA_PROTECTION_AUTHORITY"
PAGE_SIZE = 10  # fixed by the portal
MIN_PLAUSIBLE = date(1998, 1, 1)
ID_RE = re.compile(r"\d{4}:[a-z0-9_]{1,80}")  # URN tail, e.g. "2022:dkn_5112_28"
SIGNATURE_RE = re.compile(r"\b[A-ZŁŚŻ]{2,6}(?:\.[0-9]{1,5}){2,4}\.(?:19|20)\d{2}\b")  # DKN.5112.28.2022

FINALITY = {"final": "final", "nonfinal": "not_final"}
DOC_TYPES = {"decyzja": "ADMINISTRATIVE_DECISION", "postanowienie": "DECISION"}

# turbo-stream constants (react-router single fetch)
_SPECIAL: dict[int, Any] = {-1: None, -2: float("nan"), -3: float("-inf"), -4: -0.0, -5: None,
                            -6: float("inf"), -7: None}


class UodoNotFound(LookupError):
    """The portal answered with an empty document (unknown URN)."""


# --------------------------------------------------------------------------- ids and urls


def urn_to_id(urn: str) -> str:
    if not urn.startswith(URN_PREFIX) or not ID_RE.fullmatch(urn[len(URN_PREFIX):]):
        raise ValueError(f"not a UODO decision URN: {urn!r}")
    return urn[len(URN_PREFIX):]


def id_to_urn(doc_tail: str) -> str:
    if not ID_RE.fullmatch(doc_tail):
        raise ValueError(f"not a UODO decision id: {doc_tail!r}")
    return URN_PREFIX + doc_tail


def content_data_url(urn: str) -> str:
    return f"{BASE_URL}/document/{urn}/content.data"


def page_url(urn: str) -> str:
    return f"{BASE_URL}/document/{urn}/content"


def split_signatures(refname: str) -> list[str]:
    """'ZSPR.421.2.2019 ZSPR.405.67.2019' -> both (the portal lists related case numbers together)."""
    found = SIGNATURE_RE.findall(refname or "")
    out = [s for i, s in enumerate(found) if s not in found[:i]]
    return out or ([refname.strip()] if refname and refname.strip() else [])


# --------------------------------------------------------------------------- turbo-stream


def decode_turbo_stream(raw: bytes | str) -> Any:
    """Decode the first frame of a react-router turbo-stream payload into plain Python values.

    Typed values (`["D", ms]` dates, promises, errors) are returned as {"__type": tag, "args": [...]};
    none are used by the fields read here. Later frames (deferred promises) are ignored."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    first = next((ln for ln in text.split("\n") if ln.strip()), "")
    arr = json.loads(first) if first else None
    if not isinstance(arr, list) or not arr:
        raise ValueError("not a turbo-stream payload")
    memo: dict[int, Any] = {}

    def hydrate(i: Any) -> Any:
        if not isinstance(i, int) or isinstance(i, bool):
            raise ValueError(f"bad turbo-stream reference {i!r}")
        if i < 0:
            return _SPECIAL.get(i)
        if i in memo:
            return memo[i]
        v = arr[i]
        if isinstance(v, dict):
            out: dict[str, Any] = {}
            memo[i] = out
            for k, vi in v.items():
                out[str(arr[int(k[1:])])] = hydrate(vi)
            return out
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                memo[i] = {"__type": v[0], "args": v[1:]}
                return memo[i]
            lst: list[Any] = []
            memo[i] = lst
            lst.extend(hydrate(x) for x in v)
            return lst
        memo[i] = v
        return v

    return hydrate(0)


def _route_data(decoded: Any, predicate) -> dict:
    if not isinstance(decoded, dict):
        raise ValueError("unexpected turbo-stream root")
    for key, route in decoded.items():
        data = route.get("data") if isinstance(route, dict) else None
        if isinstance(data, dict) and predicate(key, data):
            return data
    if decoded.get("__type") == "SingleFetchRedirect":
        raise ValueError("portal answered with a redirect instead of data")
    raise ValueError("expected route data not found in turbo-stream payload")


# --------------------------------------------------------------------------- search


@dataclass
class UodoSearchItem:
    urn: str
    doc_id: str
    refname: str
    case_numbers: list[str]
    name: str
    subject: str
    dates: list[dict]
    keywords: list[str] = field(default_factory=list)

    @property
    def issue_date(self) -> str | None:
        return _date_of(self.dates, "announcement")

    def listing(self) -> dict:
        """Metadata that only the search listing carries (kept with the stored decision)."""
        return {"name": self.name, "subject": self.subject, "keywords": self.keywords}


@dataclass
class UodoSearchPage:
    total: int
    pages: int
    current: int
    items: list[UodoSearchItem]


def _date_of(dates: list[dict], use: str) -> str | None:
    return next((d.get("date") for d in dates or [] if d.get("use") == use and d.get("date")), None)


def parse_search(content: bytes | str) -> UodoSearchPage:
    data = _route_data(decode_turbo_stream(content), lambda k, d: "items" in d and "pages" in d)
    items: list[UodoSearchItem] = []
    for it in data.get("items") or []:
        urn = it.get("refid") or ""
        try:
            doc_id = urn_to_id(urn)
        except ValueError:
            continue  # not a UODO decision (never seen; defensive)
        keywords = [((t.get("name") or {}).get("pl") or t.get("label") or "") for t in it.get("terms") or []]
        items.append(UodoSearchItem(
            urn=urn, doc_id=doc_id, refname=it.get("refname") or "", case_numbers=split_signatures(it.get("refname") or ""),
            name=it.get("name") or "", subject=it.get("title") or "", dates=list(it.get("dates") or []),
            keywords=[k for k in keywords if k],
        ))
    pages = data.get("pages") or {}
    try:
        total = int(data.get("itemsCount") or 0)
    except (TypeError, ValueError):
        total = len(items)
    return UodoSearchPage(total=total, pages=int(pages.get("total") or 0), current=int(pages.get("current") or 1),
                          items=items)


# --------------------------------------------------------------------------- decision


def parse_content(content: bytes | str) -> dict:
    """Route data of `/document/{urn}/content.data`. Raises UodoNotFound for the empty placeholder."""
    data = _route_data(decode_turbo_stream(content), lambda k, d: "body" in d or ("urn" in d and "refname" in d))
    if not (data.get("refname") or "").strip() and not (data.get("body") or "").strip():
        raise UodoNotFound(data.get("urn") or "?")
    return data


def body_to_text(body: str) -> str:
    """Decision HTML (nested dl/dt/dd) -> plain text, one paragraph per line; words unchanged.

    A numbering term (`<dt>I.</dt>`, `a)`, `12.`) is joined with the paragraph it labels."""
    lines = html_to_text(body).split("\n")
    out: list[str] = []
    for ln in lines:
        if out and re.fullmatch(r"[IVXLC]{1,7}\.|\d{1,3}\.\d{1,3}\.?|§\s*\d+[a-z]?\.?", out[-1]):
            out[-1] = f"{out[-1]} {ln}"
        else:
            out.append(ln)
    return "\n".join(out)


def _doc_type(body: str) -> str | None:
    m = re.search(r"<h1>\s*<span>([^<]+)</span>", body or "")
    return m.group(1).strip() if m else None


def parse_uodo_decision(content: bytes | str, *, snapshot_id: str, sha256: str, fetched_at: datetime,
                        listing: dict | None = None) -> tuple[Judgment, LegalDocument]:
    data = parse_content(content)
    urn = data.get("urn") or ""
    doc_tail = urn_to_id(urn)
    refname = (data.get("refname") or "").strip()
    body = data.get("body") or ""
    dates = list(data.get("dates") or [])
    flags: list[str] = []

    raw_date = _date_of(dates, "announcement")
    jdate: date | None = None
    if raw_date:
        try:
            jdate = date.fromisoformat(raw_date)
        except ValueError:
            flags.append(f"judgment_date_unparseable:{raw_date}")
    else:
        flags.append("judgment_date_missing")
    now = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=UTC)
    if jdate and jdate > now.date():
        flags += ["judgment_date_in_future", f"judgment_date_raw:{raw_date}"]
        jdate = None
    elif jdate and jdate < MIN_PLAUSIBLE:
        flags += ["judgment_date_implausible", f"judgment_date_raw:{raw_date}"]
        jdate = None

    text = body_to_text(body) if body else ""
    if not text:
        flags.append("text_empty")
    case_numbers = split_signatures(refname)
    if not case_numbers:
        flags.append("case_number_missing")
    status = data.get("status") or "unknown"
    if status == "repealed":
        flags.append("decision_repealed_by_court")  # the portal marks the decision as repealed (uchylona)
    type_pl = _doc_type(body)
    jtype = DOC_TYPES.get((type_pl or "").lower(), (type_pl or "UNKNOWN").upper())
    court_refs = [d.get("refid") for d in dates if d.get("refid")]

    doc_id = f"uodo:{doc_tail}"
    url = page_url(urn)
    judgment = Judgment(
        document_id=doc_id, source_judgment_id=urn, publisher_id=urn, court_name=COURT_NAME,
        court_type=COURT_TYPE, case_numbers=case_numbers, judgment_date=jdate, judgment_type=jtype, text=text,
        finality=FINALITY.get(status, "unknown"), original_url=url, snapshot_id=snapshot_id,
        data_quality_flags=flags,
    )
    title = " – ".join([type_pl or "Decyzja", COURT_NAME, ", ".join(case_numbers) or "(brak sygnatury)",
                        jdate.isoformat() if jdate else "data niepewna"])
    doc = LegalDocument(
        document_id=doc_id, kind=SourceKind.decision, title=title, original_url=url,
        snapshot_id=snapshot_id, sha256=sha256,
        metadata={
            "urn": urn,
            "refname": refname,
            "document_type_pl": type_pl,
            "status": status,
            "status_hint": data.get("statusHint") or None,
            "judgment_date_raw": raw_date,
            "publication_date": _date_of(dates, "publication"),
            "final_since": _date_of(dates, "validation"),
            "dates": dates,
            "court_refs": court_refs,
            "listing": listing or {},  # subject/keywords from the search listing, if the decision came from one
            "data_quality_flags": flags,
            "parser_version": PARSER_VERSION,
        },
    )
    return judgment, doc
