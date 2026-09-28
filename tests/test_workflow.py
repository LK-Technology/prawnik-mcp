"""Structural validation of an analysis (no LLM, no legal judgement)."""

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

from prawnik_mcp.contracts import (
    CitationReport,
    CitationStatus,
    Claim,
    ClaimCheck,
    ClaimType,
    ReviewerType,
    SemanticReviewStatus,
    TemporalStatus,
)
from prawnik_mcp.workflow.analysis import Analysis, detect_out_of_scope, validate_analysis

WF = Path(__file__).parents[1] / "src" / "prawnik_mcp" / "workflow"


def claims() -> list[Claim]:
    return [
        Claim(claim_id="F1", text="Użytkownik kupił towar w sklepie internetowym.", type=ClaimType.fact),
        Claim(claim_id="L1", text="Konsument może odstąpić od umowy zawartej na odległość w terminie 14 dni.",
              type=ClaimType.law, evidence_ids=["E1"]),
        Claim(claim_id="K1", text="Użytkownik może odstąpić od umowy.", type=ClaimType.conclusion,
              premises=["F1", "L1"]),
    ]


def report(review: SemanticReviewStatus = SemanticReviewStatus.client_reported_pass,
           critical: list[str] | None = None, temporal=TemporalStatus.consolidated_text) -> CitationReport:
    def cc(cid, st):
        return ClaimCheck(claim_id=cid, citation_status=st, temporal_status=temporal,
                          semantic_review_status=review, reviewer_type=ReviewerType.llm)
    return CitationReport(
        report_id="x", created_at=datetime.now(UTC), relevant_date=date(2026, 9, 10),
        claims=[cc("F1", CitationStatus.unmapped), cc("L1", CitationStatus.verified_exact),
                cc("K1", CitationStatus.verified_exact)],
        critical_errors=critical or [],
    )


def test_clean_analysis_has_no_issues():
    a = Analysis(facts_established=["zakup przez internet"], claims=claims())
    assert validate_analysis(a, report()) == []


def test_missing_report():
    assert "brak raportu check_citations" in validate_analysis(Analysis(claims=claims()), None)


def test_report_with_critical_errors():
    issues = validate_analysis(Analysis(claims=claims()), report(critical=["L1/E1: mismatch"]))
    assert any("błędy krytyczne" in i for i in issues)


def test_no_semantic_review():
    issues = validate_analysis(Analysis(claims=claims()), report(SemanticReviewStatus.not_performed))
    assert any("brak osobnej kontroli zastosowania" in i for i in issues)


def test_review_reported_issues_and_temporal_unknown():
    issues = validate_analysis(Analysis(claims=claims()),
                               report(SemanticReviewStatus.client_reported_issues, temporal=TemporalStatus.unknown))
    assert any("zgłosiła problemy" in i for i in issues)
    assert any("nieustalone" in i for i in issues)


def test_law_without_evidence_and_conclusion_without_premises():
    cs = claims()
    cs[1] = cs[1].model_copy(update={"evidence_ids": []})
    cs[2] = cs[2].model_copy(update={"premises": []})
    issues = validate_analysis(Analysis(claims=cs), report())
    assert any("L1: twierdzenie prawne bez dowodu" in i for i in issues)
    assert any("K1: wniosek bez przesłanek" in i for i in issues)


def test_conclusion_without_fact_premise():
    cs = claims()
    cs[2] = cs[2].model_copy(update={"premises": ["L1"]})
    issues = validate_analysis(Analysis(claims=cs), report())
    assert any("bez przesłanki faktycznej" in i for i in issues)
    cs[2] = cs[2].model_copy(update={"premises": ["F1", "X9"]})
    assert any("spoza tabeli" in i for i in validate_analysis(Analysis(claims=cs), report()))


def test_claim_not_in_report_and_out_of_scope():
    cs = claims() + [Claim(claim_id="L2", text="t", type=ClaimType.law, evidence_ids=["E2"])]
    a = Analysis(claims=cs, out_of_scope_flags=["podatki"], missing_information=["data dostawy"])
    issues = validate_analysis(a, report())
    assert any("nieobjęte raportem" in i and "L2" in i for i in issues)
    assert any("poza zakresem" in i for i in issues)
    assert any("brakujące informacje" in i for i in issues)


def test_detect_out_of_scope():
    assert detect_out_of_scope("Chcę złożyć pozew i apelację") == ["pozwy", "apelacje i środki zaskarżenia"]
    assert "podatki" in detect_out_of_scope("Czy muszę zapłacić podatek PIT?")
    assert "obliczanie przedawnienia" in detect_out_of_scope("Kiedy się przedawnia roszczenie?")
    assert "prawo rodzinne" in detect_out_of_scope("sprawa o alimenty")
    assert "prawo migracyjne" in detect_out_of_scope("Potrzebuję karty pobytu")
    assert "nieruchomości" in detect_out_of_scope("kupno działki i księga wieczysta")
    assert "prawo karne" in detect_out_of_scope("zawiadomienie o przestępstwie oszustwa")
    # in-scope consumer text (real phrases from art. 27 upk) must not trigger false flags
    assert detect_out_of_scope(
        "Konsument może w terminie 14 dni odstąpić od niej bez podawania przyczyny; nieumówionej wizyty "
        "przedsiębiorcy w miejscu zamieszkania lub zwykłego pobytu konsumenta. Sklep nie pozwala na zwrot."
    ) == []


def test_reviewer_prompt_json_example_matches_client_review_format():
    text = (WF / "reviewer_prompt.md").read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)```", text, re.S).group(1)
    data = json.loads(block)
    for v in data.values():
        assert v["status"] in ("pass", "issues")
        assert v["reviewer_type"] in ("llm", "human_unverified")
        assert isinstance(v["issues"], list)


def test_instructions_cover_required_points():
    t = (WF / "instructions.md").read_text(encoding="utf-8")
    for needle in ("sources_status", "check_citations", "z pamięci", "nie może zmusić",
                   "jedna runda", "pozwów", "przedawnienia", "nieruchomości", "migracyjnego"):
        assert needle in t
    assert not re.search(r"claude-|gpt-\d", t)  # no API model identifiers
