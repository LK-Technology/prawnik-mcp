"""Regression tests for stage-3 review findings."""

import json
from datetime import date

from prawnik_mcp.contracts import ProvisionVersion, TemporalStatus
from prawnik_mcp.documents.render import _FIELD_RE
from prawnik_mcp.documents.templates import list_templates, load_template
from prawnik_mcp.evidence.temporal import temporal_status_for


def test_templates_reference_only_declared_fields():
    for t in list_templates():
        spec = load_template(t["template_id"])
        body = json.dumps(spec.model_dump(mode="json").get("letter"), ensure_ascii=False)
        for name in set(_FIELD_RE.findall(body)):
            name = name if isinstance(name, str) else name[0]
            assert spec.field(name) is not None or name in spec.fragments, (t["template_id"], name)


def test_original_publication_never_accepted_for_dated_event():
    p = ProvisionVersion(provision_id="x", document_id="celex:X", locator="art. 1", text="t",
                         version_id="celex:X:oj", version_label="oj", text_state_date=date(2011, 11, 22),
                         temporal_basis=TemporalStatus.original_publication, snapshot_id="s")
    for d in (date(2010, 1, 1), date(2011, 11, 22), date(2020, 1, 1)):
        assert temporal_status_for(p, d, today=date(2026, 9, 26))[0] == TemporalStatus.unknown
