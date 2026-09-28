"""Parsers on real fixtures (tests/fixtures/raw), offline."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from prawnik_mcp.parsers.cellar import parse_cellar_xhtml
from prawnik_mcp.parsers.eli_pdf import parse_consolidated_pdf
from prawnik_mcp.parsers.saos import parse_saos_judgment

FX = Path(__file__).parent / "fixtures" / "raw"


@pytest.fixture(scope="module")
def upk():
    return parse_consolidated_pdf((FX / "eli_DU_2026_1244.pdf").read_bytes())


@pytest.fixture(scope="module")
def kc():
    return parse_consolidated_pdf((FX / "eli_DU_2026_795.pdf").read_bytes())


def arts(parsed):
    return {a.locator: a for a in parsed.articles}


def test_upk_header(upk):
    h = upk.header
    assert h.publication == "Dz.U. 2026 poz. 1244"
    assert h.announcement_date == date(2026, 9, 4)
    assert h.state_date == date(2026, 9, 2)
    assert [a.eli_id for a in h.included_amendments] == ["DU/2025/1172"]


def test_upk_excluded_provisions(upk):
    ex = " ".join(upk.header.excluded_provisions)
    assert "Art. 13. Do umów zawartych przed dniem wejścia w życie niniejszego przepisu" in ex
    assert "(Dz. U. poz. 1172)" in ex


def test_upk_art_27_and_38(upk):
    a = arts(upk)
    t27 = a["art. 27"].text
    assert t27.startswith("Art. 27. 1. Konsument, który zawarł umowę na odległość lub poza lokalem przedsiębiorstwa")
    assert "termin do odstąpienia od umowy wynosi 30 dni." in t27
    assert "Dziennik Ustaw" not in t27
    assert a["art. 27"].page_hint == "s. 10"
    t38 = a["art. 38"].text
    assert t38.startswith("Art. 38. 1. Prawo odstąpienia od umowy")
    assert "\n11) zawartej w drodze aukcji publicznej;" in t38


def test_upk_footnotes_removed_and_annexes(upk):
    a = arts(upk)
    assert "Niniejsza ustawa w zakresie swojej regulacji" not in a["art. 2"].text
    assert "art. 7aa" in a
    assert a["art. 55"].text == "Art. 55. Ustawa wchodzi w życie po upływie 6 miesięcy od dnia ogłoszenia."
    assert {x.locator for x in upk.annexes} == {"załącznik nr 1", "załącznik nr 2"}


def test_kc_superscripts(kc):
    a = arts(kc)
    for loc in ["art. 22^1", "art. 221", "art. 385^1", "art. 471", "art. 481", "art. 556", "art. 556^1", "art. 561", "art. 43^10"]:
        assert loc in a, loc
    assert a["art. 22^1"].text.startswith("Art. 22¹. Za konsumenta uważa się osobę fizyczną")
    assert a["art. 221"].text.startswith("Art. 221. Czynności prawne określające zarząd")
    assert a["art. 385^1"].text.startswith("Art. 385¹. § 1. Postanowienia umowy zawieranej z konsumentem")
    assert "§ 2¹. Maksymalna wysokość odsetek za opóźnienie" in a["art. 481"].text
    assert not any("numeracja nie rośnie" in w for x in kc.articles for w in x.warnings)


def test_kc_header_and_later_dates(kc):
    h = kc.header
    assert h.state_date == date(2026, 5, 19)
    assert [x.eli_id for x in h.included_amendments] == ["DU/2025/1172", "DU/2025/1508", "DU/2026/184", "DU/2026/507"]
    assert "art. 125¹ § 2" in " ".join(h.excluded_provisions)
    assert ("DU/2026/507", date(2028, 11, 1)) in h.later_entry_dates


def test_saos_future_date_flag():
    j, doc = parse_saos_judgment((FX / "saos_31345.json").read_bytes(), "saos:x", "x",
                                 datetime(2026, 9, 26, tzinfo=UTC))
    assert j.judgment_date is None
    assert "judgment_date_in_future" in j.data_quality_flags
    assert j.original_url.startswith("http://orzeczenia.ms.gov.pl/")
    assert j.case_numbers == ["I ACa 772/13"]
    assert "<" not in j.text and "Sygn. akt I ACa 772/13\nWYROK" in j.text
    assert doc.metadata["judgment_date_raw"] == "3013-12-04"


def test_saos_sup_restored():
    j, _ = parse_saos_judgment((FX / "saos_361247.json").read_bytes(), "saos:x", "x", datetime(2026, 9, 26, tzinfo=UTC))
    assert j.judgment_date == date(2017, 12, 29)
    assert "art. 505¹³ § 2 k.p.c." in j.text


def test_cellar_art_9():
    c = parse_cellar_xhtml((FX / "celex_32011L0083.xhtml").read_bytes())
    a = {x.locator: x for x in c.articles}
    assert len(c.articles) == 35
    assert c.oj_reference == "L 304/64"
    assert "przez okres 14 dni" in a["art. 9"].text
    assert "\na) w przypadku umów o świadczenie usług – dnia zawarcia umowy;" in a["art. 9"].text
    assert c.title.startswith("DYREKTYWA PARLAMENTU EUROPEJSKIEGO I RADY 2011/83/UE")


def test_published_amending_act_keeps_quoted_articles_inside():
    """DU/2025/1172 has no consolidated text; quoted new articles („Art. 125²…”) stay inside art. 1."""
    from prawnik_mcp.parsers.eli_pdf import parse_published_act_pdf

    pdf = (FX / "eli_published" / "eli_DU_2025_1172.pdf").read_bytes()
    parsed = parse_published_act_pdf(pdf)
    locs = [a.locator for a in parsed.articles]
    assert locs == [f"art. {i}" for i in range(1, 17)]
    art13 = next(a for a in parsed.articles if a.locator == "art. 13")
    assert "w brzmieniu dotychczasowym" in art13.text
    assert "125" in parsed.articles[0].text  # quoted amendments remain part of art. 1
