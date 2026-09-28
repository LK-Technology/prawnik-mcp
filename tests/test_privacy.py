"""Offline tests for pseudonymisation (pseudonymisation ≠ anonymity)."""

from __future__ import annotations

import json
import logging

from prawnik_mcp import privacy

FACTS = {
    "kupujacy_imie_nazwisko": "Jan Przykładowy",
    "kupujacy_adres": "ul. Fikcyjna 1, 00-001 Przykładowo",
    "kupujacy_email": "jan.przykladowy@example.invalid",
    "sprzedawca_nazwa": "Sklep Przykładowy sp. z o.o.",
    "sprzedawca_adres": "ul. Handlowa 3, 00-003 Przykładowo",
    "numer_rachunku": "91 9999 9999 0000 0000 0000 1234",
    "opis_wady": "Jan Przykładowy zgłosił wadę telefonicznie z numeru 600 100 200; "
                 "kontakt: jan.przykladowy@example.invalid, PESEL 90010112345, NIP 123-456-32-18.",
    "data_zakupu": "2026-08-10",
    "cena": {"amount": "189.99", "currency": "PLN"},
    "kwalifikacja": {"konsument": "tak"},
}


def test_pseudonymize_replaces_personal_fields_and_free_text():
    pseudo, mapping = privacy.pseudonymize(FACTS, "reklamacja_konsumencka")
    blob = json.dumps(pseudo, ensure_ascii=False)
    for secret in ("Jan Przykładowy", "Fikcyjna", "jan.przykladowy@example.invalid", "9999 9999",
                   "600 100 200", "90010112345", "123-456-32-18", "Sklep Przykładowy"):
        assert secret not in blob, secret
    assert pseudo["kupujacy_imie_nazwisko"] == "[OSOBA_1]"
    assert pseudo["kupujacy_adres"].startswith("[ADRES_")
    assert pseudo["numer_rachunku"].startswith("[RACHUNEK_")
    assert "[OSOBA_1] zgłosił wadę" in pseudo["opis_wady"]  # same value -> same token
    assert "[PESEL_1]" in pseudo["opis_wady"] and "[TELEFON_1]" in pseudo["opis_wady"] and "[NIP_1]" in pseudo["opis_wady"]
    # non-personal data kept (and therefore still potentially identifying)
    assert pseudo["data_zakupu"] == "2026-08-10" and pseudo["cena"] == FACTS["cena"]
    assert pseudo["kwalifikacja"] == {"konsument": "tak"}
    assert set(mapping.values()) >= {"Jan Przykładowy", "jan.przykladowy@example.invalid"}


def test_restore_roundtrip():
    pseudo, mapping = privacy.pseudonymize(FACTS, "reklamacja_konsumencka")
    model_output = f"Szanowni Państwo, {pseudo['kupujacy_imie_nazwisko']} ({pseudo['kupujacy_email']}) składa reklamację."
    restored = privacy.restore(model_output, mapping)
    assert restored == "Szanowni Państwo, Jan Przykładowy (jan.przykladowy@example.invalid) składa reklamację."
    assert privacy.restore_obj(pseudo, mapping)["opis_wady"] == FACTS["opis_wady"]


def test_tokens_do_not_collide_beyond_nine():
    facts = {f"osoba_{i}_imie": f"Osoba Numer {i}" for i in range(1, 13)}
    pseudo, mapping = privacy.pseudonymize(facts)
    text = " ".join(pseudo.values())
    assert privacy.restore(text, mapping) == " ".join(facts.values())


def test_heuristics_without_template():
    pseudo, _ = privacy.pseudonymize({"dluznik_nazwa": "ABC Przykład", "telefon": "+48 600 100 200", "uwagi": "brak"})
    assert pseudo["dluznik_nazwa"].startswith("[OSOBA_") and pseudo["telefon"].startswith("[TELEFON_")
    assert pseudo["uwagi"] == "brak"


def test_describe_outgoing_lists_fields_not_values():
    desc = privacy.describe_outgoing(FACTS, "reklamacja_konsumencka")
    by_field = {d["field"]: d for d in desc}
    assert by_field["kupujacy_imie_nazwisko"]["sent_as"] == "pseudonim (token)"
    assert by_field["data_zakupu"]["sent_as"] == "jawnie"
    assert "pseudonimizacją" in by_field["opis_wady"]["sent_as"]
    assert "nie gwarantuje anonimowości" in by_field["*"]["sent_as"]
    blob = json.dumps(desc, ensure_ascii=False)
    assert "Jan Przykładowy" not in blob and "90010112345" not in blob


def test_docstring_and_no_logging(caplog):
    assert "NOT anonymisation" in privacy.__doc__
    with caplog.at_level(logging.DEBUG):
        privacy.pseudonymize(FACTS, "reklamacja_konsumencka")
        privacy.describe_outgoing(FACTS)
    assert "Przykładowy" not in caplog.text
