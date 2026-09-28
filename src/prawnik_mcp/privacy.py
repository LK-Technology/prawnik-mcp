"""Pseudonymisation of case facts before they are sent to an external model.

IMPORTANT: pseudonymisation is NOT anonymisation. The mapping stays local and the
remaining context (dates, amounts, descriptions of goods or events) may still allow a
person to be identified. The user must see what leaves the machine (`describe_outgoing`).
A local MCP server does not mean local processing: a hosted LLM still receives what the client sends.

This module never logs fact contents.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# category -> token prefix
TOKEN_PREFIX = {
    "person": "OSOBA",
    "address": "ADRES",
    "id_number": "IDENTYFIKATOR",
    "pesel": "PESEL",
    "nip": "NIP",
    "account": "RACHUNEK",
    "email": "EMAIL",
    "phone": "TELEFON",
}

# field-name heuristics for facts not described by a template (checked in order)
_KEY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"pesel", re.I), "pesel"),
    (re.compile(r"(^|_)nip($|_)|regon|krs", re.I), "nip"),
    (re.compile(r"e-?mail", re.I), "email"),
    (re.compile(r"telefon|phone|tel($|_)", re.I), "phone"),
    (re.compile(r"rachun|konto|iban|nrb|account", re.I), "account"),
    (re.compile(r"adres|address|ulica|kod_pocztowy", re.I), "address"),
    (re.compile(r"imie|imię|nazwisk|(^|_)nazwa($|_)|name|osoba|wierzyciel_nazwa|dluznik_nazwa", re.I), "person"),
]

# value patterns detected also inside free text
_VALUE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "email"),
    (re.compile(r"\b(?:PL)?\d{2}(?:[ -]?\d{4}){6}\b"), "account"),
    (re.compile(r"(?<!\d)\d{11}(?!\d)"), "pesel"),
    (re.compile(r"(?<![\d-])(?:\d{3}-\d{3}-\d{2}-\d{2}|\d{3}-\d{2}-\d{2}-\d{3}|\d{10})(?![\d-])"), "nip"),
    (re.compile(r"(?<![\d\w])(?:\+48[ -]?)?\d{3}[ -]\d{3}[ -]\d{3}(?![\d])"), "phone"),
]

_SKIP_KEYS = {"kwalifikacja"}


def _category_for_key(key: str, template_fields: dict[str, str | None]) -> str | None:
    if key in template_fields:
        return template_fields[key]
    for rx, cat in _KEY_RULES:
        if rx.search(key):
            return cat
    return None


def _template_fields(template_id: str | None) -> dict[str, str | None]:
    if not template_id:
        return {}
    from prawnik_mcp.documents.templates import load_template

    spec = load_template(template_id)
    return {f.name: f.personal for f in spec.required_facts} if spec else {}


class _Tokens:
    def __init__(self) -> None:
        self.by_value: dict[tuple[str, str], str] = {}
        self.mapping: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    def token(self, category: str, value: str) -> str:
        key = (category, value)
        if key not in self.by_value:
            prefix = TOKEN_PREFIX.get(category, "DANE")
            self.counters[prefix] = self.counters.get(prefix, 0) + 1
            tok = f"[{prefix}_{self.counters[prefix]}]"
            self.by_value[key] = tok
            self.mapping[tok] = value
        return self.by_value[key]


def _scrub_text(text: str, toks: _Tokens) -> str:
    # 1) replace values already known from personal fields (longest first)
    for (_cat, val), tok in sorted(toks.by_value.items(), key=lambda kv: -len(kv[0][1])):
        if val and len(val) >= 3:
            text = text.replace(val, tok)
    # 2) detect patterns in remaining free text
    for rx, cat in _VALUE_RULES:
        text = rx.sub(lambda m, cat=cat: toks.token(cat, m.group(0)), text)
    return text


def pseudonymize(facts: dict, template_id: str | None = None) -> tuple[dict, dict[str, str]]:
    """Replace personal data with tokens like [OSOBA_1]. Returns (pseudonymised facts, mapping).

    Personal fields are recognised from the template (`personal` attribute) and from field
    names; emails, account numbers, PESEL/NIP-like numbers and phone numbers are also
    replaced inside free-text fields. The same value always gets the same token.
    Pseudonymisation ≠ anonymity: see the module docstring."""
    tfields = _template_fields(template_id)
    toks = _Tokens()
    out = copy.deepcopy(facts)

    # pass 1: whole personal fields
    for key, val in facts.items():
        if key in _SKIP_KEYS:
            continue
        cat = _category_for_key(key, tfields)
        if cat and isinstance(val, str) and val.strip():
            out[key] = toks.token(cat, val.strip())

    # pass 2: free text in all remaining string values (recursively)
    def walk(v: Any, key: str | None = None) -> Any:
        if isinstance(v, str):
            return v if (v.startswith("[") and v in toks.mapping) else _scrub_text(v, toks)
        if isinstance(v, dict):
            return {k: (x if k in _SKIP_KEYS else walk(x, k)) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, key) for x in v]
        return v

    out = {k: (v if k in _SKIP_KEYS or (isinstance(v, str) and v in toks.mapping) else walk(v, k)) for k, v in out.items()}
    return out, dict(toks.mapping)


def restore(text: str, mapping: dict[str, str]) -> str:
    """Replace tokens back with the original values (locally, e.g. before export)."""
    for tok in sorted(mapping, key=len, reverse=True):
        text = text.replace(tok, mapping[tok])
    return text


def restore_obj(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return restore(obj, mapping)
    if isinstance(obj, dict):
        return {k: restore_obj(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [restore_obj(v, mapping) for v in obj]
    return obj


def describe_outgoing(facts: dict, template_id: str | None = None) -> list[dict[str, str]]:
    """What would be sent to an external model after pseudonymize(): one entry per field.

    Returns field names and categories only – never the values themselves."""
    tfields = _template_fields(template_id)
    pseudo, _ = pseudonymize(facts, template_id)
    out = []
    for key, val in facts.items():
        cat = None if key in _SKIP_KEYS else _category_for_key(key, tfields)
        if cat:
            how = "pseudonim (token)"
        elif pseudo.get(key) != val:
            how = "jawnie, z pseudonimizacją wykrytych danych w tekście"
        else:
            how = "jawnie"
        out.append({"field": key, "category": cat or "dane sprawy", "sent_as": how})
    out.append({
        "field": "*",
        "category": "uwaga",
        "sent_as": "Pseudonimizacja nie gwarantuje anonimowości: daty, kwoty i opisy mogą pozwolić na identyfikację osoby.",
    })
    return out
