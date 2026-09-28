"""Loading and listing document templates (package data: prawnik_mcp/templates/*.json)."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from prawnik_mcp.documents.schema import TemplateSpec

_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def templates_dir() -> Path:
    env = os.environ.get("PRAWNIK_MCP_TEMPLATES")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "templates"


@lru_cache(maxsize=32)
def _load(path: str, mtime: float) -> TemplateSpec:
    return TemplateSpec.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_template(template_id: str) -> TemplateSpec | None:
    """Validated template model, or None for an unknown/invalid id."""
    if not _ID_RE.match(template_id or ""):
        return None
    p = templates_dir() / f"{template_id}.json"
    if not p.exists():
        return None
    spec = _load(str(p), p.stat().st_mtime)
    return spec if spec.template_id == template_id else None


def get_template(template_id: str) -> dict | None:
    spec = load_template(template_id)
    if spec is None:
        return None
    d = spec.model_dump(mode="json")
    d.pop("letter", None)  # layout is internal; fields/questions/sources are the public contract
    d.pop("fragments", None)
    return d


def list_templates() -> list[dict]:
    out = []
    for p in sorted(templates_dir().glob("*.json")):
        spec = load_template(p.stem)
        if spec:
            out.append({
                "template_id": spec.template_id,
                "version": spec.version,
                "title": spec.title,
                "scope": spec.scope,
                "status": spec.status,
            })
    return out


def load_all_raw() -> dict[str, dict]:
    """Raw JSON of all templates (used by tests)."""
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(templates_dir().glob("*.json"))}
