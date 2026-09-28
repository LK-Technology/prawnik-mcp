"""Temporal rules. Provision text is irrelevant to these rules; a short real excerpt of
art. 27 ust. 1 upk (TJ Dz.U. 2026 poz. 1244) is used as the text.

Amendment id DU/2025/1172 is real (named in the obwieszczenie). The date 2027-03-01 is the date
given in its art. 16 pkt 1 for one part of that act; its per-article applicability is not verified
here – it is a test input for the rule, not a legal finding.
"""

from datetime import date

from prawnik_mcp.contracts import ProvisionVersion, TemporalStatus
from prawnik_mcp.evidence.temporal import temporal_status_for

TODAY = date(2026, 9, 26)
TEXT = "Konsument, który zawarł umowę na odległość lub poza lokalem przedsiębiorstwa, może w terminie 14 dni"
AMEND = [{"id": "DU/2025/1172", "date": "2027-03-01"}]


def prov(**kw) -> ProvisionVersion:
    base = dict(
        provision_id="eli:DU/2014/827#art. 27@eli:DU/2026/1244", document_id="eli:DU/2014/827",
        locator="art. 27", text=TEXT, version_id="eli:DU/2026/1244",
        version_label="tekst jednolity Dz.U. 2026 poz. 1244", text_state_date=date(2026, 9, 2),
        temporal_basis=TemporalStatus.consolidated_text, snapshot_id="eli:x",
    )
    base.update(kw)
    return ProvisionVersion(**base)


def st(p, d, **kw):
    kw.setdefault("today", TODAY)
    return temporal_status_for(p, d, **kw)


def test_no_date_not_requested():
    assert st(prov(), None) == (TemporalStatus.not_requested, [])


def test_after_state_date_consolidated():
    s, reasons = st(prov(), date(2026, 9, 10))
    assert s == TemporalStatus.consolidated_text and reasons == []


def test_before_state_date_unknown():
    s, reasons = st(prov(), date(2025, 6, 1))
    assert s == TemporalStatus.unknown
    assert any("tekst jednolity odzwierciedla stan na 2026-09-02" in r for r in reasons)


def test_future_date_unknown():
    s, reasons = st(prov(), date(2027, 1, 1))
    assert s == TemporalStatus.unknown
    assert any("data przyszła" in r for r in reasons)


def test_pending_change_not_yet_in_force_unknown():
    p = prov(pending_changes=["DU/2025/1172"])
    s, reasons = st(p, date(2026, 9, 10), amendments=AMEND)
    assert s == TemporalStatus.unknown
    assert any("2027-03-01" in r for r in reasons)
    # pending change without a known date is unknown too
    s, _ = st(p, date(2026, 9, 10))
    assert s == TemporalStatus.unknown
    # once the change is in force (today moved for the test) the TJ wording can be used
    s, _ = st(p, date(2027, 3, 2), amendments=AMEND, today=date(2027, 4, 1))
    assert s == TemporalStatus.consolidated_text


def test_excluded_transitional_provisions():
    p = prov(excluded_provisions=["art. 13 ustawy Dz.U. 2025 poz. 1172"])
    amend = [{"id": "DU/2025/1172", "date": "2026-03-01"}]
    s, reasons = st(p, date(2026, 9, 10), amendments=amend)
    assert s == TemporalStatus.consolidated_text
    assert any("przepisy przejściowe" in r for r in reasons)
    # no amendment dates known -> cannot rule out transitional rules
    s, _ = st(p, date(2026, 9, 10))
    assert s == TemporalStatus.unknown


def test_curated_interval_required_for_confirmed():
    # valid_from alone on a non-curated text is NOT applicability
    s, _ = st(prov(valid_from=date(2014, 12, 25)), date(2026, 9, 10))
    assert s == TemporalStatus.consolidated_text
    cur = prov(temporal_basis=TemporalStatus.confirmed, valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31))
    assert st(cur, date(2026, 5, 1))[0] == TemporalStatus.confirmed
    assert st(cur, date(2025, 5, 1))[0] == TemporalStatus.unknown
    open_ended = prov(temporal_basis=TemporalStatus.confirmed, valid_from=date(2026, 1, 1), text_state_date=None)
    assert st(open_ended, date(2026, 5, 1))[0] == TemporalStatus.unknown


def test_original_publication_after_publication_unknown():
    p = prov(temporal_basis=TemporalStatus.original_publication, text_state_date=date(2011, 11, 22))
    assert st(p, date(2026, 1, 1))[0] == TemporalStatus.unknown
