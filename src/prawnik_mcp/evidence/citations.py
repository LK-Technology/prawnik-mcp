"""Citation check: answers only questions 1–2.

1. Does the cited document exist in the local corpus?
2. Is the quote faithful, under the cited locator and in the cited version?

It does NOT judge whether a source supports a claim or whether the law applies
(questions 3–4). Evidence text is treated purely as data: it is compared, never
interpreted or executed.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from prawnik_mcp.contracts import (
    CONTRACT_VERSION,
    CitationCheck,
    CitationReport,
    CitationStatus,
    Claim,
    ClaimCheck,
    ClaimType,
    EvidenceSpan,
    Judgment,
    ProvisionVersion,
    ReviewerType,
    SemanticReviewStatus,
    TemporalStatus,
    article_of,
    canonical_locator,
    normalize_text,
)
from prawnik_mcp.evidence.temporal import temporal_status_for
from prawnik_mcp.store import Store, normalize_case_number

SHORT_QUOTE_CHARS = 20
SHORT_FRAGMENT_CHARS = 8
NOT_IN_CORPUS = "brak w lokalnym korpusie – nie oznacza, że nie istnieje"
SHORT_WARNING = "cytat zbyt krótki do jednoznacznej weryfikacji"

# worst-first ordering used to aggregate a claim's evidence
_SEVERITY = {
    CitationStatus.verified_exact: 0,
    CitationStatus.verified_normalized: 1,
    CitationStatus.source_unavailable: 2,
    CitationStatus.wrong_locator: 3,
    CitationStatus.version_mismatch: 4,
    CitationStatus.metadata_mismatch: 5,
    CitationStatus.ambiguous_reference: 5,
    CitationStatus.mismatch: 6,
    CitationStatus.document_not_found: 7,
    CitationStatus.unmapped: 8,
}

_CRITICAL_LAW = {
    CitationStatus.mismatch,
    CitationStatus.wrong_locator,
    CitationStatus.version_mismatch,
    CitationStatus.document_not_found,
    CitationStatus.metadata_mismatch,
    CitationStatus.ambiguous_reference,
    CitationStatus.unmapped,
}
_CRITICAL_CONCLUSION = _CRITICAL_LAW - {CitationStatus.wrong_locator}

_TEMPORAL_SEVERITY = {
    TemporalStatus.confirmed: 0,
    TemporalStatus.consolidated_text: 1,
    TemporalStatus.original_publication: 2,
    TemporalStatus.not_requested: 3,
    TemporalStatus.unknown: 4,
}


# --------------------------------------------------------------------------- quote matching

_ELLIPSIS_RE = re.compile(r"\[\s*(?:…|\.\.\.)\s*\]|\(\s*(?:…|\.\.\.)\s*\)|…|\.\.\.")
_TAG_RE = re.compile(r"<[^>]+>")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _strip_html(s: str) -> str:
    if "<" not in s:
        return s
    return html.unescape(_TAG_RE.sub(" ", s))


def _fragments(quote: str) -> list[str]:
    return [f.strip() for f in _ELLIPSIS_RE.split(quote) if f.strip()]


def _in_order(frags: list[str], text: str) -> bool:
    pos = 0
    for f in frags:
        i = text.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


MatchKind = Literal["exact", "normalized"]


def match_quote(quote: str, text: str, *, is_html: bool = False) -> MatchKind | None:
    """Exact (NFC) -> normalized; ellipsis fragments must appear in order in the same text."""
    q = _nfc(quote).strip()
    if not q:
        return None
    t = _nfc(text)
    if q in t:
        return "exact"
    frags = _fragments(q)
    if len(frags) > 1 and _in_order(frags, t):
        return "exact"
    plain = _strip_html(t) if is_html else t
    nt = normalize_text(plain)
    nq = normalize_text(q)
    if nq and nq in nt:
        return "normalized"
    nfr = [normalize_text(f) for f in frags]
    nfr = [f for f in nfr if f]
    if nfr and _in_order(nfr, nt):
        return "normalized"
    return None


def _short_quote(quote: str) -> bool:
    frags = [normalize_text(f) for f in _fragments(quote)]
    total = sum(len(f) for f in frags)
    return total < SHORT_QUOTE_CHARS or (len(frags) > 1 and min(len(f) for f in frags) < SHORT_FRAGMENT_CHARS)


def _verified(kind: MatchKind) -> CitationStatus:
    return CitationStatus.verified_exact if kind == "exact" else CitationStatus.verified_normalized


# --------------------------------------------------------------------------- judgment references

_COURT_ABBR = [
    (r"^sn\b", "sąd najwyższy"),
    (r"^sa\b", "sąd apelacyjny"),
    (r"^so\b", "sąd okręgowy"),
    (r"^sr\b", "sąd rejonowy"),
    (r"^nsa\b", "naczelny sąd administracyjny"),
    (r"^wsa\b", "wojewódzki sąd administracyjny"),
]


def _norm_court(s: str) -> str:
    s = " ".join(_nfc(s).casefold().replace(".", " ").split())
    for pat, full in _COURT_ABBR:
        s = re.sub(pat, full, s)
    return s


def _date_unverifiable(j: Judgment) -> bool:
    return j.judgment_date is None or any("date" in f for f in j.data_quality_flags)


class JudgmentRefCheck(BaseModel):
    status: Literal["ok", "not_found", "metadata_mismatch", "ambiguous"]
    case_number: str
    candidates: list[str] = []  # document_ids
    detail: str = ""
    warnings: list[str] = []


def verify_judgment_reference(
    store: Store,
    case_number: str,
    court_name: str | None = None,
    judgment_date: date | None = None,
) -> JudgmentRefCheck:
    """Check that a case number (+ optional court/date) matches a judgment in the local corpus.

    Case numbers are not globally unique: the same number can exist in many courts.
    """
    found = store.find_judgments_by_case_number(case_number)
    if not found:
        return JudgmentRefCheck(status="not_found", case_number=case_number, detail=NOT_IN_CORPUS)

    warnings: list[str] = []
    matching: list[Judgment] = []
    mismatch_details: list[str] = []
    for j in found:
        problems: list[str] = []
        if court_name and _norm_court(court_name) != _norm_court(j.court_name):
            problems.append(f"sąd: podano „{court_name}”, w korpusie „{j.court_name}”")
        if judgment_date:
            if _date_unverifiable(j):
                warnings.append(
                    f"{j.document_id}: data wyroku w źródle jest błędna lub brak jej "
                    f"({j.judgment_date}); nie można jej porównać"
                )
            elif j.judgment_date != judgment_date:
                problems.append(f"data: podano {judgment_date.isoformat()}, w korpusie {j.judgment_date.isoformat()}")
        if problems:
            mismatch_details.append(f"{j.document_id}: " + "; ".join(problems))
        else:
            matching.append(j)

    if not matching:
        return JudgmentRefCheck(
            status="metadata_mismatch", case_number=case_number,
            candidates=[j.document_id for j in found],
            detail="sygnatura istnieje w korpusie, ale metadane się nie zgadzają: " + " | ".join(mismatch_details),
            warnings=warnings,
        )
    if len(matching) > 1:
        return JudgmentRefCheck(
            status="ambiguous", case_number=case_number,
            candidates=[j.document_id for j in matching],
            detail="kilka orzeczeń o tej sygnaturze – podaj sąd i datę",
            warnings=warnings,
        )
    return JudgmentRefCheck(
        status="ok", case_number=case_number, candidates=[matching[0].document_id], warnings=warnings
    )


def _parse_case_ref(document_id: str, ev: EvidenceSpan) -> tuple[str, str | None, date | None] | None:
    """'sygn:I ACa 772/13;sad=Sąd Apelacyjny w Łodzi;data=2013-12-04' (+ optional ev.metadata)."""
    for prefix in ("sygn:", "case:"):
        if document_id.startswith(prefix):
            break
    else:
        return None
    parts = [p.strip() for p in document_id[len(prefix):].split(";")]
    case, court, jdate = parts[0], None, None
    for p in parts[1:]:
        k, _, v = p.partition("=")
        k = k.strip().casefold()
        if k in ("sad", "sąd", "court"):
            court = v.strip()
        elif k in ("data", "date"):
            try:
                jdate = date.fromisoformat(v.strip())
            except ValueError:
                jdate = None
    meta = getattr(ev, "metadata", None) or {}
    court = court or meta.get("court_name")
    if jdate is None and meta.get("judgment_date"):
        try:
            jdate = date.fromisoformat(str(meta["judgment_date"])[:10])
        except ValueError:
            pass
    return case, court, jdate


# --------------------------------------------------------------------------- per-evidence checks


class _EvResult:
    def __init__(self, check: CitationCheck, provisions: list[ProvisionVersion] | None = None,
                 snapshots: list[str] | None = None, issues: list[str] | None = None):
        self.check = check
        self.provisions = provisions or []
        self.snapshots = snapshots or []
        self.issues = issues or []


def _check_judgment_quote(ev: EvidenceSpan, j: Judgment) -> _EvResult:
    kind = match_quote(ev.quote, j.text, is_html=True)
    issues: list[str] = []
    if j.data_quality_flags:
        issues.append(f"{j.document_id}: problemy jakości danych źródła: {', '.join(j.data_quality_flags)}")
    if ev.locator:
        issues.append(f"{ev.evidence_id}: lokalizator w orzeczeniu („{ev.locator}”) nie jest weryfikowany – "
                      "sprawdzono cały tekst orzeczenia")
    if kind is None:
        check = CitationCheck(evidence_id=ev.evidence_id, status=CitationStatus.mismatch,
                              detail=f"cytatu nie znaleziono w tekście {j.document_id}")
    else:
        check = CitationCheck(evidence_id=ev.evidence_id, status=_verified(kind), found_in_version=j.document_id)
    return _EvResult(check, snapshots=[j.snapshot_id], issues=issues)


def _check_case_ref(store: Store, ev: EvidenceSpan, ref: tuple[str, str | None, date | None]) -> _EvResult:
    case, court, jdate = ref
    r = verify_judgment_reference(store, case, court, jdate)
    if r.status == "not_found":
        return _EvResult(CitationCheck(evidence_id=ev.evidence_id, status=CitationStatus.document_not_found,
                                       detail=f"sygnatura {case}: {NOT_IN_CORPUS}"))
    if r.status == "metadata_mismatch":
        return _EvResult(CitationCheck(evidence_id=ev.evidence_id, status=CitationStatus.metadata_mismatch,
                                       detail=r.detail), issues=r.warnings)
    candidates = [j for d in r.candidates if (j := store.get_judgment(d))]
    hits = [(j, match_quote(ev.quote, j.text, is_html=True)) for j in candidates]
    hits = [(j, k) for j, k in hits if k]
    if len(hits) == 1:
        res = _check_judgment_quote(ev, hits[0][0])
        res.issues = r.warnings + res.issues
        if r.status == "ambiguous":
            res.issues.append(f"sygnatura {case} niejednoznaczna; cytat odnaleziono tylko w {hits[0][0].document_id}")
        return res
    if len(hits) > 1:
        return _EvResult(
            CitationCheck(evidence_id=ev.evidence_id, status=CitationStatus.ambiguous_reference,
                          detail=f"sygnatura {case} niejednoznaczna: cytat w kilku orzeczeniach "
                                 f"({', '.join(j.document_id for j, _ in hits)}); podaj sąd i datę"),
            snapshots=[j.snapshot_id for j, _ in hits], issues=r.warnings)
    return _EvResult(
        CitationCheck(evidence_id=ev.evidence_id, status=CitationStatus.mismatch,
                      detail=f"cytatu nie znaleziono w orzeczeniach o sygnaturze {case}"),
        snapshots=[j.snapshot_id for j in candidates], issues=r.warnings)


def _latest_version(provs: list[ProvisionVersion]) -> str | None:
    best: dict[str, date] = {}
    for p in provs:
        d = p.text_state_date or date.min
        if p.version_id not in best or d > best[p.version_id]:
            best[p.version_id] = d
    if not best:
        return None
    return max(best, key=lambda v: (best[v], v))


def _check_provision_quote(store: Store, ev: EvidenceSpan) -> _EvResult:
    eid = ev.evidence_id
    all_provs = store.get_provisions(ev.document_id)
    doc = store.get_document(ev.document_id)
    if not all_provs:
        if doc is None:
            return _EvResult(CitationCheck(evidence_id=eid, status=CitationStatus.document_not_found,
                                           detail=f"{ev.document_id}: {NOT_IN_CORPUS}"))
        return _EvResult(CitationCheck(
            evidence_id=eid, status=CitationStatus.source_unavailable,
            detail=f"{ev.document_id} jest w katalogu, ale jego tekst nie został wczytany do lokalnego korpusu"),
            snapshots=[doc.snapshot_id])

    issues: list[str] = []
    versions = sorted({p.version_id for p in all_provs})
    target_version = ev.version_id or _latest_version(all_provs)
    version_known = target_version in versions
    in_version = [p for p in all_provs if p.version_id == target_version]
    others = [p for p in all_provs if p.version_id != target_version]

    canon: str | None = None
    article: str | None = None
    if ev.locator:
        canon = canonical_locator(ev.locator)
        article = article_of(ev.locator) if canon else None
        if canon is None:
            issues.append(f"{eid}: nieczytelny lokalizator „{ev.locator}”")
    else:
        issues.append(f"{eid}: brak lokalizatora – cytat szukany w całym akcie")

    if canon and canon != article:
        issues.append(f"{eid}: zgodność sprawdzona na poziomie {article}; jednostka „{canon}” nie jest wydzielana")

    def found(provs: list[ProvisionVersion]) -> list[tuple[ProvisionVersion, MatchKind]]:
        out = []
        for p in provs:
            k = match_quote(ev.quote, p.text)
            if k:
                out.append((p, k))
        return out

    # 1) cited unit in cited version
    if version_known:
        cited = [p for p in in_version if article is None or p.locator == article] if (article or not ev.locator) else []
        hits = found(cited)
        if hits:
            p, k = hits[0]
            if ev.snapshot_id and ev.snapshot_id != p.snapshot_id:
                issues.append(f"{eid}: podany snapshot {ev.snapshot_id} różni się od lokalnego {p.snapshot_id}")
            return _EvResult(
                CitationCheck(evidence_id=eid, status=_verified(k), found_in_locator=p.locator,
                              found_in_version=p.version_id),
                provisions=[p], snapshots=[p.snapshot_id], issues=issues)
        # 2) elsewhere in the same version
        if article is not None or ev.locator:
            hits = found([p for p in in_version if p.locator != article])
            if hits:
                locs = ", ".join(sorted({p.locator for p, _ in hits}))
                missing = "" if any(p.locator == article for p in in_version) else \
                    f" (brak {article} w lokalnej wersji)"
                return _EvResult(
                    CitationCheck(evidence_id=eid, status=CitationStatus.wrong_locator,
                                  found_in_locator=hits[0][0].locator, found_in_version=target_version,
                                  detail=f"cytat występuje w: {locs}, a nie w {canon or ev.locator}{missing}"),
                    provisions=[p for p, _ in hits], snapshots=[p.snapshot_id for p, _ in hits], issues=issues)

    # 3) other versions
    hits = found(others)
    if hits:
        same_loc = [(p, k) for p, k in hits if article is None or p.locator == article]
        p, _ = (same_loc or hits)[0]
        why = "cytowana wersja nie istnieje w lokalnym korpusie" if not version_known else \
            "cytat nie występuje w cytowanej wersji"
        return _EvResult(
            CitationCheck(evidence_id=eid, status=CitationStatus.version_mismatch,
                          found_in_locator=p.locator, found_in_version=p.version_id,
                          detail=f"{why} ({target_version}); znaleziono w wersji {p.version_id}, {p.locator}"),
            provisions=[p], snapshots=[p.snapshot_id], issues=issues)

    if not version_known:
        return _EvResult(CitationCheck(
            evidence_id=eid, status=CitationStatus.document_not_found,
            detail=f"wersja {target_version} aktu {ev.document_id}: {NOT_IN_CORPUS}"), issues=issues)

    detail = f"cytatu nie znaleziono w {ev.document_id} ({target_version})"
    if article and not any(p.locator == article for p in in_version):
        detail += f"; brak {article} w lokalnej wersji"
    snaps = sorted({p.snapshot_id for p in in_version})
    return _EvResult(CitationCheck(evidence_id=eid, status=CitationStatus.mismatch, detail=detail),
                     snapshots=snaps, issues=issues)


def _check_evidence(store: Store, ev: EvidenceSpan) -> _EvResult:
    ref = _parse_case_ref(ev.document_id, ev)
    if ref is not None:
        res = _check_case_ref(store, ev, ref)
    elif (j := store.get_judgment(ev.document_id)) is not None:
        res = _check_judgment_quote(ev, j)
    elif ev.document_id.startswith("saos:"):
        res = _EvResult(CitationCheck(evidence_id=ev.evidence_id, status=CitationStatus.document_not_found,
                                      detail=f"{ev.document_id}: {NOT_IN_CORPUS}"))
    else:
        res = _check_provision_quote(store, ev)
    if res.check.status in (CitationStatus.verified_exact, CitationStatus.verified_normalized) \
            and _short_quote(ev.quote):
        res.issues.append(f"{ev.evidence_id}: {SHORT_WARNING}")
    return res


# --------------------------------------------------------------------------- hashing


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _binding_hash(binding: dict | None) -> str | None:
    if binding is None:
        return None
    if isinstance(binding.get("binding_hash"), str):
        return binding["binding_hash"]
    return stable_hash(binding)


# --------------------------------------------------------------------------- main entry


def _apply_client_review(cc: ClaimCheck, review: dict) -> None:
    status = review.get("status")
    if status == "pass":
        cc.semantic_review_status = SemanticReviewStatus.client_reported_pass
    elif status == "issues":
        cc.semantic_review_status = SemanticReviewStatus.client_reported_issues
    else:
        cc.unresolved_issues.append(f"nieprawidłowy status kontroli klienta: {status!r}")
        return
    rt = review.get("reviewer_type")
    try:
        cc.reviewer_type = ReviewerType(rt)
    except ValueError:
        cc.reviewer_type = ReviewerType.none
        cc.unresolved_issues.append(f"nieznany typ recenzenta: {rt!r}")
    if cc.reviewer_type == ReviewerType.none and status == "pass":
        # a pass without a reviewer is not a review
        cc.semantic_review_status = SemanticReviewStatus.not_performed
    for issue in review.get("issues") or []:
        cc.unresolved_issues.append(f"kontrola klienta: {issue}")


def check_citations(
    store: Store,
    claims: list[Claim],
    evidence: list[EvidenceSpan],
    *,
    relevant_date: date | None = None,
    binding: dict | None = None,
    client_review: dict[str, dict] | None = None,
    today: date | None = None,
) -> CitationReport:
    ev_by_id: dict[str, EvidenceSpan] = {}
    critical: list[str] = []
    for ev in evidence:
        if ev.evidence_id in ev_by_id:
            critical.append(f"zduplikowany identyfikator dowodu: {ev.evidence_id}")
        ev_by_id[ev.evidence_id] = ev

    results: dict[str, _EvResult] = {eid: _check_evidence(store, ev) for eid, ev in ev_by_id.items()}
    amendments_cache: dict[str, list[dict] | None] = {}

    def amendments_for(p: ProvisionVersion) -> list[dict] | None:
        if p.version_id not in amendments_cache:
            val = None
            for did in (p.version_id, p.document_id):
                d = store.get_document(did)
                if d and isinstance(d.metadata.get("amendments"), list):
                    val = d.metadata["amendments"]
                    break
            amendments_cache[p.version_id] = val
        return amendments_cache[p.version_id]

    snapshot_ids: set[str] = set()
    checks: list[ClaimCheck] = []
    for claim in claims:
        evs: list[CitationCheck] = []
        issues: list[str] = []
        provs: list[ProvisionVersion] = []
        for eid in claim.evidence_ids:
            r = results.get(eid)
            if r is None:
                evs.append(CitationCheck(evidence_id=eid, status=CitationStatus.unmapped,
                                         detail="brak dowodu o tym identyfikatorze w żądaniu"))
                continue
            evs.append(r.check)
            issues.extend(r.issues)
            provs.extend(r.provisions)
            snapshot_ids.update(r.snapshots)

        if not claim.evidence_ids:
            citation_status = CitationStatus.unmapped
            if claim.type == ClaimType.fact:
                issues.append("fakt bez dowodu – przyjęty jako podany przez użytkownika, niezweryfikowany przez serwer")
        else:
            citation_status = max((e.status for e in evs), key=_SEVERITY.__getitem__)

        # temporal
        if provs:
            temporal = TemporalStatus.confirmed
            for p in provs:
                st, reasons = temporal_status_for(p, relevant_date, amendments=amendments_for(p), today=today)
                issues.extend(f"{p.locator} ({p.version_id}): {r}" for r in reasons)
                if _TEMPORAL_SEVERITY[st] > _TEMPORAL_SEVERITY[temporal]:
                    temporal = st
        elif relevant_date is None:
            temporal = TemporalStatus.not_requested
        else:
            temporal = TemporalStatus.unknown
            if claim.type != ClaimType.fact:
                issues.append("brak zweryfikowanego przepisu w dowodach – brzmienie na datę zdarzenia nieustalone")

        cc = ClaimCheck(claim_id=claim.claim_id, claim_type=claim.type, citation_status=citation_status, temporal_status=temporal,
                        evidence=evs, unresolved_issues=list(dict.fromkeys(issues)))
        if client_review and claim.claim_id in client_review:
            _apply_client_review(cc, client_review[claim.claim_id])
        checks.append(cc)

        # critical errors
        crit_set = {ClaimType.law: _CRITICAL_LAW, ClaimType.conclusion: _CRITICAL_CONCLUSION}.get(claim.type)
        if crit_set:
            if not claim.evidence_ids:
                critical.append(f"{claim.claim_id}: twierdzenie typu {claim.type.value} bez dowodu (unmapped)")
            for e in evs:
                if e.status in crit_set:
                    critical.append(f"{claim.claim_id}/{e.evidence_id}: {e.status.value} – {e.detail}".rstrip(" –"))
                elif e.status == CitationStatus.wrong_locator:
                    cc.unresolved_issues.append(f"{e.evidence_id}: cytat pod innym lokalizatorem ({e.found_in_locator})")
            for e in evs:
                if e.status == CitationStatus.source_unavailable:
                    critical.append(f"{claim.claim_id}/{e.evidence_id}: BLOKADA – źródło niedostępne lokalnie "
                                    f"(nie oznacza fałszywego cytatu): {e.detail}")

    binding_hash = _binding_hash(binding)
    snaps = sorted(snapshot_ids)
    report_id = stable_hash({
        "claims": [c.model_dump(mode="json") for c in claims],
        "evidence": [e.model_dump(mode="json") for e in evidence],
        "relevant_date": relevant_date.isoformat() if relevant_date else None,
        "binding_hash": binding_hash,
        "client_review": client_review,
        "snapshot_ids": snaps,
        "contract_version": CONTRACT_VERSION,
    })
    return CitationReport(
        report_id=report_id,
        created_at=datetime.now(UTC),
        claims=checks,
        critical_errors=list(dict.fromkeys(critical)),
        snapshot_ids=snaps,
        binding_hash=binding_hash,
        relevant_date=relevant_date,
        client_review_provided=bool(client_review),
    )
