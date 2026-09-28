"""Corpus synchronisation: KC + UPK (ELI), directive 2011/83/EU (Cellar), small SAOS sample.

`sync_corpus(store, offline_fixtures=Path("tests/fixtures/raw"))` builds the same corpus from
locally saved API responses, without network. A failure of one source never stops the others;
the source is then marked `unavailable` and its previously stored data are kept.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from prawnik_mcp.connectors import cellar as cellar_conn
from prawnik_mcp.connectors import eli as eli_conn
from prawnik_mcp.connectors import saos as saos_conn
from prawnik_mcp.connectors.http import PoliteClient
from prawnik_mcp.contracts import SourceRecord
from prawnik_mcp.store import Store

TERMS_CHECKED = date(2026, 9, 26)

ELI_ACTS = {"DU/1964/93": "Kodeks cywilny", "DU/2014/827": "ustawa o prawach konsumenta"}
CELEX_ACTS = ["32011L0083"]
_SAOS_COMMON = {"courtType": "COMMON", "judgmentDateFrom": "2016-01-01"}
# Plain full-text queries sorted by date returned irrelevant results; phrases + keywords work better.
SAOS_QUERIES: list[dict[str, str]] = [
    {"all": '"odstąpienia od umowy zawartej na odległość"', **_SAOS_COMMON},
    {"all": '"niezgodność towaru z umową"', "keywords": "sprzedaż konsumencka", **_SAOS_COMMON},
    {"all": '"wezwanie do zapłaty" "odsetki ustawowe za opóźnienie"', "keywords": "odsetki", **_SAOS_COMMON},
]
SAOS_MAX_TOTAL = 30


class SourceSyncResult(BaseModel):
    source_id: str
    ok: bool = False
    counts: dict[str, int] = {}
    errors: list[str] = []
    warnings: list[str] = []


class SyncReport(BaseModel):
    mode: Literal["online", "offline_fixtures"]
    started_at: datetime
    finished_at: datetime | None = None
    sources: dict[str, SourceSyncResult] = Field(default_factory=dict)
    store_stats: dict[str, int] = {}

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.sources.values())


# --------------------------------------------------------------------------- source records

_BASE = {
    "eli": dict(
        publisher="Kancelaria Sejmu RP – API ELI (Dziennik Ustaw)",
        base_url="https://api.sejm.gov.pl/eli",
        terms_of_use=(
            "Teksty aktów normatywnych nie są przedmiotem prawa autorskiego (art. 4 pkt 2 pr. aut.). "
            "Odrębnego regulaminu API ELI nie odnaleziono – warunki korzystania z API niezweryfikowane. "
            "Dokumentacja: https://api.sejm.gov.pl/eli_pl.html"
        ),
        required_attribution="Źródło: Dziennik Ustaw RP, API ELI Kancelarii Sejmu (api.sejm.gov.pl)",
        known_gaps=[
            "Tylko dwa akty: Kodeks cywilny i ustawa o prawach konsumenta.",
            "Tekst jednolity może zawierać zmiany wchodzące w życie po dniu stanu prawnego (pending_changes); "
            "dla zdarzeń sprzed tych dat brzmienie jest niepewne.",
            "Brak historii brzmień: valid_from/valid_to nieustalone; przepisy przejściowe spoza TJ zapisane w excluded_provisions.",
            "Tekst odczytany z PDF (warstwa tekstowa pypdf): indeksy górne odtwarzane z rozmiaru i położenia czcionki; "
            "zdarzają się artefakty odstępów wewnątrz wyrazów (np. „pozw alają”) obecne w warstwie tekstowej PDF.",
            "Przypisy usunięte z treści artykułów; zachowane w metadanych dokumentu (article_footnotes).",
        ],
    ),
    "saos": dict(
        publisher="SAOS – System Analizy Orzeczeń Sądowych (ICM UW); orzeczenia pochodzą z portali sądów",
        base_url="https://www.saos.org.pl/api",
        terms_of_use=(
            "Orzeczenia sądów to dokumenty urzędowe (art. 4 pkt 2 pr. aut.). Regulamin serwisu SAOS i prawa do bazy danych "
            "niezweryfikowane. Teksty zanonimizowane przez źródło; nie redystrybuujemy korpusu."
        ),
        required_attribution="Źródło: SAOS (saos.org.pl) oraz portal orzeczeń sądu wskazany w original_url",
        known_gaps=[
            "Tylko mała próbka (≤ 30) orzeczeń dla trzech tematów; brak pokrycia orzecznictwa.",
            "Błędy danych źródła, np. saos:31345 ma datę wyroku 3013-12-04 – takie daty są zerowane i flagowane.",
            "Prawomocność nieznana (finality=unknown).",
            "Treść z SAOS, nie bezpośrednio z portalu sądu; możliwe różnice względem wersji u wydawcy.",
        ],
    ),
    "cellar": dict(
        publisher="Urząd Publikacji Unii Europejskiej – Cellar",
        base_url="https://publications.europa.eu/resource/celex",
        terms_of_use=(
            "Wg informacji EUR-Lex o ponownym wykorzystaniu treści (decyzja Komisji 2011/833/UE) dozwolone z podaniem źródła; "
            "odczytano opis na stronie EUR-Lex w etapie 0, bez oceny prawnej. "
            "https://eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html"
        ),
        required_attribution="© Unia Europejska, https://eur-lex.europa.eu",
        known_gaps=[
            "Tylko dyrektywa 2011/83/UE.",
            "Tekst pierwotny z Dz.U. UE – bez konsolidacji; zmiany (np. dyrektywą 2019/2161) nieuwzględnione.",
            "Strona EUR-Lex zwraca wyzwanie anty-botowe (202) – nie jest używana ani obchodzona.",
            "Motywy (preambuła) i załączniki nie są zapisane jako przepisy.",
        ],
    ),
}


def _record(store: Store, source_id: str, *, success: bool, partial: bool, offline: bool,
            coverage: str, intervals: list[str]) -> SourceRecord:
    prev = store.get_source(source_id)
    base = _BASE[source_id]
    if success:
        status = "degraded" if (partial or offline) else "ok"
        last = prev.last_successful_sync if (prev and offline) else (None if offline else datetime.now(UTC))
        cov = coverage + (" [zbudowane z lokalnych próbek offline – tests/fixtures/raw]" if offline else "")
        ivs = intervals
    else:
        status = "unavailable"
        last = prev.last_successful_sync if prev else None
        cov = prev.coverage if prev else "brak danych lokalnych"
        ivs = prev.supported_intervals if prev else []
    rec = SourceRecord(
        source_id=source_id, terms_checked_at=TERMS_CHECKED, last_successful_sync=last,
        access_status=status, coverage=cov, supported_intervals=ivs, **base,
    )
    store.upsert_source(rec)
    return rec


def _eli_coverage(store: Store) -> tuple[str, list[str]]:
    parts, ivs = [], []
    for logical, name in ELI_ACTS.items():
        doc = store.get_document(f"eli:{logical}")
        if not doc:
            continue
        cur = (doc.metadata.get("consolidated_versions") or [{}])[0]
        n = cur.get("provision_count", 0)
        iv = f"{name}: TJ {cur.get('publication')}, stan prawny na {cur.get('state_date')}"
        parts.append(f"{iv} ({n} jednostek)")
        ivs.append(iv)
    return ("; ".join(parts) or "brak"), ivs


# --------------------------------------------------------------------------- online


def _sync_online(store: Store, client: PoliteClient, report: SyncReport, force: bool, saos_max: int) -> None:
    # ELI
    res = SourceSyncResult(source_id="eli")
    for logical in ELI_ACTS:
        try:
            ing = eli_conn.sync_act(store, client, logical, force=force)
            res.counts[logical] = ing.provisions
            res.warnings += [f"{logical}: {w}" for w in ing.warnings]
        except Exception as e:  # noqa: BLE001 - one act must not stop the others
            res.errors.append(f"{logical}: {e}")
    res.ok = not res.errors
    cov, ivs = _eli_coverage(store)
    _record(store, "eli", success=bool(res.counts), partial=bool(res.errors), offline=False, coverage=cov, intervals=ivs)
    report.sources["eli"] = res

    # Cellar
    res = SourceSyncResult(source_id="cellar")
    for celex in CELEX_ACTS:
        try:
            ing = cellar_conn.sync_celex(store, client, celex, force=force)
            res.counts[celex] = ing.provisions
            res.warnings += ing.warnings
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{celex}: {e}")
    res.ok = not res.errors
    _record(store, "cellar", success=bool(res.counts), partial=bool(res.errors), offline=False,
            coverage=_cellar_coverage(store), intervals=["dyrektywa 2011/83/UE: tekst pierwotny Dz.U. UE L 304 z 22.11.2011"])
    report.sources["cellar"] = res

    # SAOS
    res = SourceSyncResult(source_id="saos")
    today = datetime.now(UTC).date().isoformat()
    queries = [{**q, "judgmentDateTo": today} for q in SAOS_QUERIES]
    try:
        ing = saos_conn.sync_queries(store, client, queries, max_total=saos_max, force=force)
        res.counts = {"fetched": len(ing.judgments), "reused_checkpoint": len(ing.skipped)}
        res.errors = ing.errors
        res.warnings = [f"{k}: {', '.join(v)}" for k, v in ing.flags.items()]
        res.ok = not ing.errors and bool(ing.judgments or ing.skipped)
        success = bool(ing.judgments or ing.skipped)
    except Exception as e:  # noqa: BLE001
        res.errors.append(str(e))
        success = False
    _record(store, "saos", success=success, partial=bool(res.errors), offline=False,
            coverage=_saos_coverage(store), intervals=[])
    report.sources["saos"] = res


def _cellar_coverage(store: Store) -> str:
    docs = [d for d in store.list_documents() if d.document_id.startswith("celex:")]
    return "; ".join(f"{d.document_id} ({len(store.get_provisions(d.document_id))} artykułów, tekst pierwotny)" for d in docs) or "brak"


def _saos_coverage(store: Store) -> str:
    n = store.stats()["judgments"]
    return f"{n} orzeczeń SAOS (próbka tematyczna: odstąpienie od umowy na odległość, reklamacja/niezgodność towaru, odsetki/wezwanie do zapłaty)"


# --------------------------------------------------------------------------- offline


def _mtime(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, UTC)


def _sync_offline(store: Store, fx: Path, report: SyncReport) -> None:
    # ELI: pair every TJ metadata file with its PDF and the logical act's metadata
    res = SourceSyncResult(source_id="eli")
    metas = {p: json.loads(p.read_bytes()) for p in sorted(fx.glob("eli_*.meta.json"))}
    by_eli = {m.get("ELI"): p for p, m in metas.items()}
    for p, m in metas.items():
        targets = [r["id"] for r in (m.get("references") or {}).get("Tekst jednolity dla aktu", [])]
        if not targets:
            continue
        pdf = fx / p.name.replace(".meta.json", ".pdf")
        logical_p = by_eli.get(targets[0])
        if not pdf.exists() or logical_p is None:
            res.errors.append(f"{m.get('ELI')}: brak PDF lub metadanych aktu {targets[0]} w próbkach")
            continue
        # use only the latest TJ available for this act
        if eli_conn.latest_consolidated(metas[logical_p]) != m["ELI"]:
            res.warnings.append(f"{m['ELI']}: nie jest najnowszym TJ wg metadanych; pominięto")
            continue
        try:
            ing = eli_conn.ingest_from_files(store, logical_p.read_bytes(), p.read_bytes(), pdf.read_bytes(),
                                             fetched_at=_mtime(pdf))
            res.counts[targets[0]] = ing.provisions
            res.warnings += [f"{targets[0]}: {w}" for w in ing.warnings]
        except Exception as e:  # parsing problem of one act must not stop the others
            res.errors.append(f"{targets[0]}: {type(e).__name__}: {e}")
    res.ok = bool(res.counts) and not res.errors
    cov, ivs = _eli_coverage(store)
    _record(store, "eli", success=bool(res.counts), partial=bool(res.errors), offline=True, coverage=cov, intervals=ivs)
    report.sources["eli"] = res

    res = SourceSyncResult(source_id="cellar")
    for p in sorted(fx.glob("celex_*.xhtml")):
        celex = p.stem.split("_", 1)[1]
        try:
            ing = cellar_conn.ingest_xhtml(store, celex, p.read_bytes(), cellar_conn.celex_url(celex), _mtime(p))
            res.counts[celex] = ing.provisions
        except Exception as e:
            res.errors.append(f"{celex}: {type(e).__name__}: {e}")
    res.ok = bool(res.counts) and not res.errors
    _record(store, "cellar", success=bool(res.counts), partial=bool(res.errors), offline=True,
            coverage=_cellar_coverage(store), intervals=["dyrektywa 2011/83/UE: tekst pierwotny Dz.U. UE L 304 z 22.11.2011"])
    report.sources["cellar"] = res

    res = SourceSyncResult(source_id="saos")
    n = 0
    for p in sorted(fx.glob("saos_*.json")):
        if not re.fullmatch(r"saos_\d+\.json", p.name):
            continue
        try:
            j = saos_conn.ingest_judgment_bytes(store, p.read_bytes(), saos_conn.judgment_url(p.stem.split("_")[1]), _mtime(p))
            n += 1
            if j.data_quality_flags:
                res.warnings.append(f"{j.document_id}: {', '.join(j.data_quality_flags)}")
        except Exception as e:
            res.errors.append(f"{p.name}: {type(e).__name__}: {e}")
    res.counts = {"judgments": n}
    res.ok = n > 0 and not res.errors
    _record(store, "saos", success=n > 0, partial=bool(res.errors), offline=True, coverage=_saos_coverage(store), intervals=[])
    report.sources["saos"] = res


# --------------------------------------------------------------------------- entry point


def sync_corpus(store: Store, client: PoliteClient | None = None, *, offline_fixtures: Path | None = None,
                force: bool = False, saos_max: int = SAOS_MAX_TOTAL) -> SyncReport:
    report = SyncReport(mode="offline_fixtures" if offline_fixtures else "online", started_at=datetime.now(UTC))
    if offline_fixtures is not None:
        _sync_offline(store, Path(offline_fixtures), report)
    else:
        own = client is None
        client = client or PoliteClient()
        try:
            _sync_online(store, client, report, force, min(saos_max, SAOS_MAX_TOTAL))
        finally:
            if own:
                client.close()
    report.finished_at = datetime.now(UTC)
    report.store_stats = store.stats()
    return report
