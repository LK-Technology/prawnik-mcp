"""Pydantic model of a document template (templates/*.json).

Templates are data, not code. They contain no legal text: `legal_sources` hold only
identifiers and locators that the client must verify with `get_legal_document`.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FieldType = Literal["str", "date", "money", "enum", "list"]
PersonalCategory = Literal["person", "address", "id_number", "account", "email", "phone"]


class Condition(BaseModel):
    """`field` equals `equals` (or, when `equals` is None, the field is present)."""

    field: str
    equals: Any = None


class FieldValidation(BaseModel):
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    options: list[str] | None = None  # enum
    currencies: list[str] | None = None  # money
    min_items: int | None = None  # list
    not_before: list[str] = []  # date: must be >= these date fields
    after: list[str] = []  # date: must be > these date fields
    not_after: list[str] = []  # date: must be <= these date fields
    not_future: bool = False  # date: must be <= today
    iban_pl: bool = False  # str: Polish bank account number (NRB/IBAN checksum)
    required_if: Condition | None = None


class FieldSpec(BaseModel):
    name: str
    label: str
    type: FieldType
    required: bool = True
    validation: FieldValidation = Field(default_factory=FieldValidation)
    personal: PersonalCategory | None = None  # used by privacy.pseudonymize
    help: str | None = None


class QualifyingQuestion(BaseModel):
    id: str
    question: str
    why_it_matters: str
    options: list[str]
    excluding_answers: list[str] = []
    exclusion_reason: str | None = None
    flag_answers: list[str] = []  # answers that do not exclude but require a check
    flag_note: str | None = None
    related_sources: list[dict[str, str]] = []  # identifiers only, to be verified

    @model_validator(mode="after")
    def _check(self) -> "QualifyingQuestion":
        for a in self.excluding_answers + self.flag_answers:
            if a not in self.options:
                raise ValueError(f"{self.id}: answer {a!r} not in options")
        if self.excluding_answers and not self.exclusion_reason:
            raise ValueError(f"{self.id}: exclusion_reason required")
        if self.flag_answers and not self.flag_note:
            raise ValueError(f"{self.id}: flag_note required")
        return self


class LegalSourceRef(BaseModel):
    document_id: str
    locator: str | None = None
    purpose: str  # why the client should look at it (no statement of its content)

    @model_validator(mode="after")
    def _only_supported(self) -> "LegalSourceRef":
        if not re.match(r"^(eli:DU/\d{4}/\d+|celex:\w+|saos:\d+)$", self.document_id):
            raise ValueError(f"unsupported document_id {self.document_id}")
        return self


class Fragment(BaseModel):
    """Inline conditional text: first variant whose condition holds is used."""

    variants: list[dict[str, Any]]  # {"when": Condition, "text": str}


class Block(BaseModel):
    type: Literal["right", "left", "heading", "paragraph", "numbered", "signature", "draft", "list_field"]
    text: str | None = None
    items: list[str] = []
    field: str | None = None  # list_field
    section: str | None = None  # draft
    title: str | None = None  # draft / list_field heading
    when: Condition | None = None


class ReviewEntry(BaseModel):
    date: str
    reviewer: str
    reviewer_type: str
    note: str


class TemplateSpec(BaseModel):
    template_id: str
    version: str
    title: str
    scope: str
    status: str
    required_facts: list[FieldSpec]
    qualifying_questions: list[QualifyingQuestion]
    exclusions: list[str]
    legal_sources: list[LegalSourceRef]
    legal_sources_note: str
    supported_dates: str
    temporal_checks: list[dict[str, str]] = []  # {"field": ..., "note": ...}
    allowed_draft_sections: list[str] = []
    fragments: dict[str, Fragment] = {}
    letter: list[Block]
    review_history: list[ReviewEntry] = []

    @model_validator(mode="after")
    def _consistency(self) -> "TemplateSpec":
        names = {f.name for f in self.required_facts}
        if len(names) != len(self.required_facts):
            raise ValueError("duplicate field names")
        ids = [q.id for q in self.qualifying_questions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate qualifying question ids")
        if "nie sprawdzony przez prawnika" not in self.status and "not reviewed by a lawyer" not in self.status:
            if not self.review_history:
                raise ValueError("status must state missing lawyer review")
        known = names | set(self.fragments)
        for f in self.required_facts:
            v = f.validation
            for ref in v.not_before + v.after + v.not_after:
                if ref not in names:
                    raise ValueError(f"{f.name}: unknown date field {ref}")
            if v.required_if and v.required_if.field not in names:
                raise ValueError(f"{f.name}: unknown required_if field")
            if f.type == "enum" and not v.options:
                raise ValueError(f"{f.name}: enum without options")
        for b in self.letter:
            for txt in [b.text or ""] + b.items:
                for ref in re.findall(r"\{(\w+)\}", txt):
                    if ref not in known:
                        raise ValueError(f"letter references unknown field {ref}")
            if b.type == "draft" and b.section not in self.allowed_draft_sections:
                raise ValueError(f"draft section {b.section} not allowed")
        for name, frag in self.fragments.items():
            for var in frag.variants:
                if "when" in var:
                    Condition.model_validate(var["when"])
                for ref in re.findall(r"\{(\w+)\}", var.get("text", "")):
                    if ref not in names:
                        raise ValueError(f"fragment {name} references unknown field {ref}")
        return self

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.required_facts if f.name == name), None)
