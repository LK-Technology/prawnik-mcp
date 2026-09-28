"""Structure of a client-side analysis and structural validation.

Structural only: it checks that the analysis is built the way the procedure requires
(workflow/instructions.md). It never judges legal correctness.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel

from prawnik_mcp.contracts import (
    CitationReport,
    Claim,
    ClaimType,
    SemanticReviewStatus,
    TemporalStatus,
)


class Analysis(BaseModel):
    facts_established: list[str] = []  # facts stated by the user
    facts_assumed: list[str] = []  # assumptions made by the assistant, to be confirmed
    missing_information: list[str] = []  # outcome-changing questions still open
    claims: list[Claim] = []
    counterarguments: list[str] = []
    possible_actions: list[str] = []
    out_of_scope_flags: list[str] = []


def validate_analysis(analysis: Analysis, report: CitationReport | None) -> list[str]:
    """Return a list of structural issues (Polish). Empty list = no structural issue found."""
    issues: list[str] = []
    by_id = {c.claim_id: c for c in analysis.claims}

    if len(by_id) != len(analysis.claims):
        issues.append("zduplikowane identyfikatory twierdzeń")

    for c in analysis.claims:
        if c.type == ClaimType.law and not c.evidence_ids:
            issues.append(f"{c.claim_id}: twierdzenie prawne bez dowodu (evidence_ids)")
        if c.type == ClaimType.conclusion:
            if not c.premises:
                issues.append(f"{c.claim_id}: wniosek bez przesłanek")
                continue
            unknown = [p for p in c.premises if p not in by_id]
            if unknown:
                issues.append(f"{c.claim_id}: przesłanki spoza tabeli twierdzeń: {', '.join(unknown)}")
            types = {by_id[p].type for p in c.premises if p in by_id}
            if ClaimType.fact not in types:
                issues.append(f"{c.claim_id}: wniosek bez przesłanki faktycznej")
            if ClaimType.law not in types and not c.evidence_ids:
                issues.append(f"{c.claim_id}: wniosek bez przesłanki prawnej")

    if analysis.out_of_scope_flags:
        issues.append("sprawa dotyczy obszaru poza zakresem: " + ", ".join(analysis.out_of_scope_flags))
    if analysis.missing_information:
        issues.append("brakujące informacje istotne dla wyniku – odpowiedź ograniczona albo pytanie do użytkownika")

    if report is None:
        issues.append("brak raportu check_citations")
        return issues

    if report.critical_errors:
        issues.append(f"raport check_citations zawiera błędy krytyczne ({len(report.critical_errors)})")

    checks = {c.claim_id: c for c in report.claims}
    missing = [c.claim_id for c in analysis.claims if c.claim_id not in checks]
    if missing:
        issues.append("twierdzenia nieobjęte raportem check_citations: " + ", ".join(missing))

    no_review: list[str] = []
    for c in analysis.claims:
        cc = checks.get(c.claim_id)
        if cc is None or c.type == ClaimType.fact:
            continue
        if cc.semantic_review_status == SemanticReviewStatus.not_performed:
            no_review.append(c.claim_id)
        elif cc.semantic_review_status == SemanticReviewStatus.client_reported_issues:
            issues.append(f"{c.claim_id}: kontrola zastosowania zgłosiła problemy")
        if report.relevant_date and cc.temporal_status == TemporalStatus.unknown:
            issues.append(f"{c.claim_id}: brzmienie przepisu na datę zdarzenia nieustalone")
    if no_review:
        issues.append("brak osobnej kontroli zastosowania: " + ", ".join(no_review))
    return issues


# --------------------------------------------------------------------------- out of scope

OUT_OF_SCOPE: dict[str, list[str]] = {
    "pozwy": [r"pozew\w*", r"pozw(ac|e|ie|iemy|em|u|y|ow)\b", r"pozwan\w*", r"wytocz\w* powodztw\w*"],
    "apelacje i środki zaskarżenia": [r"apelacj\w*", r"zazaleni\w*", r"skarg\w* kasacyjn\w*", r"kasacj\w*"],
    "terminy procesowe": [r"termin\w* procesow\w*", r"termin\w* na (wniesienie|apelacj|zazaleni|sprzeciw)\w*",
                          r"sprzeciw\w* od nakazu"],
    "obliczanie przedawnienia": [r"przedawni\w*"],
    "podatki": [r"podat\w*", r"\bpit\b", r"\bvat\b", r"urz\w* skarbow\w*", r"\bzus\b"],
    "prawo karne": [r"karn\w*", r"przestepstw\w*", r"wykroczeni\w*", r"prokurat\w*", r"zawiadomieni\w* o przestepstw\w*"],
    "prawo rodzinne": [r"rozwod\w*", r"aliment\w*", r"wladz\w* rodzicielsk\w*", r"separacj\w*",
                       r"kontakt\w* z dzieckiem", r"opiek\w* nad dzieckiem"],
    "prawo migracyjne": [r"\bwiz(a|y|e|ie)\b", r"karta pobytu", r"karty pobytu", r"zezwoleni\w* na pobyt",
                         r"cudzoziem\w*", r"obywatelstw\w*", r"deportac\w*", r"azyl\w*"],
    "nieruchomości": [r"nieruchomos\w*", r"hipote\w*", r"ksieg\w* wieczyst\w*", r"dzialk\w*",
                      r"akt\w* notarialn\w*", r"zakup\w* mieszkani\w*", r"kupn\w* mieszkani\w*"],
}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold()).replace("ł", "l")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def detect_out_of_scope(text: str) -> list[str]:
    """Keyword-based, deliberately over-inclusive. A flag means "check scope", not a legal finding."""
    t = _fold(text)
    return [area for area, pats in OUT_OF_SCOPE.items()
            if any(re.search(r"(?<!\w)" + p, t) for p in pats)]
