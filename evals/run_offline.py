"""Offline eval runner (skeleton).

Checks only the machine-checkable parts of the cases (status codes, citation statuses,
explicit flags) by calling prawnik_mcp.service functions and documents.render_document.
Service functions are imported lazily; when missing they are reported as "not_available".
Legal-quality metrics are NOT measured here (they require a model run and a lawyer).

Usage:
    .venv/bin/python evals/run_offline.py [--data-dir DIR] [--json OUT.json] [--cases DIR]
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).parent / "cases"
REQUIRED_KEYS = {"case_id", "split", "question", "facts", "expected", "traps", "gold_status"}
EXPECTED_KEYS = {"sources", "must_ask", "expected_status", "refusal_reason"}
EXPECTED_STATUSES = {"ok", "out_of_scope", "temporal_unknown", "ask"}
TRAPS = {
    "fictional_case_number", "wrong_court", "irrelevant_real_quote", "amended_provision",
    "transitional_provision", "future_date", "conflicting_case_law", "party_statement_as_court",
    "missing_facts", "consumer_exclusion", "source_unavailable", "empty_database", "diacritics_ocr",
    "prompt_injection",
}
CHECK_KINDS = {"search", "get_document", "check_citations", "sources_status", "render", "fault_injection"}
ALLOWED_SOURCE_DOCS = {"eli:DU/1964/93", "eli:DU/2014/827", "celex:32011L0083"}
GOLD_STATUS = "candidate — AI-generated, not validated by a lawyer"
NOT_MEASURED = "not measured (requires model + lawyer)"
LEGAL_QUALITY_METRICS = [
    "recall@10 of relevant sources",
    "support of claims by cited sources (question 3)",
    "applicability to facts, dates and exceptions (question 4)",
    "useful answer rate on supported cases",
    "critical legal errors",
]


def load_cases(cases_dir: Path = CASES_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(cases_dir.glob("*.json"))]


def validate_case(c: dict) -> list[str]:
    """Schema problems of one case (empty list = valid)."""
    errs = [f"missing key {k}" for k in REQUIRED_KEYS - set(c)]
    if errs:
        return errs
    exp = c["expected"]
    errs += [f"expected.{k} missing" for k in EXPECTED_KEYS - set(exp)]
    if exp.get("expected_status") not in EXPECTED_STATUSES:
        errs.append(f"bad expected_status {exp.get('expected_status')}")
    for s in exp.get("sources", []):
        if s.get("document_id") not in ALLOWED_SOURCE_DOCS or not s.get("locator"):
            errs.append(f"bad source {s}")
    if c["split"] not in {"dev", "held_out"}:
        errs.append("bad split")
    if c["gold_status"] != GOLD_STATUS:
        errs.append("gold_status must mark the case as an unvalidated candidate")
    for t in c["traps"]:
        if t not in TRAPS:
            errs.append(f"unknown trap {t}")
    if exp.get("expected_status") in {"out_of_scope"} and not exp.get("refusal_reason"):
        errs.append("out_of_scope requires refusal_reason")
    for chk in c.get("offline_checks", []):
        if chk.get("kind") not in CHECK_KINDS:
            errs.append(f"unknown check kind {chk.get('kind')}")
        if chk.get("store") not in {"corpus", "empty"}:
            errs.append("check.store must be corpus|empty")
        if not isinstance(chk.get("expect"), dict):
            errs.append("check.expect missing")
    return errs


# --------------------------------------------------------------------------- execution


def _service():
    try:
        return importlib.import_module("prawnik_mcp.service")
    except ImportError:
        return None


def _status(res: Any) -> str | None:
    st = getattr(res, "status", None)
    if st is None and isinstance(res, dict):
        st = res.get("status")
    return getattr(st, "value", st)


def _dump(res: Any) -> Any:
    if hasattr(res, "model_dump"):
        return res.model_dump(mode="json")
    return res


def _evaluate(res: Any, expect: dict) -> tuple[bool, str]:
    status = _status(res)
    dumped = _dump(res)
    blob = json.dumps(dumped, ensure_ascii=False, default=str)
    data = dumped.get("data") if isinstance(dumped, dict) else None
    problems = []
    if "status_in" in expect and status not in expect["status_in"]:
        problems.append(f"status {status} not in {expect['status_in']}")
    if "temporal_unknown_or_status_in" in expect:
        ok = status == "temporal_unknown" or status in expect["temporal_unknown_or_status_in"] or (
            status == "ok" and '"temporal_status": "unknown"' in blob)
        if not ok:
            problems.append(f"expected temporal_unknown, got status {status}")
    for needle in expect.get("hit_text_contains", []):
        if needle not in blob:
            problems.append(f"{needle!r} not in result")
    if "document_status" in expect and (data or {}).get("document_status") != expect["document_status"]:
        problems.append(f"document_status {(data or {}).get('document_status')} != {expect['document_status']}")
    if "claim_status_in" in expect:
        claims = (data or {}).get("claims", []) if isinstance(data, dict) else []
        by_id = {c.get("claim_id"): c.get("citation_status") for c in claims}
        for cid, allowed in expect["claim_status_in"].items():
            if by_id.get(cid) not in allowed:
                problems.append(f"claim {cid}: {by_id.get(cid)} not in {allowed}")
    return (not problems), "; ".join(problems) or f"status={status}"


def run_check(chk: dict, stores: dict) -> dict:
    kind, args, expect = chk["kind"], dict(chk.get("args", {})), chk["expect"]
    if kind == "fault_injection":
        return {"result": "not_run", "detail": "requires network fault injection (not available offline)"}
    store = stores.get(chk["store"])
    if chk["store"] == "corpus" and (store is None or not store.stats().get("documents")):
        return {"result": "skipped", "detail": "local corpus not loaded (run sync first)"}
    try:
        if kind == "render":
            from prawnik_mcp.documents.render import render_document

            with tempfile.TemporaryDirectory() as out:
                res = render_document(store, args["template_id"], args.get("facts", {}), args.get("draft"),
                                      args.get("report_id"), Path(out))
                ok, detail = _evaluate(res, expect)
        else:
            svc = _service()
            fn_name = {"search": "search_legal", "get_document": "get_legal_document",
                       "check_citations": "check_citations_tool", "sources_status": "sources_status"}[kind]
            fn = getattr(svc, fn_name, None) if svc else None
            if fn is None:
                return {"result": "not_available", "detail": f"prawnik_mcp.service.{fn_name} not available"}
            res = fn(store, **args)
            ok, detail = _evaluate(res, expect)
    except Exception as e:  # noqa: BLE001 - an exception is a failed check, reported by type only
        return {"result": "fail", "detail": f"exception {type(e).__name__}"}
    return {"result": "pass" if ok else "fail", "detail": detail}


def run(cases: list[dict], data_dir: Path | None) -> dict:
    from prawnik_mcp.store import Store

    results = []
    with tempfile.TemporaryDirectory() as empty_dir:
        stores = {"empty": Store(Path(empty_dir))}
        try:
            stores["corpus"] = Store(data_dir) if data_dir and Path(data_dir).exists() else None
        except Exception:  # noqa: BLE001
            stores["corpus"] = None
        try:
            for c in cases:
                schema = validate_case(c)
                for i, chk in enumerate(c.get("offline_checks", [])):
                    r = run_check(chk, stores)
                    results.append({"case_id": c["case_id"], "check": i, "kind": chk["kind"],
                                    "traps": c["traps"], **r})
                if schema:
                    results.append({"case_id": c["case_id"], "check": "schema", "kind": "schema",
                                    "traps": c.get("traps", []), "result": "fail", "detail": "; ".join(schema)})
        finally:
            for s in stores.values():
                if s is not None:
                    s.close()
    trap_results: dict[str, Counter] = {}
    for r in results:
        for t in r["traps"]:
            trap_results.setdefault(t, Counter())[r["result"]] += 1
    return {
        "cases": len(cases),
        "checks": results,
        "summary": dict(Counter(r["result"] for r in results)),
        "per_trap": {t: dict(c) for t, c in sorted(trap_results.items())},
        "traps_without_machine_check": sorted(TRAPS - set(trap_results)),
        "legal_quality_metrics": {m: NOT_MEASURED for m in LEGAL_QUALITY_METRICS},
        "gold_status": GOLD_STATUS,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--cases", type=Path, default=CASES_DIR)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    report = run(load_cases(a.cases), a.data_dir)
    for r in report["checks"]:
        print(f"{r['case_id']:8} {str(r['check']):6} {r['kind']:16} {r['result']:14} {r['detail']}")
    print("summary:", report["summary"])
    print("per trap:", json.dumps(report["per_trap"], ensure_ascii=False))
    print("traps without machine check:", ", ".join(report["traps_without_machine_check"]) or "-")
    for m, v in report["legal_quality_metrics"].items():
        print(f"{m}: {v}")
    if a.json:
        a.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
