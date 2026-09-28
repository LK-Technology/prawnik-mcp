"""Store schema migrations (0.1.0 -> current) and transaction batching."""

import sqlite3

import pytest

from prawnik_mcp.migrations import LATEST, SCHEMA_V1
from prawnik_mcp.store import Store


def _make_v01_db(path):
    db = sqlite3.connect(path / "prawnik.sqlite3")
    db.executescript(SCHEMA_V1)
    doc = ('{"document_id":"eli:DU/2014/827","kind":"statute","title":"ustawa o prawach konsumenta",'
           '"original_url":"https://api.sejm.gov.pl/eli/acts/DU/2014/827","snapshot_id":"eli:abc","sha256":"x"}')
    db.execute("INSERT INTO documents VALUES (?,?,?,?)", ("eli:DU/2014/827", "statute", "eli:abc", doc))
    db.execute("INSERT INTO fts(ref, kind, document_id, title, body) VALUES (?,?,?,?,?)",
               ("p1", "provision", "eli:DU/2014/827", "upk art. 27", "Konsument może odstąpić od umowy"))
    db.execute("INSERT INTO fts(ref, kind, document_id, title, body) VALUES (?,?,?,?,?)",
               ("saos:1", "judgment", "saos:1", "wyrok", "odstąpienie od umowy zawartej na odległość"))
    db.commit()
    db.close()


def test_migrates_v01_database_without_loss(tmp_path):
    _make_v01_db(tmp_path)
    store = Store(tmp_path)
    assert store.schema_version == LATEST
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == LATEST
    assert store.get_document("eli:DU/2014/827").title == "ustawa o prawach konsumenta"
    assert store.db.execute("SELECT source_id FROM documents").fetchone()[0] == "eli"
    rows = store.fts_search('"odst"*', None, 10, 0)
    assert {r[0] for r in rows} == {"p1", "saos:1"}
    assert [r[0] for r in store.fts_search('"odst"*', None, 10, 0, source_ids=["saos"])] == ["saos:1"]
    store.close()
    # reopening is idempotent
    assert Store(tmp_path).schema_version == LATEST


def test_batch_rolls_back_on_error(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(RuntimeError):
        with store.batch():
            store.set_sync_state("saos", "q1", cursor="3", done=False, items=30, bytes_=10)
            raise RuntimeError("boom")
    assert store.get_sync_state("saos", "q1") is None
    with store.batch():
        store.set_sync_state("saos", "q1", cursor="3", done=False, items=30, bytes_=10)
    assert store.get_sync_state("saos", "q1")["cursor"] == "3"


def test_cache_ttl(tmp_path):
    from datetime import timedelta

    store = Store(tmp_path)
    store.cache_put("k", "v", ttl=timedelta(hours=1))
    assert store.cache_get("k") == "v"
    store.cache_put("old", "v", ttl=timedelta(seconds=-1))
    assert store.cache_get("old") is None
