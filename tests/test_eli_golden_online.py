"""Golden quality checks of the ELI parser on a varied set of real acts (online; nightly CI).

Invariants, not exact text: every act yields articles, numbering is mostly increasing, no article is
empty, superscript restoration warnings stay bounded. Run: PRAWNIK_ONLINE=1 pytest -m online -k golden
"""

import os
import re

import pytest

from prawnik_mcp.connectors.eli import sync_act
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.store import Store

GOLDEN = {
    "DU/1964/93": "Kodeks cywilny", "DU/1964/296": "Kodeks postępowania cywilnego", "DU/1997/553": "Kodeks karny",
    "DU/1997/555": "Kodeks postępowania karnego", "DU/1974/141": "Kodeks pracy", "DU/1997/926": "Ordynacja podatkowa",
    "DU/2004/535": "ustawa o VAT", "DU/2018/646": "Prawo przedsiębiorców", "DU/2018/1000": "ustawa o ochronie danych osobowych",
    "DU/2014/827": "ustawa o prawach konsumenta", "DU/1994/414": "Prawo budowlane", "DU/1964/59": "Kodeks rodzinny i opiekuńczy",
    "DU/2000/1037": "Kodeks spółek handlowych", "DU/2007/331": "ustawa o ochronie konkurencji i konsumentów",
    "DU/1994/83": "prawo autorskie", "DU/1971/114": "Kodeks wykroczeń", "DU/1999/930": "Kodeks karny skarbowy",
    "DU/1997/939": "Prawo bankowe", "DU/1960/168": "Kodeks postępowania administracyjnego",
    "DU/2002/1270": "Prawo o postępowaniu przed sądami administracyjnymi",
}


def _num(loc: str) -> tuple:
    m = re.match(r"art\. (\d+)([a-z]*)(?:\^(\d+)([a-z]*))?", loc)
    if not m:
        return (0, "", 0, "")
    return (int(m.group(1)), m.group(2) or "", int(m.group(3) or 0), m.group(4) or "")


@pytest.mark.online
@pytest.mark.skipif(os.environ.get("PRAWNIK_ONLINE") != "1", reason="set PRAWNIK_ONLINE=1 to hit public APIs")
@pytest.mark.parametrize("eli", list(GOLDEN))
def test_golden_act_parses(tmp_path_factory, eli):
    store = Store(tmp_path_factory.mktemp("golden"))
    with PoliteClient() as client:
        ing = sync_act(store, client, eli)
    provs = [p for p in store.get_provisions(f"eli:{eli}") if p.locator.startswith("art.")]
    assert len(provs) >= 20, f"{GOLDEN[eli]}: only {len(provs)} articles"
    assert all(p.text.strip() for p in provs)
    keys = [_num(p.locator) for p in provs]
    decreasing = sum(1 for a, b in zip(keys, keys[1:], strict=False) if b <= a)
    assert decreasing <= max(2, len(keys) // 100), f"{GOLDEN[eli]}: {decreasing} non-increasing steps"
    sup_warn = sum(1 for w in ing.warnings if "indeks górny" in w)
    assert sup_warn <= max(10, len(provs) // 10), f"{GOLDEN[eli]}: {sup_warn} superscript warnings"
