"""Shared data contracts (pydantic models, statuses, locator helpers).

All tool outputs are wrapped in `ToolResult`. Statuses are explicit: an empty
result never means that a provision or judgment does not exist.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

CONTRACT_VERSION = "0.1.0"


# --------------------------------------------------------------------------- statuses


class ResultStatus(str, Enum):
    ok = "ok"
    not_found = "not_found"  # not in the local corpus; does NOT mean it does not exist
    ambiguous = "ambiguous"  # several candidates, caller must choose
    source_unavailable = "source_unavailable"  # network/HTTP/parse failure of the upstream
    stale = "stale"  # data older than the source's freshness policy
    out_of_scope = "out_of_scope"  # outside the supported legal area / source kinds
    temporal_unknown = "temporal_unknown"  # text found, version for the date not established
    invalid_input = "invalid_input"
    blocked = "blocked"  # operation refused by a server-side gate (e.g. export)


class TemporalStatus(str, Enum):
    confirmed = "confirmed"  # a curated, explicitly verified interval covers relevant_date
    consolidated_text = "consolidated_text"  # official consolidated text; state date known; no known conflicting change
    original_publication = "original_publication"  # text as originally published (no consolidation)
    unknown = "unknown"  # cannot establish which wording applies on relevant_date
    not_requested = "not_requested"  # caller gave no relevant_date


class CitationStatus(str, Enum):
    verified_exact = "verified_exact"  # quote found byte-for-byte (after NFC) in the cited version
    verified_normalized = "verified_normalized"  # found after whitespace/hyphenation normalisation
    mismatch = "mismatch"  # document/locator exists, quote not found there
    wrong_locator = "wrong_locator"  # quote exists in the document but under another locator
    version_mismatch = "version_mismatch"  # quote only in another version/snapshot than cited
    document_not_found = "document_not_found"  # id not in local corpus
    source_unavailable = "source_unavailable"
    unmapped = "unmapped"  # claim has no evidence at all
    metadata_mismatch = "metadata_mismatch"  # e.g. case number exists but court/date differ
    ambiguous_reference = "ambiguous_reference"  # case number matches several judgments; court/date needed


class SemanticReviewStatus(str, Enum):
    not_performed = "not_performed"
    client_reported_pass = "client_reported_pass"  # reported by the client AI; server did not verify
    client_reported_issues = "client_reported_issues"


class ReviewerType(str, Enum):
    none = "none"
    llm = "llm"  # an LLM reviewer run by the client; never means a lawyer checked it
    human_unverified = "human_unverified"  # declared by the user, identity/competence not verified


class SourceKind(str, Enum):
    statute = "statute"  # PL act (ELI)
    judgment = "judgment"  # PL court judgment (SAOS)
    eu_act = "eu_act"  # EU act (Cellar)
    eu_judgment = "eu_judgment"


# --------------------------------------------------------------------------- records


class SourceRecord(BaseModel):
    source_id: str  # "eli", "saos", "cellar"
    name: str | None = None
    maturity: str | None = None  # stable | beta | experimental | research
    terms_url: str | None = None
    publisher: str
    base_url: str
    terms_of_use: str  # short statement + link; data rights assessed separately from code licence
    terms_checked_at: date
    required_attribution: str | None = None
    last_successful_sync: datetime | None = None
    access_status: Literal["ok", "degraded", "unavailable", "never_synced"] = "never_synced"
    coverage: str  # human-readable scope actually held locally
    known_gaps: list[str] = []
    supported_intervals: list[str] = []  # e.g. "KC: TJ Dz.U. 2026 poz. 795, stan na 2026-05-19"


class Snapshot(BaseModel):
    snapshot_id: str  # "<source_id>:<sha256[:16]>"
    source_id: str
    url: str  # URL actually fetched
    fetched_at: datetime
    sha256: str
    content_type: str
    size_bytes: int
    parser_version: str | None = None
    raw_path: str  # path relative to the data dir


class LegalDocument(BaseModel):
    document_id: str  # "eli:DU/1964/93", "saos:31345", "celex:32011L0083"
    kind: SourceKind
    eli: str | None = None
    celex: str | None = None
    ecli: str | None = None
    title: str
    publication: str | None = None  # e.g. "Dz.U. 2026 poz. 795"
    original_url: str  # human-facing URL at the original publisher
    snapshot_id: str
    sha256: str
    metadata: dict[str, Any] = {}


class ProvisionVersion(BaseModel):
    provision_id: str  # f"{document_id}#{locator}@{version_id}"
    document_id: str  # logical act, e.g. "eli:DU/2014/827"
    locator: str  # canonical, see canonical_locator()
    text: str
    version_id: str  # e.g. "eli:DU/2026/1244" (consolidated text) or "celex:32011L0083:oj"
    version_label: str  # e.g. "tekst jednolity Dz.U. 2026 poz. 1244"
    text_state_date: date | None = None  # "z uwzględnieniem stanu prawnego na dzień"
    valid_from: date | None = None  # only when curated and verified
    valid_to: date | None = None
    temporal_basis: TemporalStatus
    pending_changes: list[str] = []  # changes included in the text with later entry into force
    excluded_provisions: list[str] = []  # e.g. transitional rules the TJ says it does not include
    snapshot_id: str
    page_hint: str | None = None  # page(s) in the original PDF
    warnings: list[str] = []  # parser warnings, e.g. uncertain superscript restoration


class Judgment(BaseModel):
    document_id: str  # "saos:<id>"
    source_judgment_id: str  # id at SAOS
    publisher_id: str | None = None  # id at the original publisher (e.g. orzeczenia.ms.gov.pl)
    court_name: str
    court_type: str
    case_numbers: list[str]
    judgment_date: date | None
    judgment_type: str
    text: str
    finality: Literal["unknown", "final", "not_final"] = "unknown"
    original_url: str | None
    snapshot_id: str
    data_quality_flags: list[str] = []  # e.g. "judgment_date_in_future"


class EvidenceSpan(BaseModel):
    evidence_id: str  # caller-chosen, unique within a request
    document_id: str
    locator: str | None = None  # "art. 27 ust. 1" / section of a judgment
    version_id: str | None = None  # expected version; None = "current local"
    snapshot_id: str | None = None
    quote: str  # exact text the claim relies on
    metadata: dict[str, Any] = {}  # e.g. {"court_name": ..., "judgment_date": ...} for case-number references


class ClaimType(str, Enum):
    fact = "fact"
    law = "law"
    conclusion = "conclusion"


class Claim(BaseModel):
    claim_id: str
    text: str
    type: ClaimType
    evidence_ids: list[str] = []
    premises: list[str] = []  # claim_ids or fact ids the conclusion depends on
    counterarguments: list[str] = []
    gaps: list[str] = []


# --------------------------------------------------------------------------- tool envelopes


class Coverage(BaseModel):
    sources_searched: list[str] = []
    sources_unavailable: list[str] = []
    corpus_note: str = "Wynik dotyczy tylko lokalnego korpusu; brak trafienia nie oznacza nieistnienia."


class ToolResult(BaseModel):
    status: ResultStatus
    data: Any = None
    warnings: list[str] = []
    coverage: Coverage = Field(default_factory=Coverage)
    contract_version: str = CONTRACT_VERSION


class SearchHit(BaseModel):
    document_id: str
    kind: SourceKind
    title: str
    locator: str | None = None
    version_id: str | None = None
    snippet: str  # <= ~800 chars; truncation marked with "[…]"
    original_url: str
    snapshot_id: str
    fetched_at: datetime | None = None
    score: float | None = None
    metadata: dict[str, Any] = {}


class CitationCheck(BaseModel):
    evidence_id: str
    status: CitationStatus
    found_in_locator: str | None = None
    found_in_version: str | None = None
    detail: str = ""


class ClaimCheck(BaseModel):
    claim_id: str
    claim_type: ClaimType | None = None  # fact claims may be unmapped (they come from the user)
    citation_status: CitationStatus  # worst status over the claim's evidence
    temporal_status: TemporalStatus
    semantic_review_status: SemanticReviewStatus = SemanticReviewStatus.not_performed
    reviewer_type: ReviewerType = ReviewerType.none
    evidence: list[CitationCheck] = []
    unresolved_issues: list[str] = []


class CitationReport(BaseModel):
    report_id: str  # hash of inputs + snapshot ids; any change invalidates it
    created_at: datetime
    claims: list[ClaimCheck]
    critical_errors: list[str] = []
    snapshot_ids: list[str] = []  # snapshots the verdicts depend on
    binding_hash: str | None = None  # sha256 of (facts, draft, template_id, template_version) if given
    relevant_date: date | None = None
    client_review_provided: bool = False  # semantic review statuses came from the client, not the server
    note: str = (
        "Kontrola sprawdza istnienie źródła i wierność cytatu (pytania 1–2). "
        "Nie ocenia, czy źródło wspiera twierdzenie ani czy prawo ma zastosowanie (pytania 3–4)."
    )


# --------------------------------------------------------------------------- helpers

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def normalize_text(s: str) -> str:
    """Normalisation used for `verified_normalized` comparisons.

    NFC, unify quotes/dashes, join words hyphenated at line breaks, collapse whitespace.
    Never used to *produce* quotes shown to the user.
    """
    s = unicodedata.normalize("NFC", s)
    s = s.replace("­", "").replace(" ", " ")
    s = re.sub(r"[„”“«»]", '"', s)
    s = re.sub(r"[‘’]", "'", s)
    s = re.sub(r"[–—−]", "-", s)
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


_LOC_RE = re.compile(
    r"^\s*art\.?\s*(?P<art>\d+[a-z]{0,3}(?:\s*\^\s*\d+|[¹²³⁰-⁹]+)?)"
    r"(?:\s*§\s*(?P<par>\d+[a-z]?(?:\^\d+)?))?"
    r"(?:\s*ust\.?\s*(?P<ust>\d+[a-z]?(?:\^\d+)?))?"
    r"(?:\s*pkt\.?\s*(?P<pkt>\d+[a-z]?(?:\^\d+)?))?"
    r"(?:\s*lit\.?\s*(?P<lit>[a-z]))?\s*$",
    re.IGNORECASE,
)


def canonical_locator(loc: str) -> str | None:
    """'Art. 385(1) §1' / 'art 385^1 § 1' / 'art. 385¹ § 1' -> 'art. 385^1 § 1'. None if unparseable.

    Superscript article numbers are written with '^' (PDF extraction flattens them;
    the parser must restore them, see parsers/)."""
    loc = re.sub(r"\((\d+)\)", r"^\1", loc.strip())
    m = _LOC_RE.match(loc)
    if not m:
        return None
    art = re.sub(r"\s+", "", m["art"])
    sup = re.search(r"[¹²³⁰-⁹]+", art)
    if sup:
        art = art[: sup.start()] + "^" + sup.group().translate(_SUP)
    out = f"art. {art.lower()}"
    if m["par"]:
        out += f" § {m['par']}"
    if m["ust"]:
        out += f" ust. {m['ust']}"
    if m["pkt"]:
        out += f" pkt {m['pkt']}"
    if m["lit"]:
        out += f" lit. {m['lit'].lower()}"
    return out


def article_of(locator: str) -> str | None:
    """'art. 27 ust. 1' -> 'art. 27'."""
    c = canonical_locator(locator)
    return c.split(" §")[0].split(" ust.")[0].split(" pkt")[0].split(" lit.")[0] if c else None


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def snapshot_id_for(source_id: str, sha256: str) -> str:
    return f"{source_id}:{sha256[:16]}"
