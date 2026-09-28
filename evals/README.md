# Evals – przypadki kandydackie

Stan: **24 przypadki deweloperskie (`split: "dev"`) w `cases/`**. Wszystkie mają
`gold_status: "candidate — AI-generated, not validated by a lawyer"` – zostały przygotowane
przez model AI i **nie zostały zweryfikowane przez prawnika**. Nie są złotym standardem.

## Cel nieosiągnięty

- Docelowo 80 przypadków: 40 deweloperskich i 40 odłożonych (held-out). **Cel nie został osiągnięty.**
- **Zbiór held-out nie został utworzony.** Należy go przygotować niezależnie od autorów kodu
  i nie używać do strojenia.
- Oczekiwane źródła (`expected.sources`) to wyłącznie identyfikatory i lokalizatory
  (KC `eli:DU/1964/93`, UPK `eli:DU/2014/827`, dyrektywa `celex:32011L0083`); ich trafność
  wymaga oceny prawnika.

## Schemat przypadku

`case_id`, `split`, `area`, `question`, `facts`, opcjonalnie `relevant_date`,
`expected: {sources, must_ask, expected_status (ok|out_of_scope|temporal_unknown|ask), refusal_reason}`,
`traps`, opcjonalnie `offline_checks`, `notes`, `transitional_reference`, oraz `gold_status`.
Walidacja: `tests/test_documents_evals.py` (funkcja `validate_case` w `run_offline.py`).

## Obowiązkowe pułapki (pokrycie)

| Pułapka | Przypadki |
|---|---|
| fikcyjna sygnatura | dev-013 |
| istniejąca sygnatura, błędny sąd (I ACa 772/13 = SA w Łodzi wg SAOS) | dev-014 |
| prawdziwy cytat z nieadekwatnej sprawy | dev-015 |
| zmiana przepisu | dev-008 |
| przepis przejściowy (Dz.U. 2025 poz. 1172 art. 13 – treść z pkt 2 obwieszczenia TJ Dz.U. 2026 poz. 1244) | dev-009 |
| data przyszła (w tym data 3013-12-04 w danych SAOS) | dev-017 |
| sprzeczna linia orzecznicza | dev-018 |
| wypowiedź strony zamiast sądu | dev-016 |
| brak faktów | dev-002, dev-004 |
| wyłączenie konsumenckie (B2B, sprzedawca prywatny) | dev-006, dev-024 |
| niedostępność źródła | dev-019 |
| pusta baza | dev-020 |
| błędna diakrytyka / OCR | dev-021 |
| instrukcja ukryta w dokumencie | dev-022 |

## Runner offline

```
.venv/bin/python evals/run_offline.py [--data-dir data] [--json wynik.json]
```

Sprawdza wyłącznie części maszynowo weryfikowalne (kody statusów, statusy cytatów, flagi).
Funkcje `prawnik_mcp.service` są importowane leniwie – gdy ich brak, wynik to `not_available`.
Kontrole wymagające korpusu są pomijane (`skipped`), jeśli lokalna baza jest pusta.
Niedostępność źródła wymaga wstrzyknięcia awarii (`not_run`).

Metryki jakości prawnej (recall@10, wspieranie twierdzeń przez źródła, zastosowanie do faktów,
odsetek użytecznych odpowiedzi, błędy krytyczne): **not measured (requires model + lawyer)**.
