"""Rendering of document drafts from templates, with server-side export gates.

Gates (enforced here, not by the client):
- facts are validated programmatically (types, required, money, date order);
- qualifying answers that exclude the template -> `out_of_scope`;
- missing facts -> only an explicitly unfilled form with "[UZUPEŁNIJ: …]" placeholders;
- a filled draft needs a CitationReport bound to exactly these facts/draft/template version,
  without critical errors, with every claim verified and every snapshot still present.

Nothing here computes deadlines or interest, and no fact contents are logged.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from prawnik_mcp.contracts import (
    ClaimType,
    CitationReport,
    CitationStatus,
    ResultStatus,
    TemporalStatus,
    ToolResult,
)
from prawnik_mcp.documents import money
from prawnik_mcp.documents.schema import Condition, FieldSpec, TemplateSpec
from prawnik_mcp.documents.templates import load_template

DOCUMENT_COMPLETE = "kompletny projekt"
DOCUMENT_INCOMPLETE = "niekompletny formularz"
STATUS_LINE = "projekt eksperymentalny – nie jest poradą prawną; nie sprawdzony przez prawnika"
SIGNATURE_LINE = "……………… (podpis)"
QUALIFYING_KEY = "kwalifikacja"
VERIFIED = {CitationStatus.verified_exact, CitationStatus.verified_normalized}
FORBIDDEN_PHRASES = ("prawnie bezbłędn",)
_FIELD_RE = re.compile(r"\{(\w+)\}")


def placeholder(label: str) -> str:
    return f"[UZUPEŁNIJ: {label}]"


# --------------------------------------------------------------------------- binding


def _canon(v: Any) -> Any:
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, dict):
        return {unicodedata.normalize("NFC", str(k)): _canon(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, (Decimal, date)):
        return str(v)
    return v


def binding_hash(template_id: str, template_version: str, facts: dict, draft: dict | None) -> str:
    """Stable sha256 over canonical JSON of (template id, version, facts, draft).

    Any change of facts, draft text or template version gives a different hash, which
    invalidates a CitationReport bound to the previous one. `draft=None` equals `{}`."""
    payload = {
        "template_id": template_id,
        "template_version": template_version,
        "facts": _canon(facts or {}),
        "draft": _canon(draft or {}),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- validation


def _present(v: Any) -> bool:
    return v is not None and v != "" and v != [] and v != {}


@dataclass
class Validated:
    values: dict[str, Any] = field(default_factory=dict)  # parsed values of present fields
    errors: list[dict[str, str]] = field(default_factory=list)
    missing: list[FieldSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _cond_holds(cond: Condition, facts: dict) -> bool | None:
    """True/False, or None when the condition field is absent."""
    v = facts.get(cond.field)
    if not _present(v):
        return None
    return True if cond.equals is None else v == cond.equals


def is_required(f: FieldSpec, facts: dict) -> bool:
    if f.required:
        return True
    rif = f.validation.required_if
    return bool(rif and _cond_holds(rif, facts))


def _parse_field(f: FieldSpec, raw: Any, errors: list[dict[str, str]]) -> Any:
    def err(msg: str) -> None:
        errors.append({"field": f.name, "message": msg})

    v = f.validation
    if f.type in ("str", "enum"):
        if not isinstance(raw, str):
            return err("oczekiwano tekstu")
        s = unicodedata.normalize("NFC", raw).strip()
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", s):
            return err("niedozwolone znaki sterujące")
        if "[UZUPEŁNIJ" in s.upper():
            return err("pole zawiera znacznik do uzupełnienia – podaj rzeczywistą wartość albo pozostaw puste")
        if f.type == "enum":
            if s not in (v.options or []):
                return err(f"dozwolone wartości: {', '.join(v.options or [])}")
            return s
        if v.min_length and len(s) < v.min_length:
            return err(f"za krótkie (min. {v.min_length} znaków)")
        if v.max_length and len(s) > v.max_length:
            return err(f"za długie (maks. {v.max_length} znaków)")
        if v.pattern and not re.fullmatch(v.pattern, s):
            return err("niepoprawny format")
        if v.iban_pl and not money.valid_pl_account(s):
            return err("niepoprawny numer rachunku (26 cyfr NRB / IBAN PL, błędna suma kontrolna)")
        return s
    if f.type == "date":
        if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw.strip()):
            return err("oczekiwano daty w formacie RRRR-MM-DD")
        try:
            return date.fromisoformat(raw.strip())
        except ValueError:
            return err("nieistniejąca data")
    if f.type == "money":
        if not isinstance(raw, dict) or "amount" not in raw or "currency" not in raw:
            return err('oczekiwano obiektu {"amount": "123.45", "currency": "PLN"}')
        amount = money.parse_amount(raw["amount"])
        if amount is None:
            return err("kwota nie jest liczbą")
        if amount <= 0:
            return err("kwota musi być większa od zera")
        if money.decimal_places(amount) > 2:
            return err("kwota może mieć najwyżej 2 miejsca po przecinku")
        if amount >= Decimal(10) ** 12:
            return err("kwota poza obsługiwanym zakresem")
        cur = raw["currency"]
        allowed = v.currencies or list(money.CURRENCY_FORMS)
        if cur not in allowed:
            return err(f"dozwolone waluty: {', '.join(allowed)}")
        return (amount, cur)
    if f.type == "list":
        if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
            return err("oczekiwano listy niepustych tekstów")
        if v.min_items and len(raw) < v.min_items:
            return err(f"lista musi mieć co najmniej {v.min_items} elementy")
        return [unicodedata.normalize("NFC", x).strip() for x in raw]
    return err("nieobsługiwany typ pola")


def validate_facts(spec: TemplateSpec, facts: dict, today: date) -> Validated:
    out = Validated()
    known = {f.name for f in spec.required_facts} | {QUALIFYING_KEY}
    unknown = sorted(k for k in facts if k not in known)
    if unknown:
        out.warnings.append(f"Pominięto nieznane pola: {', '.join(unknown)}.")
    for f in spec.required_facts:
        raw = facts.get(f.name)
        if not _present(raw):
            if is_required(f, facts):
                out.missing.append(f)
            continue
        val = _parse_field(f, raw, out.errors)
        if val is not None:
            out.values[f.name] = val
    # cross-field checks
    for f in spec.required_facts:
        if f.type != "date" or f.name not in out.values:
            continue
        d, v = out.values[f.name], f.validation
        if v.not_future and d > today:
            out.errors.append({"field": f.name, "message": "data z przyszłości"})
        for ref in v.not_before:
            o = out.values.get(ref)
            if isinstance(o, date) and d < o:
                out.errors.append({"field": f.name, "message": f"data wcześniejsza niż pole {ref}"})
        for ref in v.after:
            o = out.values.get(ref)
            if isinstance(o, date) and d <= o:
                out.errors.append({"field": f.name, "message": f"data musi być późniejsza niż pole {ref}"})
        for ref in v.not_after:
            o = out.values.get(ref)
            if isinstance(o, date) and d > o:
                out.errors.append({"field": f.name, "message": f"data późniejsza niż pole {ref}"})
    for f in spec.required_facts:  # "<money>_slownie" must match "<money>"
        if f.name.endswith("_slownie") and f.name in out.values:
            base = out.values.get(f.name[: -len("_slownie")])
            if isinstance(base, tuple) and not money.words_match(out.values[f.name], *base):
                out.errors.append({
                    "field": f.name,
                    "message": f"kwota słownie nie odpowiada kwocie cyframi (oczekiwano np.: {money.amount_in_words(*base)})",
                })
    return out


@dataclass
class Qualification:
    answers: dict[str, str]
    errors: list[dict[str, str]]
    exclusions: list[str]
    flags: list[str]
    unanswered: list[str]


def qualify(spec: TemplateSpec, facts: dict) -> Qualification:
    raw = facts.get(QUALIFYING_KEY) or {}
    q = Qualification({}, [], [], [], [])
    if not isinstance(raw, dict):
        q.errors.append({"field": QUALIFYING_KEY, "message": "oczekiwano obiektu {id_pytania: odpowiedź}"})
        return q
    ids = {x.id for x in spec.qualifying_questions}
    for k in raw:
        if k not in ids:
            q.errors.append({"field": f"{QUALIFYING_KEY}.{k}", "message": "nieznane pytanie kwalifikujące"})
    for question in spec.qualifying_questions:
        a = raw.get(question.id)
        if not _present(a):
            q.unanswered.append(question.id)
            continue
        if a not in question.options:
            q.errors.append({"field": f"{QUALIFYING_KEY}.{question.id}",
                             "message": f"dozwolone odpowiedzi: {', '.join(question.options)}"})
            continue
        q.answers[question.id] = a
        if a in question.excluding_answers:
            q.exclusions.append(question.exclusion_reason or question.id)
        elif a in question.flag_answers:
            q.flags.append(question.flag_note or question.id)
    return q


# --------------------------------------------------------------------------- report gate


def check_report(store, report_id: str | None, expected_binding: str, draft: dict) -> tuple[CitationReport | None, list[str]]:
    """Returns (report, blocking reasons)."""
    if not report_id:
        return None, ["Brak report_id: wypełniony projekt wymaga raportu z check_citations."]
    raw = store.load_report_json(report_id)
    if raw is None:
        return None, [f"Nie znaleziono raportu {report_id} w lokalnym magazynie."]
    try:
        rep = CitationReport.model_validate_json(raw)
    except ValidationError:
        return None, ["Raport kontroli cytatów jest nieczytelny (niezgodny z kontraktem)."]
    reasons: list[str] = []
    if rep.report_id != report_id:
        reasons.append("Identyfikator w treści raportu nie zgadza się z report_id.")
    if not rep.binding_hash:
        reasons.append("Raport nie jest powiązany z faktami i treścią pisma (brak binding_hash).")
    elif rep.binding_hash != expected_binding:
        reasons.append("Raport jest nieaktualny: fakty, treść pisma lub wersja szablonu zmieniły się po kontroli (binding_hash się nie zgadza).")
    if rep.critical_errors:
        reasons.append(f"Raport zawiera błędy krytyczne ({len(rep.critical_errors)}).")
    # user-supplied fact claims without evidence are allowed; they are listed in the report as unverified
    bad = [c.claim_id for c in rep.claims if c.citation_status not in VERIFIED
           and not (c.claim_type == ClaimType.fact and c.citation_status == CitationStatus.unmapped)]
    if bad:
        reasons.append(f"Niezweryfikowane cytaty w twierdzeniach: {', '.join(bad)}.")
    missing = [s for s in rep.snapshot_ids if store.get_snapshot(s) is None]
    if missing:
        reasons.append(f"Snapshoty źródeł, na których oparto raport, nie są już dostępne: {', '.join(missing)}.")
    if any((t or "").strip() for t in draft.values()) and not rep.claims:
        reasons.append("Pismo zawiera tekst redakcyjny, ale raport nie zawiera żadnych sprawdzonych twierdzeń.")
    return rep, reasons


# --------------------------------------------------------------------------- composition


@dataclass
class RBlock:
    kind: str  # right, left, heading, paragraph, numbered, signature, subheading
    lines: list[str]


def _display(f: FieldSpec, v: Any) -> str:
    if f.type == "date":
        return v.strftime("%d.%m.%Y") + " r."
    if f.type == "money":
        return money.format_amount(*v)
    if f.type == "list":
        return ", ".join(v)
    if f.validation.iban_pl:
        return money.format_account(v)
    return str(v)


class _Composer:
    def __init__(self, spec: TemplateSpec, facts: dict, val: Validated, draft: dict, complete: bool):
        self.spec, self.facts, self.val, self.draft, self.complete = spec, facts, val, draft, complete
        self.placeholders: list[str] = []

    def _ph(self, f: FieldSpec) -> str:
        if f.label not in self.placeholders:
            self.placeholders.append(f.label)
        return placeholder(f.label)

    def _cond(self, cond: Condition) -> bool | str:
        """True/False, or a placeholder string when a needed field is missing."""
        f = self.spec.field(cond.field)
        held = _cond_holds(cond, self.facts)
        if held is None:
            return self._ph(f) if f and is_required(f, self.facts) else False
        return held

    def value(self, name: str) -> str:
        if name in self.spec.fragments:
            for var in self.spec.fragments[name].variants:
                if "when" not in var:
                    return self.fill(var.get("text", ""))
                c = self._cond(Condition.model_validate(var["when"]))
                if isinstance(c, str):
                    return c
                if c:
                    return self.fill(var.get("text", ""))
            return ""
        f = self.spec.field(name)
        if f is None:  # template references an undeclared field: never invent a value
            return f"[UZUPEŁNIJ: {name}]"
        if name in self.val.values:
            return _display(f, self.val.values[name])
        return self._ph(f)

    def fill(self, text: str) -> str:
        filled = _FIELD_RE.sub(lambda m: self.value(m.group(1)), text)
        return re.sub(r"\b r\.\.", " r.", filled)  # "…2026 r." + sentence dot

    def blocks(self) -> list[RBlock]:
        out: list[RBlock] = []
        for b in self.spec.letter:
            if b.when is not None:
                c = self._cond(b.when)
                if isinstance(c, str):
                    out.append(RBlock("paragraph", [c]))
                    continue
                if not c:
                    continue
            if b.type in ("right", "left", "paragraph", "heading"):
                text = self.fill(b.text or "")
                if text.strip():
                    out.append(RBlock(b.type, text.split("\n")))
            elif b.type == "numbered":
                out.append(RBlock("numbered", [self.fill(i) for i in b.items]))
            elif b.type == "list_field":
                items = self.val.values.get(b.field)
                if items:
                    out.append(RBlock("subheading", [b.title or ""]))
                    out.append(RBlock("numbered", list(items)))
            elif b.type == "draft":
                text = (self.draft.get(b.section) or "").strip()
                if not text:
                    continue
                out.append(RBlock("subheading", [b.title or b.section]))
                if not self.complete:
                    label = f"{b.title or b.section} – tekst zostanie wstawiony po uzupełnieniu danych i kontroli źródeł"
                    self.placeholders.append(label)
                    out.append(RBlock("paragraph", [placeholder(label)]))
                    continue
                for para in re.split(r"\n\s*\n", unicodedata.normalize("NFC", text)):
                    out.append(RBlock("paragraph", para.split("\n")))
            elif b.type == "signature":
                out.append(RBlock("signature", [SIGNATURE_LINE]))
        return out


# --------------------------------------------------------------------------- main entry


def render_document(
    store,
    template_id: str,
    facts: dict,
    draft: dict | None,
    report_id: str | None,
    out_dir: Path,
    *,
    today: date | None = None,
    example: bool = False,
    file_stem: str | None = None,
) -> ToolResult:
    """Validate, gate and export a letter (MD + DOCX) and a separate sources/notes report.

    `example=True` marks the user report as containing fictional example data."""
    from prawnik_mcp.documents import export  # local import keeps python-docx optional for listing

    today = today or date.today()
    spec = load_template(template_id)
    if spec is None:
        return ToolResult(status=ResultStatus.invalid_input, data={"errors": [{"field": "template_id", "message": "nieznany szablon"}]})
    if not isinstance(facts, dict):
        return ToolResult(status=ResultStatus.invalid_input, data={"errors": [{"field": "facts", "message": "oczekiwano obiektu"}]})
    draft = draft or {}
    if not isinstance(draft, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in draft.items()):
        return ToolResult(status=ResultStatus.invalid_input, data={"errors": [{"field": "draft", "message": "oczekiwano obiektu {sekcja: tekst}"}]})
    draft_errors = [
        {"field": f"draft.{k}", "message": f"sekcja niedozwolona w tym szablonie (dozwolone: {', '.join(spec.allowed_draft_sections) or 'brak'})"}
        for k in draft if k not in spec.allowed_draft_sections
    ]
    for k, v in draft.items():
        if any(p in v.lower() for p in FORBIDDEN_PHRASES):
            draft_errors.append({"field": f"draft.{k}", "message": "niedozwolone określenie gwarantujące poprawność prawną"})
    bhash = binding_hash(spec.template_id, spec.version, facts, draft)
    base = {"template_id": spec.template_id, "template_version": spec.version, "binding_hash": bhash}

    q = qualify(spec, facts)
    if q.errors or draft_errors:
        return ToolResult(status=ResultStatus.invalid_input, data={**base, "errors": q.errors + draft_errors})
    if q.exclusions:
        return ToolResult(status=ResultStatus.out_of_scope, data={**base, "reasons": q.exclusions},
                          warnings=["Szablon nie ma zastosowania na podstawie odpowiedzi na pytania kwalifikujące."])

    val = validate_facts(spec, facts, today)
    if val.errors:
        return ToolResult(status=ResultStatus.invalid_input, data={**base, "errors": val.errors}, warnings=val.warnings)

    warnings = list(val.warnings) + q.flags
    complete = not val.missing and not q.unanswered
    report = None
    if complete:
        report, reasons = check_report(store, report_id, bhash, draft)
        if reasons:
            return ToolResult(status=ResultStatus.blocked, data={**base, "report_id": report_id, "reasons": reasons},
                              warnings=warnings + ["Eksport wypełnionego projektu zablokowany."])
        for c in report.claims:
            if c.temporal_status == TemporalStatus.original_publication:
                warnings.append(f"Twierdzenie {c.claim_id}: tekst w brzmieniu pierwotnym, bez potwierdzenia obowiązywania na datę sprawy.")
            if c.temporal_status == TemporalStatus.unknown:
                warnings.append(f"Twierdzenie {c.claim_id}: nie ustalono brzmienia przepisu dla daty sprawy (temporal_status=unknown).")
    elif report_id:
        warnings.append("report_id pominięto: formularz jest niekompletny, raport zostanie wymagany po uzupełnieniu danych.")

    comp = _Composer(spec, facts, val, draft, complete)
    blocks = comp.blocks()
    doc_status = DOCUMENT_COMPLETE if complete else DOCUMENT_INCOMPLETE
    unanswered_q = [x for x in spec.qualifying_questions if x.id in q.unanswered]

    ctx = export.ReportContext(
        spec=spec, document_status=doc_status, binding_hash=bhash, values=val.values,
        answers=q.answers, unanswered=unanswered_q, missing=[f.label for f in val.missing],
        flags=q.flags, warnings=warnings, report=report, report_id=report_id if complete else None,
        example=example, generated_at=datetime.now(UTC),
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = file_stem or f"{spec.template_id}-{bhash[:10]}"
    if not re.fullmatch(r"[\w.-]{1,120}", stem):
        return ToolResult(status=ResultStatus.invalid_input, data={"errors": [{"field": "file_stem", "message": "niedozwolona nazwa pliku"}]})
    files = {
        "letter_md": out_dir / f"{stem}-pismo.md",
        "letter_docx": out_dir / f"{stem}-pismo.docx",
        "report_md": out_dir / f"{stem}-raport.md",
        "report_docx": out_dir / f"{stem}-raport.docx",
    }
    files["letter_md"].write_text(export.letter_markdown(blocks), encoding="utf-8")
    export.letter_docx(blocks, spec.title, files["letter_docx"])
    files["report_md"].write_text(export.report_markdown(ctx), encoding="utf-8")
    export.report_docx(ctx, files["report_docx"])

    return ToolResult(
        status=ResultStatus.ok,
        data={
            **base,
            "document_status": doc_status,
            "status_note": STATUS_LINE,
            "report_id": report_id if complete else None,
            "missing_fields": [f.label for f in val.missing],
            "placeholders": comp.placeholders,
            "unanswered_questions": [x.id for x in unanswered_q],
            "flags": q.flags,
            "files": {k: str(v) for k, v in files.items()},
        },
        warnings=warnings,
    )
