"""CJEU case law (Court of Justice, General Court) from Cellar: SPARQL metadata + REST content negotiation.

Verified against the live endpoints on 2026-09-28:
- `GET /resource/celex/<CELEX>` with `Accept: application/xhtml+xml` + `Accept-Language: pol` answers 303 to the
  manifestation, then 200 (XHTML, ~40-150 KB of text). Older cases and many recent orders exist only as legacy
  `text/html` (still Polish); some documents have neither in Polish. Missing formats answer 404
  ("does not hold a content datastream of the requested type"), which is never treated as "no such case".
- The SPARQL endpoint is Virtuoso: `bif:contains` uses its full-text index over expression titles (party names,
  case subject, keywords), ~1-2 s; a plain `FILTER(CONTAINS(...))` over the same titles takes 40-60 s and is not used.
  Cellar has no full-text index over the judgments' texts, so there is no search inside the reasoning.
- Requests per stored document: 1 (core metadata) + 1 (relations) + 2 (content: 303 + 200), plus one 404 per
  format/language tried before the one that exists. Polite rate: catalog `rate_per_s`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode

from prawnik_mcp.connectors.base import RemoteHit
from prawnik_mcp.connectors.cellar import EURLEX_HUMAN, SOURCE_ID, SPARQL, celex_url
from prawnik_mcp.connectors.http import NotFoundUpstream, PoliteClient, SourceUnavailable
from prawnik_mcp.contracts import Judgment
from prawnik_mcp.parsers.cellar_case import (
    CASE_CELEX_RE,
    DOC_TYPES,
    ECLI_RE,
    PARSER_VERSION,
    TYPE_PL,
    CaseMeta,
    build_case,
    canonical_case,
    case_from_celex,
    case_numbers_in,
    case_text,
    celex_for_case,
    is_case_celex,
    normalize_ecli,
    parse_core_rows,
    parse_relation_rows,
)
from prawnik_mcp.relevance import STOPWORDS
from prawnik_mcp.store import Store

SPARQL_JSON = "application/sparql-results+json"
PREFIXES = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n")
LANG_URI = "<http://publications.europa.eu/resource/authority/language/{}>"
TYPES_RE = r"^6[0-9]{4}(CJ|CO|CC|TJ|TO|FJ|FO)[0-9]{4}$"
# (language, format): Polish first; other languages only as a flagged fallback.
FORMATS = {"xhtml": "application/xhtml+xml", "html": "text/html"}
VARIANTS = [("pol", "xhtml"), ("pol", "html"), ("eng", "xhtml"), ("eng", "html"), ("fra", "xhtml"), ("fra", "html")]
MAX_SEARCH_ROWS = 60


@dataclass
class CaseIngest:
    document_id: str
    celex: str
    text_chars: int
    snapshot_id: str
    fetched_now: bool
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- SPARQL


def _core_select(where: str, lang: str = "POL") -> str:
    return PREFIXES + f"""SELECT DISTINCT ?celex ?date ?ecli ?title ?parties ?case ?national ?indicator WHERE {{
 {where}
 OPTIONAL {{ ?w cdm:work_date_document ?date }}
 OPTIONAL {{ ?w cdm:case-law_ecli ?ecli }}
 OPTIONAL {{ ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language {LANG_URI.format(lang)} ;
   cdm:expression_title ?title .
   OPTIONAL {{ ?e cdm:expression_case-law_identifier_case ?case }}
   OPTIONAL {{ ?e cdm:expression_case-law_parties ?parties }}
   OPTIONAL {{ ?e cdm:expression_case-law_source_ruling_preliminary ?national }}
   OPTIONAL {{ ?e cdm:expression_case-law_indicator_decision ?indicator }} }}
}} LIMIT 200"""


def core_query(celexes: list[str], lang: str = "POL") -> str:
    values = " ".join(f'"{c}"^^xsd:string' for c in celexes if is_case_celex(c))
    return _core_select(f"VALUES ?celex {{ {values} }} ?w cdm:resource_legal_id_celex ?celex .", lang)


def ecli_query(ecli: str) -> str:
    if not ECLI_RE.fullmatch(ecli):
        raise ValueError(f"not an ECLI: {ecli!r}")
    return _core_select(f'?w cdm:case-law_ecli "{ecli}"^^xsd:string ; cdm:resource_legal_id_celex ?celex . '
                        f'FILTER(REGEX(STR(?celex), "{TYPES_RE}"))')


def relations_query(celex: str) -> str:
    if not is_case_celex(celex):
        raise ValueError(f"not a CJEU CELEX: {celex!r}")
    return PREFIXES + f"""SELECT DISTINCT ?rel ?val WHERE {{
 ?w cdm:resource_legal_id_celex "{celex}"^^xsd:string .
 {{ ?w cdm:work_cites_work ?x . ?x cdm:resource_legal_id_celex ?val . BIND("cites" AS ?rel) }}
 UNION {{ ?w cdm:case-law_interpretes_resource_legal ?x . ?x cdm:resource_legal_id_celex ?val . BIND("interprets" AS ?rel) }}
 UNION {{ ?w cdm:case-law_is-about_case-law-subject-matter ?s . BIND(REPLACE(STR(?s), "^.*/", "") AS ?val) BIND("subject" AS ?rel) }}
 FILTER(!CONTAINS(STR(?val), "_"))
}} LIMIT 400"""


def fulltext_expression(query: str) -> str | None:
    """Virtuoso `bif:contains` expression: every significant word must occur in the title / keywords.
    Longer words become prefix matches (crude Polish inflection handling)."""
    terms: list[str] = []
    for w in re.findall(r"\w+", query.lower()):
        if w in STOPWORDS or (len(w) < 3 and not w.isdigit()):
            continue
        if w.isdigit() or len(w) <= 4:
            terms.append(f"'{w}'")
        else:
            terms.append(f"'{w[: max(4, len(w) - (4 if len(w) >= 7 else 2))]}*'")
        if len(terms) >= 6:
            break
    return " AND ".join(terms) if terms else None


def _title_select(where: str) -> str:
    return PREFIXES + f"""SELECT DISTINCT ?celex ?date ?ecli ?title ?parties ?case ?indicator WHERE {{
 {where}
 FILTER(REGEX(STR(?celex), "{TYPES_RE}"))
 OPTIONAL {{ ?w cdm:work_date_document ?date }}
 OPTIONAL {{ ?w cdm:case-law_ecli ?ecli }}
 OPTIONAL {{ ?e cdm:expression_case-law_identifier_case ?case }}
 OPTIONAL {{ ?e cdm:expression_case-law_parties ?parties }}
 OPTIONAL {{ ?e cdm:expression_case-law_indicator_decision ?indicator }}
}} ORDER BY DESC(?date) LIMIT {MAX_SEARCH_ROWS}"""


def title_query(expr: str) -> str:
    esc = expr.replace('"', "")
    return _title_select(f"""?e cdm:expression_title ?title . ?title bif:contains "{esc}" .
 ?e cdm:expression_uses_language {LANG_URI.format('POL')} ; cdm:expression_belongs_to_work ?w .
 ?w cdm:resource_legal_id_celex ?celex .""")


def joined_case_query(canon: str) -> str:
    """Case number that is not the first of a joined-case group ('C-179/17' of C-70/17 and C-179/17)."""
    base = canon.split(" ")[0]
    if not canonical_case(base):
        raise ValueError(f"not a CJEU case number: {canon!r}")
    return _title_select(f"""?e cdm:expression_case-law_identifier_case ?case . ?case bif:contains "'{base}'" .
 ?e cdm:expression_uses_language {LANG_URI.format('POL')} ; cdm:expression_belongs_to_work ?w ; cdm:expression_title ?title .
 ?w cdm:resource_legal_id_celex ?celex .""")


def _sparql_url(query: str) -> str:
    return f"{SPARQL}?{urlencode({'query': query})}"


def _bindings(content: bytes, url: str) -> list[dict]:
    try:
        return json.loads(content)["results"]["bindings"]
    except (ValueError, KeyError, TypeError):
        raise SourceUnavailable(url, "odpowiedź SPARQL Cellar w nieoczekiwanym formacie") from None


def _run(client: PoliteClient, query: str) -> list[dict]:
    url = _sparql_url(query)
    return _bindings(client.get(url, accept=SPARQL_JSON).content, url)


def _search_metas(rows: list[dict]) -> list[CaseMeta]:
    metas = parse_core_rows(rows)
    dates = {m.celex: m.date for m in metas.values()}
    return sorted(metas.values(), key=lambda m: dates[m.celex].isoformat() if dates[m.celex] else "", reverse=True)


# --------------------------------------------------------------------------- live search


def _hit(m: CaseMeta, match: str) -> RemoteHit:
    court, court_type, jtype = DOC_TYPES[m.doc_type]
    parties = m.parties or (m.parts[1] if len(m.parts) > 1 else "")
    cn = ", ".join(m.case_numbers or [case_from_celex(m.celex)])
    d = m.date.isoformat() if m.date else "data nieustalona"
    return RemoteHit(
        document_id=f"celex:{m.celex}", kind="eu_judgment",
        title=f"{court}, {TYPE_PL[m.doc_type]}, {d}, {cn or 'sygn. w treści'}: {parties}".rstrip(": "),
        snippet=(m.keywords or "")[:600], original_url=EURLEX_HUMAN.format(celex=m.celex),
        metadata={"court": court, "court_type": court_type, "case_numbers": m.case_numbers,
                  "judgment_date": m.date.isoformat() if m.date else None, "judgment_type": jtype,
                  "celex": m.celex, "ecli": m.ecli, "document_type_pl": TYPE_PL[m.doc_type], "match": match,
                  "source": SOURCE_ID})


def query_identifiers(query: str, filters: dict | None = None) -> tuple[list[str], list[str], list[str]]:
    """(case numbers, CELEX numbers, ECLIs) named by the query or by `filters["case_number"]`."""
    text = " ".join(str(x) for x in ((filters or {}).get("case_number"), query) if x)
    cases = case_numbers_in(text)
    celex = list(dict.fromkeys(m.upper() for m in CASE_CELEX_RE.findall(text)))
    eclis = list(dict.fromkeys(normalize_ecli(m) for m in ECLI_RE.findall(text)))
    return cases, celex, eclis


def search_cases(client: PoliteClient, query: str, *, limit: int, filters: dict | None = None) -> list[RemoteHit]:
    """Case number / CELEX / ECLI lookup; title-word search only when the caller asked for `eu_judgment`."""
    f = filters or {}
    cases, celex, eclis = query_identifiers(query, f)
    hits: dict[str, RemoteHit] = {}
    if cases or celex:
        cands = list(celex)
        for c in cases:
            cands += celex_for_case(c)
        for m in _search_metas(_run(client, core_query(list(dict.fromkeys(cands))))):
            hits.setdefault(m.celex, _hit(m, "celex" if m.celex in celex else "case_number"))
        if not hits:  # not the first case of a joined-case group: match the identifier of the group
            for c in cases:
                for m in _search_metas(_run(client, joined_case_query(c))):
                    hits.setdefault(m.celex, _hit(m, "joined_case"))
    for e in eclis:
        for m in _search_metas(_run(client, ecli_query(e))):
            hits.setdefault(m.celex, _hit(m, "ecli"))
    if not hits and not (cases or celex or eclis) and "eu_judgment" in (f.get("kinds") or []):
        expr = fulltext_expression(query)
        if expr:
            for m in _search_metas(_run(client, title_query(expr))):
                hits.setdefault(m.celex, _hit(m, "title_words"))
    return list(hits.values())[:limit]


# --------------------------------------------------------------------------- fetch + ingest


def _get_cached_or_fetch(store: Store, client: PoliteClient, url: str, fetch_url: str, accept: str,
                         force: bool) -> tuple[bytes, datetime, bool]:
    if not force:
        snap = store.find_snapshot_by_url(url)
        content = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
        if snap and content is not None:
            return content, snap.fetched_at, False
    r = client.get(fetch_url, accept=accept)
    return r.content, r.fetched_at, True


def variant_url(celex: str, lang: str, fmt: str) -> str:
    return celex_url(celex) if (lang, fmt) == VARIANTS[0] else f"{celex_url(celex)}#{lang}.{fmt}"


def fetch_content(store: Store, client: PoliteClient, celex: str, *, force: bool) -> tuple[bytes, str, str, str, datetime, bool]:
    """(content, url, language, format, fetched_at, fetched_now); first available variant, cached ones first."""
    if not force:
        for lang, fmt in VARIANTS:
            url = variant_url(celex, lang, fmt)
            snap = store.find_snapshot_by_url(url)
            data = store.read_snapshot_bytes(snap.snapshot_id) if snap else None
            if snap and data is not None:
                return data, url, lang, fmt, snap.fetched_at, False
    tried: list[str] = []
    for lang, fmt in VARIANTS:
        try:
            r = client.get(celex_url(celex), accept=FORMATS[fmt], headers={"Accept-Language": lang})
        except NotFoundUpstream:
            tried.append(f"{lang}/{fmt}")
            continue
        if b"<html" not in r.content[:3000].lower():
            raise SourceUnavailable(celex_url(celex), f"nieoczekiwany typ odpowiedzi Cellar: {r.content_type}", r.status)
        return r.content, variant_url(celex, lang, fmt), lang, fmt, r.fetched_at, True
    raise NotFoundUpstream(celex_url(celex), f"Cellar nie ma treści dokumentu w żadnym z wariantów (404): {', '.join(tried)}", 404)


def ingest_case(store: Store, celex: str, core: bytes, core_url: str, core_at: datetime | None,
                rel: bytes | None, rel_url: str, rel_at: datetime | None,
                content: bytes, content_url: str, content_at: datetime | None, lang: str, fmt: str,
                *, content_type: str | None = None) -> tuple[Judgment, list[str]]:
    """Parse everything first, then write snapshots, document and judgment. Raises before any write."""
    metas = parse_core_rows(_bindings(core, core_url))
    meta = metas.get(celex)
    if meta is None:
        raise NotFoundUpstream(core_url, f"Cellar nie zna dokumentu {celex} (brak metadanych SPARQL)", 404)
    warnings: list[str] = []
    if rel is not None:
        parse_relation_rows(_bindings(rel, rel_url), meta)
    text = case_text(content)
    if not text:
        raise SourceUnavailable(content_url, "odpowiedź Cellar nie zawiera tekstu orzeczenia (nieoczekiwany format)")
    flags: list[str] = []
    if lang != "pol":
        flags.append(f"language_fallback:{lang}")
        warnings.append(f"celex:{celex}: brak polskiej wersji w Cellar; zapisano tekst w języku {lang}.")
    if fmt == "html":
        flags.append("legacy_html_format")
    if rel is None:
        flags.append("relations_unavailable")
    ctype = content_type or FORMATS[fmt]
    csnap = store.put_snapshot(SOURCE_ID, content_url, content, ctype, parser_version=PARSER_VERSION, fetched_at=content_at)
    msnap = store.put_snapshot(SOURCE_ID, core_url, core, SPARQL_JSON, parser_version=PARSER_VERSION, fetched_at=core_at)
    extra = {}
    if rel is not None:
        rsnap = store.put_snapshot(SOURCE_ID, rel_url, rel, SPARQL_JSON, parser_version=PARSER_VERSION, fetched_at=rel_at)
        extra["relations_snapshot_id"] = rsnap.snapshot_id
    judgment, doc = build_case(
        meta, text, snapshot_id=csnap.snapshot_id, sha256=csnap.sha256, meta_snapshot_id=msnap.snapshot_id,
        original_url=EURLEX_HUMAN.format(celex=celex), source_url=celex_url(celex), language=lang, fmt=fmt,
        fallback_flags=flags, extra=extra)
    store.upsert_document(doc)
    store.upsert_judgment(judgment, title=doc.title)
    return judgment, warnings


def sync_case(store: Store, client: PoliteClient, celex: str, *, force: bool = False) -> CaseIngest:
    """Metadata (2 SPARQL requests) + text (first available of Polish XHTML / HTML, then flagged fallbacks)."""
    if not is_case_celex(celex):
        raise ValueError(f"not a CJEU CELEX number: {celex!r}")
    core_url = _sparql_url(core_query([celex]))
    core, core_at, fresh_core = _get_cached_or_fetch(store, client, core_url, core_url, SPARQL_JSON, force)
    metas = parse_core_rows(_bindings(core, core_url))
    if celex not in metas:
        raise NotFoundUpstream(core_url, f"Cellar nie zna dokumentu {celex} (brak metadanych SPARQL)", 404)
    if not metas[celex].title:  # no Polish expression title: try the English one for the metadata
        en_url = _sparql_url(core_query([celex], "ENG"))
        en, en_at, fresh_en = _get_cached_or_fetch(store, client, en_url, en_url, SPARQL_JSON, force)
        if celex in parse_core_rows(_bindings(en, en_url)):
            core, core_url, core_at, fresh_core = en, en_url, en_at, fresh_en
    rel_url = _sparql_url(relations_query(celex))
    rel: bytes | None = None
    rel_at = None
    try:
        rel, rel_at, _ = _get_cached_or_fetch(store, client, rel_url, rel_url, SPARQL_JSON, force)
        _bindings(rel, rel_url)
    except (SourceUnavailable, NotFoundUpstream):
        rel = None
    content, c_url, lang, fmt, c_at, fresh_c = fetch_content(store, client, celex, force=force)
    j, warnings = ingest_case(store, celex, core, core_url, core_at, rel, rel_url, rel_at, content, c_url, c_at, lang, fmt)
    return CaseIngest(j.document_id, celex, len(j.text), j.snapshot_id, fresh_c or fresh_core, warnings)
