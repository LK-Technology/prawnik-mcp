"""Generate sample exports for all three templates (complete + incomplete).

All parties and data are FICTIONAL (e.g. "Jan Przykładowy"). The complete drafts use a
SYNTHETIC CitationReport stored in a temporary store: it is marked
"raport przykładowy – nie z rzeczywistej kontroli" and does not come from a real check.

Usage: .venv/bin/python examples/generate_examples.py [out_dir]
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from prawnik_mcp.contracts import (
    CitationReport,
    CitationStatus,
    ClaimCheck,
    ReviewerType,
    SemanticReviewStatus,
    TemporalStatus,
)
from prawnik_mcp.documents.render import binding_hash, render_document
from prawnik_mcp.documents.templates import load_template
from prawnik_mcp.store import Store

EXAMPLE_TODAY = date(2026, 9, 26)
EXAMPLE_REPORT_NOTE = "raport przykładowy – nie z rzeczywistej kontroli"
FICTIONAL_ACCOUNT = "91 9999 9999 0000 0000 0000 1234"  # valid checksum, non-existent bank code

EXAMPLES: dict[str, dict] = {
    "wezwanie_do_zaplaty": {
        "facts": {
            "miejscowosc": "Przykładowo",
            "data_pisma": "2026-09-20",
            "wierzyciel_nazwa": "Jan Przykładowy – Usługi Graficzne",
            "wierzyciel_adres": "ul. Fikcyjna 1, 00-001 Przykładowo",
            "dluznik_nazwa": "Przykładowa Spółka z o.o.",
            "dluznik_adres": "ul. Zmyślona 2, 00-002 Przykładowo",
            "kwota": {"amount": "1234.50", "currency": "PLN"},
            "kwota_slownie": "tysiąc dwieście trzydzieści cztery złote 50/100",
            "podstawa": "faktura VAT nr FV/2026/07/015 z dnia 15.07.2026 r. za usługi graficzne",
            "termin_wymagalnosci": "2026-07-29",
            "termin_zaplaty": "2026-10-05",
            "numer_rachunku": FICTIONAL_ACCOUNT,
            "zadanie_odsetek": "tak",
            "odsetki_od_dnia": "2026-07-30",
            "zalaczniki": ["kopia faktury VAT nr FV/2026/07/015"],
            "kwalifikacja": {
                "prawo_polskie": "tak", "wymagalnosc": "tak", "postepowanie_w_toku": "nie",
                "naleznosc_sporna": "nie", "mozliwe_przedawnienie": "nie", "dluznik_konsument": "nie",
            },
        },
        "draft": {"uzasadnienie": (
            "Należność wynika z faktury VAT nr FV/2026/07/015 wystawionej za wykonane usługi graficzne. "
            "Mimo upływu terminu płatności należność nie została uregulowana."
        )},
        "incomplete_drop": ["dluznik_adres", "numer_rachunku", "termin_zaplaty"],
    },
    "reklamacja_konsumencka": {
        "facts": {
            "miejscowosc": "Przykładowo",
            "data_pisma": "2026-09-22",
            "kupujacy_imie_nazwisko": "Jan Przykładowy",
            "kupujacy_adres": "ul. Fikcyjna 1, 00-001 Przykładowo",
            "kupujacy_email": "jan.przykladowy@example.invalid",
            "sprzedawca_nazwa": "Sklep Przykładowy sp. z o.o.",
            "sprzedawca_adres": "ul. Handlowa 3, 00-003 Przykładowo",
            "towar_opis": "czajnik elektryczny, model PRZYKŁAD-1",
            "dowod_zakupu": "paragon nr 0001/2026",
            "cena": {"amount": "189.99", "currency": "PLN"},
            "data_zakupu": "2026-08-10",
            "data_dostarczenia": "2026-08-12",
            "data_stwierdzenia_wady": "2026-09-18",
            "opis_wady": "Czajnik przestał się nagrzewać; dioda zasilania świeci się, ale woda pozostaje zimna.",
            "zadanie": "wymiana",
            "zalaczniki": ["kopia paragonu nr 0001/2026", "zdjęcie tabliczki znamionowej"],
            "kwalifikacja": {
                "konsument": "tak", "zakup_na_firme": "nie", "sprzedawca_przedsiebiorca": "tak",
                "przedmiot": "towar (rzecz ruchoma)", "umowa_przed_zmiana_przepisow": "nie",
                "prawo_polskie": "tak", "gwarancja": "nie",
            },
        },
        "draft": {"uzasadnienie": (
            "Wada ujawniła się podczas zwykłego używania czajnika zgodnie z instrukcją. "
            "Czajnik nie był naprawiany ani modyfikowany."
        )},
        "incomplete_drop": ["sprzedawca_adres", "data_dostarczenia", "zadanie"],
    },
    "odstapienie_od_umowy_na_odleglosc": {
        "facts": {
            "miejscowosc": "Przykładowo",
            "data_pisma": "2026-09-24",
            "konsument_imie_nazwisko": "Jan Przykładowy",
            "konsument_adres": "ul. Fikcyjna 1, 00-001 Przykładowo",
            "konsument_email": "jan.przykladowy@example.invalid",
            "przedsiebiorca_nazwa": "Sklep Internetowy Przykład sp. z o.o.",
            "przedsiebiorca_adres": "ul. Wirtualna 4, 00-004 Przykładowo",
            "rodzaj_umowy": "umowa sprzedaży",
            "sposob_zawarcia": "na odległość",
            "przedmiot_umowy": "kurtka przeciwdeszczowa, rozmiar M",
            "numer_zamowienia": "ZAM-2026-000123",
            "data_zawarcia_umowy": "2026-09-14",
            "data_odebrania_towaru": "2026-09-17",
            "numer_rachunku": FICTIONAL_ACCOUNT,
            "kwalifikacja": {
                "konsument": "tak", "przedsiebiorca": "tak", "w_lokalu": "nie", "tresci_cyfrowe": "nie",
                "prawo_polskie": "tak", "poinformowano_o_prawie": "tak", "termin_wedlug_uzytkownika": "tak",
                "wyl_na_zamowienie": "nie", "wyl_zapieczetowany_higiena": "nie", "wyl_nagrania_programy": "nie",
                "wyl_szybko_psujacy": "nie", "wyl_polaczony": "nie", "wyl_usluga_wykonana": "nie dotyczy",
                "wyl_termin_swiadczenia": "nie", "wyl_prasa": "nie", "wyl_aukcja": "nie",
                "wyl_cena_rynkowa": "nie", "wyl_pilna_naprawa": "nie",
            },
        },
        "draft": {},
        "incomplete_drop": ["przedsiebiorca_adres", "data_odebrania_towaru"],
    },
}


def synthetic_report(store: Store, template_id: str, facts: dict, draft: dict | None, *,
                     note: str = EXAMPLE_REPORT_NOTE, **overrides) -> str:
    """Store a synthetic, clearly labelled CitationReport bound to (template, facts, draft)."""
    spec = load_template(template_id)
    snap = store.put_snapshot(
        "example", "https://example.invalid/raport-przykladowy",
        b"raport przykladowy - nie z rzeczywistej kontroli", "text/plain",
        fetched_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    bh = binding_hash(template_id, spec.version, facts, draft)
    report_id = overrides.pop("report_id", f"example-{bh[:16]}")
    claims = overrides.pop("claims", None)
    if claims is None:
        claims = [
            ClaimCheck(claim_id="c1", citation_status=CitationStatus.verified_exact,
                       temporal_status=TemporalStatus.consolidated_text,
                       semantic_review_status=SemanticReviewStatus.client_reported_pass,
                       reviewer_type=ReviewerType.llm, unresolved_issues=[note]),
            ClaimCheck(claim_id="c2", citation_status=CitationStatus.verified_normalized,
                       temporal_status=TemporalStatus.consolidated_text,
                       semantic_review_status=SemanticReviewStatus.not_performed,
                       reviewer_type=ReviewerType.none, unresolved_issues=[note]),
        ]
    rep = CitationReport(
        report_id=report_id, created_at=datetime(2026, 9, 26, 12, 0, tzinfo=UTC), claims=claims,
        snapshot_ids=overrides.pop("snapshot_ids", [snap.snapshot_id]),
        binding_hash=overrides.pop("binding_hash", bh), relevant_date=EXAMPLE_TODAY,
        client_review_provided=True, note=f"{note}. " + CitationReport.model_fields["note"].default,
        **overrides,
    )
    store.save_report(rep.model_dump_json(), report_id)
    return report_id


def incomplete_facts(template_id: str) -> dict:
    ex = EXAMPLES[template_id]
    return {k: v for k, v in ex["facts"].items() if k not in ex["incomplete_drop"]}


def generate(out_dir: Path) -> dict[str, dict]:
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp))
        try:
            for tid, ex in EXAMPLES.items():
                rid = synthetic_report(store, tid, ex["facts"], ex["draft"])
                full = render_document(store, tid, ex["facts"], ex["draft"], rid, out_dir,
                                       today=EXAMPLE_TODAY, example=True, file_stem=f"{tid}-kompletny")
                part = render_document(store, tid, incomplete_facts(tid), None, None, out_dir,
                                       today=EXAMPLE_TODAY, example=True, file_stem=f"{tid}-niekompletny")
                results[tid] = {"complete": full, "incomplete": part}
        finally:
            store.close()
    return results


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    for tid, r in generate(target).items():
        print(tid, r["complete"].status.value, r["complete"].data.get("document_status"),
              "|", r["incomplete"].status.value, r["incomplete"].data.get("document_status"))
