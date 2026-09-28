"""Schema test for evals/cases and a smoke run of the offline runner (no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_offline", ROOT / "evals" / "run_offline.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

CASES = runner.load_cases()


def test_at_least_20_dev_cases_unique_ids():
    assert len(CASES) >= 20
    ids = [c["case_id"] for c in CASES]
    assert len(ids) == len(set(ids))
    assert all(c["split"] == "dev" for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_case_schema(case):
    assert runner.validate_case(case) == []


def test_all_mandatory_traps_covered():
    covered = {t for c in CASES for t in c["traps"]}
    assert covered == runner.TRAPS


def test_areas_cover_supported_domain():
    assert {c["area"] for c in CASES} == {"payments", "consumer_purchase", "distance_withdrawal", "complaint"}


def test_wrong_court_case_uses_saos_fixture():
    import json

    saos = json.loads((ROOT / "tests" / "fixtures" / "raw" / "saos_31345.json").read_text(encoding="utf-8"))["data"]
    assert saos["courtCases"][0]["caseNumber"] == "I ACa 772/13"
    assert saos["division"]["court"]["name"] == "Sąd Apelacyjny w Łodzi"
    case = next(c for c in CASES if "wrong_court" in c["traps"])
    assert "I ACa 772/13" in case["question"] and "Krakowie" in case["question"]
    assert "Sąd Apelacyjny w Łodzi" in case["offline_checks"][0]["expect"]["hit_text_contains"]


def test_validate_case_rejects_bad_case():
    bad = dict(CASES[0])
    bad["gold_status"] = "validated"
    bad["expected"] = {**bad["expected"], "sources": [{"document_id": "eli:DU/2099/1", "locator": "art. 1"}]}
    assert len(runner.validate_case(bad)) >= 2


def test_runner_offline_smoke():
    report = runner.run(CASES, data_dir=None)
    assert report["cases"] == len(CASES)
    results = {r["result"] for r in report["checks"]}
    assert results <= {"pass", "fail", "skipped", "not_available", "not_run"}
    renders = [r for r in report["checks"] if r["kind"] == "render"]
    assert renders and all(r["result"] == "pass" for r in renders)
    assert all(v.startswith("not measured") for v in report["legal_quality_metrics"].values())
    assert not [r for r in report["checks"] if r["kind"] == "schema"]
