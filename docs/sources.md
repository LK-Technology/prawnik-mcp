# Data sources — access, terms, known gaps

Status as of 2026-09-26. This is a technical description, **not legal advice**. "Unverified" means that no binding terms document was read.

**General rules.** These are enforced in `connectors/http.py`:
- **Access:** `https` only, to allowlisted hosts. Every redirect is checked and private addresses are blocked. The client sends an identifiable User-Agent, makes one request at a time per host with a minimum delay between them, and retries with exponential backoff, honouring `Retry-After`. Responses are capped in size.
- **Errors:** a 404 is reported as `NotFoundUpstream`. Any other failure is reported as `SourceUnavailable`.
- **Anti-bot pages:** challenges such as the EUR-Lex 202 challenge page are never bypassed.
- **Snapshots:** every response is stored as a snapshot with its sha256, URL and fetch time.
- **Redistribution:** the project ships **no corpus**. Users build it locally with `prawnik-mcp sync`.

Polish statutes and official documents are excluded from copyright (art. 4 of the Polish Copyright Act). That does **not** settle database rights, personal data, or the terms of a particular service, so each source is assessed separately below.

## ELI API — Chancellery of the Sejm (Dziennik Ustaw)

- **Endpoints:**
  - act metadata: `https://api.sejm.gov.pl/eli/acts/{DU/year/pos}`;
  - text: `.../text.pdf` and `.../text.html`;
  - docs: https://api.sejm.gov.pl/eli_pl.html
- **Terms:** there is no separate API licence (unverified). Legal acts are exempt under art. 4 of the Copyright Act.
- **Attribution:** "Źródło: Dziennik Ustaw RP, API ELI Kancelarii Sejmu".
- **How we use it:**
  - Start from the logical act's metadata.
  - Take the latest reference labelled `Inf. o tekście jednolitym`.
  - Fetch that notice's metadata and its PDF.
  - Parse it with `parsers/eli_pdf.py`.
- **Known gaps:**
  - **`text.html` of the base act is the original (e.g. 1964) wording**, not the current one. Consolidated texts (TJ) are often PDF-only.
  - A TJ can include amendments that enter into force after its "state of law" date. For example, the TJ of the Civil Code, Dz.U. 2026 poz. 795, includes Dz.U. 2026 poz. 507, whose ELI date is 2028-11-01. These amendments are recorded act-wide in `pending_changes`, not per article.
  - Transitional provisions that the TJ does not include are quoted verbatim in `excluded_provisions`.
  - There is no history of wordings: `valid_from` and `valid_to` are not populated.
  - PDF text extraction flattens superscripts (art. 22¹ comes out as "221"). They are restored heuristically from glyph size and baseline, and uncertain cases go to `ProvisionVersion.warnings`.

## SAOS — Court judgments analysis system (ICM, University of Warsaw)

- **Endpoints:**
  - `https://www.saos.org.pl/api/search/judgments` (search);
  - `.../api/judgments/{id}` (single judgment);
  - `.../api/dump/judgments` (bulk dump).

  Always send `Accept: application/json`. Without it the API returns 406. The dump endpoint also requires `pageSize ≥ 10`, otherwise it returns 400.
- **Terms:** judgments are official documents, anonymised at the source. The service terms and database rights are unverified.
- **Attribution:** "Źródło: SAOS (saos.org.pl)", plus the original court portal from `source.judgmentUrl`.
- **Known gaps:**
  - There are data errors at the source. For example `saos:31345` has the judgment date `3013-12-04`. We set such dates to null and flag them (`judgment_date_in_future`).
  - Finality (prawomocność) is unknown.
  - The SAOS text can differ from the version on the court portal.
  - **Coverage by court type** (API counts and newest judgment date, checked 2026-09-28):

    | Court type | Judgments | Newest |
    |---|---|---|
    | Common courts | 472,575 | 2026-09-24 |
    | Supreme Court (SN) | 38,081 | 2016-06-22 |
    | Constitutional Tribunal (TK) | 9,503 | 2015-12-09 |
    | National Appeal Chamber (KIO) | 22,168 | 2018-09-06 |
    | Administrative courts | 0 | — |

    Supreme Court and Constitutional Tribunal rulings after 2016/2015 are **not** available through SAOS.
    Current SN and TK rulings come from the SN and TK connectors (below). KIO has its own, current connector. Administrative courts: CBOSA.
  - SAOS search can be slow (15–30 s) and the whole service has maintenance windows, when it serves an HTML
    "Przerwa techniczna" page with status 200. The client reports that as unavailable, not as an empty result.

## SN — Supreme Court (Sąd Najwyższy), sn.pl ruling database

Status as of 2026-09-28. Technical description, **not legal advice**. "Unverified" means no binding terms document was read.

- **Host:** `https://www.sn.pl` (Joomla site of the Supreme Court, behind an Imperva/Incapsula WAF). No documented public API and no bulk export. SAOS mirrors SN only up to 2016; sn.pl has current rulings and older ones back to at least 1999.
- **Endpoints (verified live 2026-09-28).** The search page `/pl/wyszukiwarka-orzeczen` calls a com_ajax plugin, `GET /pl/index.php?option=com_ajax&plugin=snproxy&format=json&task=…`. Answers are JSON wrapped twice (`{"success":true,"data":[{"success":true,"data":X}]}`).
  - Search: `task=searchOrzeczenia` with `q` and `tresc` (full text, both sent as the page does), `sygnatura`, `data_wydania_od` / `data_wydania_do`, `izba` (e.g. `Izba Cywilna`), `forma_orzeczenia` (e.g. `uchwała SN`), `sedzia_w_skladzie`, `strona`, `rozmiar_strony` (10/25/50/100). It returns case number, date, form and id only: no snippet, no total, newest first. `sygnatura` is a case-insensitive **substring** match.
  - Metadata: `task=detailsOrzeczenie&id=…` (chambers, bench, presiding judge, rapporteur, reasons author, dissenting judges, division, modification date).
  - Text: `task=OrzeczeniePlikHtml&id=…` returns `{"raw": base64}`, an HTML rendering of the ruling PDF with one positioned element per line.
  - An unknown id returns **200 with a problem object** `{"title":"Not Found","status":404}`; the connector raises `NotFoundUpstream`.
  - `robots.txt` is the Joomla default and disallows `/administrator/ /api/ /bin/ /cache/ /cli/ /components/ /includes/ /installation/ /language/ /layouts/ /libraries/ /logs/ /modules/ /plugins/ /tmp/`. The endpoint above (`/pl/index.php`) is not disallowed; no disallowed path is used.
- **How we use it:**
  - Live search asks SN for case numbers always, and for phrases only with `filters.court_type = "SUPREME"`, because phrase results are unranked metadata. Case-number hits are filtered to the exact number locally.
  - `fetch("sn:<id>")` gets the metadata and the text (2 requests) and stores both raw JSON answers as snapshots. Text lines are sorted by position, running headers on pages 2+ are dropped, superscript digits become `¹²³` ("art. 804¹").
  - Bulk sync walks issue-date windows oldest first, with checkpoint/resume; `since` is required unless a case number is given. At 0.5 req/s one week of rulings (about 1,000) takes roughly 70 minutes.
  - A non-JSON answer (for example a WAF page) raises an error; it is never read as "0 hits".
- **Terms actually read (2026-09-28):**
  - The database page: no terms.
  - `/pl/informacje/ponowne-wykorzystywanie-informacji-publicznych` summarises the 2021 Open Data Act. SN "may" set reuse conditions, but **none are set for the ruling database**; reuse is free of charge. The page says a reuse request is needed for information published outside BIP without stated conditions; whether that applies here is **unverified** (sn.pl carries the BIP logo).
  - Rulings are official documents (art. 4 pt 2 of the Copyright Act): our reading, **unverified**. Database rights **unverified**. No published rate limit; we use 0.5 req/s.
- **Personal data:** rulings are anonymised by SN (parties as initials). Judges, lay judges, clerks and prosecutors are named. Fixtures pass `scripts/pii_scan.py`.
- **Attribution:** "Źródło: Sąd Najwyższy – Baza orzeczeń (www.sn.pl)".
- **Code provenance:** original code.
- **Known gaps:**
  - Undocumented proxy that can change with any site release; a periodic live smoke check is advisable.
  - Ids are upstream search-index ids; stability across a reindex is unknown.
  - Reasons are often published after the operative part (flag `uzasadnienie_not_in_text`); re-fetch with force.
  - Dissent metadata can be empty although the text records a dissent. There is no separate thesis field.
  - Forms other than wyrok/postanowienie/uchwała/zarządzenie become `judgment_type = UNKNOWN` with flag `judgment_type_unmapped:<form>`.
  - Overlap with SAOS (`courtType = SUPREME`, up to 2016): the same ruling can exist as `saos:<id>` and `sn:<id>`, and a case-number lookup then returns `ambiguous`.
  - Line-end hyphenation and letter-spaced emphasis ("o d d a l i ł") are kept as in the source.

## TK — Constitutional Tribunal (Trybunał Konstytucyjny), trybunal.gov.pl

Status as of 2026-09-28. Technical description, **not legal advice**. "Unverified" means no binding terms document was read.

- **Host:** `https://trybunal.gov.pl` (TYPO3 site of the Tribunal; site search is EXT:solr). No public API or bulk export. SAOS has TK rulings only up to 2015.
- **Endpoints (verified live 2026-09-28):**
  - Ruling: `GET /postepowanie-i-orzeczenia/{wyroki|postanowienia}/art/{slug}`. The article holds the **operative part only** (the reasoning comes from IPO, below): case number, date, composition (chair, rapporteurs), operative part, voting and dissent markers. An unknown slug returns 404.
  - Case page: `GET /s/{sig}`, the site's short link per case number: `sk-20-25` for current cases, `p-3512` for older ones (tried second). It links to the case's rulings. An unknown case returns 404.
  - Search: `GET /wyszukiwarka?tx_solr[q]=…&tx_solr[filter][0]=category:Wyrok&tx_solr[filter][1]=category:Postanowienie&tx_solr[page]=N`, 10 hits per page, relevance order, snippet but no structured case number or date.
  - Listings: `/postepowanie-i-orzeczenia/wyroki` (back to 2002) and `/postanowienia`, 12 per page, newest first. Pager links carry a TYPO3 `cHash` and are followed as-is.
  - `robots.txt` disallows only `/typo3/` and `/typo3conf/`.
  - IPO, full text with reasoning: `GET https://ipo.trybunal.gov.pl/ipo/Sprawa?pokaz=dokumenty&sygnatura=K%201/20`, the deep link the ruling articles themselves carry. One request, no cookies, ViewState or JavaScript needed: the JSF page is rendered on the server, one tab per ruling of the case, each with the full text (`div#tekst_<id>`: komparycja, tenor, uzasadnienie, then dissenting opinions). Verified on K 1/20, SK 20/25, P 35/12, K 23/11 and K 21/24 (a postanowienie). An unknown case number gives 200 and a redirect to `/ipo/exception/sprawaId.xhtml` ("Nie odnaleziono sprawy"). IPO's `robots.txt` answers 404 (no restrictions).
  - IPO answers **HTTP/2 only**: an HTTP/1.1 request is accepted and never answered. This is why the earlier recon saw "no response within 30–60 s". `PoliteClient` now negotiates HTTP/2 (dependency `httpx[http2]`); without the `h2` package the client cannot read IPO.
  - Not used: OTK ZU (`otkzu.trybunal.gov.pl`) and the IPO search form (JSF POST; the case deep link makes it unnecessary).
- **How we use it:**
  - Live search: a query that is exactly one TK case number (or the `case_number` filter) goes to `/s/{sig}` (1–2 requests); anything else goes to the site search restricted to rulings, at most 3 pages.
  - `fetch("tk:<section>/<slug>")` gets the ruling page and stores the raw response as the snapshot (1 request), then the IPO case page for the ruling's (first) case number and stores that as a second snapshot (1 request, 70 KB to 1 MB). The IPO tab is matched to the article by kind (wyrok or postanowienie) and date; the case number shown on the IPO page must equal the one asked for. The stored text is then the IPO text (komparycja, tenor, uzasadnienie) with `metadata.text_scope = "full_text_with_reasoning"`, `reasoning_included = true`, and `metadata.ipo` (URL, snapshot id, tab id, the `.doc` download link, reasoning length, number of dissenting opinions). Dissenting opinions stay in the IPO snapshot only, not in the text. The article's operative part stays in `metadata.operative_part`.
  - Fallback: if IPO fails (network, 5xx, unknown case, no matching tab, layout change), the operative-part record is stored, the flag `ipo_full_text_unavailable` is added (a sync warning) and the reason is in `metadata.ipo.error`; the next `fetch` retries IPO only. A ruling whose IPO tab has no reasoning keeps the operative part and the flag `reasoning_not_included`. Offline sync reads recorded `tk/ipo_*.html` pages.
  - Bulk sync: listing scope (default, newest first, cursor = section and next pager URL, `since` stops a section), query scope (search pages) or one case. A page is committed only when all its items were handled.
  - A 200 response without the expected block raises an error; it is never read as "0 hits".
  - `finality = "final"` is set from art. 190 ust. 1 of the Constitution, not read from the source. The flag `reasoning_not_included` remains only on records without a reasoning (IPO unavailable, or the ruling has none).
- **Terms actually read (2026-09-28):** `/informacja-publiczna-media/ponowne-wykorzystywanie` sets reuse conditions under art. 14–16 of the Act of 11 August 2021: state the source, when the information was created and obtained, and the author if known; state that it was processed; the Tribunal is not liable for processed information. Rulings are official documents (art. 4 pt 2 of the Copyright Act): our reading, **unverified**. Database rights **unverified**. No published rate limit; we use 0.5 req/s. IPO shows no terms of its own (no terms page; the user manual has none) and has no robots restrictions; it is the same publisher, so the same conditions are applied (**unverified** that they formally extend to IPO).
- **Personal data:** judges and the court clerk are named; natural-person complainants appear by initials. The page header carries the Tribunal's institutional press e-mail, which is allowlisted in `scripts/pii_allowlist.txt`.
- **Attribution:** "Źródło: Trybunał Konstytucyjny – trybunal.gov.pl (orzeczenia wraz z uzasadnieniami; informacja przetworzona: HTML → tekst, bez zdań odrębnych)". Cite with the ruling date and the fetch date (snapshot `fetched_at`), as the reuse conditions ask.
- **Code provenance:** original code; `fold` and `PL_MONTHS` come from `parsers/kio.py`.
- **Known gaps:**
  - No reasoning (uzasadnienie), no OTK ZU / Dz.U. publication reference, no preliminary-review rulings (Ts/Tw).
  - The site search indexes only what the site publishes, so phrase recall is low; hits usually lack a case number and date until fetched.
  - Older articles (seen: 2013–2014) carry the source's windows-1250 damage ("Sšdu"); the text is stored as-is and flagged `source_encoding_artifacts`.
  - The ruling date comes from the text header; the listing date is only a cross-check.
  - Ids are article slugs, stable as long as editors do not rename articles.
  - Overlap with SAOS (`courtType = CONSTITUTIONAL_TRIBUNAL`, up to 2015): the same ruling can exist as `saos:<id>` and `tk:<section>/<slug>`, and a case-number lookup then returns `ambiguous`.

## Registries — KRS, VAT white list, VIES (on-demand lookups)

Status as of 2026-10-04. Technical description, **not legal advice**. Code: `src/prawnik_mcp/registries/`
(`ids.py`, `krs.py`, `wl_vat.py`, `vies.py`, `lookup.py`). Catalog role `lookup`: nothing is synced, indexed or
searchable as a document; the MCP tool `lookup_entity` asks the registries live.

- **Identifiers (`ids.py`, offline):** NIP (weights 6,5,7,2,3,4,5,6,7, mod 11), REGON 9/14 (KRS writes a 9-digit REGON
  padded with `00000`; it is shortened back), KRS (zero-padded to 10), Polish NRB/IBAN (mod 97), EU VAT (per-country
  VIES formats; GR -> EL). 11-digit input is refused as a possible PESEL; text is refused as a name. **There is no search
  by name, surname or PESEL, and no bank-account owner lookup.**
- **Paths and cost:** NIP/REGON -> white-list search (1 search) -> KRS current extract if the subject has a KRS number
  (1–2 GETs). KRS -> KRS extract (P, then S) -> white-list search by the extract's NIP (1 search; `include_vat=false`
  skips it). EU VAT -> VIES (1 POST); PL VAT also runs the NIP path. `bank_account` -> white-list check (1 of the
  'check' quota). `include_full_history` -> KRS full extract (1 GET). A struck-off entity always costs the full extract.

### KRS — open API of the Ministry of Justice
- **Endpoints (verified 2026-10-04):** `GET https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}?rejestr={P|S}&format=json`
  and `/OdpisPelny/…`. No key, no published rate limit; we use 0.5 req/s.
- **Answers observed:** 200 JSON (`odpis.naglowekA|naglowekP`, `odpis.dane.dzial1..6`); 404 problem JSON = no entity
  with this number in this register (we try P, then S; both 404 = `not_found`); 400 = malformed number (never sent);
  **204 with an empty body on OdpisAktualny = struck-off entity** (0000018507, 0000065491, 0000300472): its full
  extract has `stanPozycji = 2` and a last entry "WYKREŚLENIE Z KRAJOWEGO REJESTRU SĄDOWEGO". `stanPozycji` is
  undocumented (1 active, 2 struck off, 3 seen on the active WOŚP foundation) and only passed through.
- **Card:** name, legal form, KRS/NIP/REGON, register, seat and company address, registration date, last entry, share
  capital, main PKD, representation (organ, rule verbatim, members masked with functions), supervisory bodies, prokura
  (verbatim type), status flags from dział 6 (liquidation, bankruptcy, restructuring, dissolution, receivership:
  `entry_in_register`, `ending_entry_present`, statutory name marker such as "W UPADŁOŚCI", verbatim entries),
  struck-off status with its basis, financial statement filings (years), mergers/transformations, and the keys of any
  other dział 4/5/6 entries. Provenance: URL, `fetched_at`, `state_as_of` = `stanZDnia`, snapshot id, processing note.
- **Full extract:** every value carries entry numbers (`nrWpisuWprow`/`nrWpisuWykr`); the card of a struck-off entity is
  the state before the deletion entry, and `include_full_history` + `date` gives the register state after the last entry
  made by that date (entry dates, not event dates: board entries are declaratory).
- **Personal data:** MS masks names and PESEL in structured fields ("F*****", "5**********"); masked names are returned
  as given, never unmasked, and the masked PESEL is not returned. **Free-text fields are not masked by MS**: ORLEN's
  `rodzajProkury` carried full names with PESEL numbers. Before output every 11-digit number in free text is removed,
  the name before "PESEL" is masked, names matching a listed person's masked shape (initials + lengths) are masked, and a
  final pass drops any PESEL-valid number. Other names in free text (e.g. notaries) stay as published. Raw extracts are
  kept unchanged as local snapshots only.
- **Terms:** open API launched 2022-03-08 under the open data act, "z uwzględnieniem przepisów RODO" (gov.pl, read
  2026-10-04). No API terms found; prs.ms.gov.pl/krs/openApi is a JavaScript page that was not rendered (unverified).
  Open-data reuse conditions (source, time of creation/obtaining, processing) are met by the provenance fields.
- **Warnings returned:** an API extract does not replace an official KRS extract; no liquidation/bankruptcy entry is not
  proof of good standing; names are masked by MS.

### VAT white list (Wykaz podatników VAT, MF)
- **Endpoints (verified 2026-10-04):** `GET https://wl-api.mf.gov.pl/api/search/nip/{nip}?date=` (and `/search/regon/`),
  `GET /api/check/nip/{nip}/bank-account/{nrb}?date=` -> `accountAssigned` TAK/NIE + `requestId`. Subject fields seen:
  name, nip, statusVat, regon, pesel, krs, residenceAddress, workingAddress, representatives, authorizedClerks, partners,
  registrationLegalDate, registrationDenialBasis/Date, restorationBasis/Date, removalBasis/Date, exemptionSmeDate,
  accountNumbers, hasVirtualAccounts.
- **Quota (gov.pl/web/kas/api-wykazu-podatnikow-vat):** search 100/day, check 5000/day; then access may be blocked until
  0:00, also for the podatki.gov.pl web search. Guard: table `registry_quota` (migration 6), Europe/Warsaw day, search
  <= 80, check <= 4500, counted before sending; at the limit nothing is sent (`daily_quota_exhausted`, top-level
  `blocked`). No retries for white-list calls. HTTP 429 is assumed to mean an exhausted MF quota (not observed).
- **Date:** required, not future, <= 5 years back; validated locally. NIP checksums are validated locally (the API
  answers a bad checksum like an unknown NIP).
- **Personal data:** a subject with a PESEL or without a KRS number is treated as a possible natural person: only name,
  NIP, VAT status, town, number of accounts and registration/removal dates are returned, and only a minimised copy of the
  answer is stored. Others: representatives, clerks and partners by name only. Accounts are never listed; a caller's
  account is checked (TAK/NIE) and shown as `…1234`.
- **requestId:** MF's electronic identifier of the query (what, for which day, when); returned with a note to keep it as
  proof of the check.

### VIES (European Commission)
- **Endpoints (verified 2026-10-04):** `POST https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number`,
  `GET …/check-status`. `requester_vat` adds a consultation number (not verified live).
- **Terms (VIES disclaimer, read 2026-10-04 from the site's own text):** confirmation of VAT numbers only; extraction,
  retransmission, copying or reproduction forbidden. **Answers are never cached, stored, logged or recorded as fixtures.**

### Network log and fixtures
- 2026-10-04: about 37 KRS GETs (probes and verification, >= 2 s apart), 5 white-list searches + 1 check, 3 VIES calls
  (2 checks + 1 status), User-Agent `prawnik-mcp/0.2.0.dev0`.
- `tests/fixtures/raw/registries/krs/` (redacted copies, <= 63 KB each): current extracts of ORLEN 0000028860 (free-text
  PESEL removed, names next to them masked, e-mail replaced), PKP 0000019193, WOŚP 0000030897 (register S), Capitea
  0000413997 (restructuring ended), Getin Noble Bank w upadłości 0000304735 (bankruptcy, BFG resolution); full extract of
  struck-off PBG Avatia 0000300472; 404 and 400 problem bodies.
- `tests/fixtures/raw/registries/wl_vat/`: searches for NIP 7740001454 and 5250000251 (account numbers replaced by
  `REDACTED-ACCOUNT-nnn`), a not-found answer (9999999999), a check answer (TAK).

## NBP — average exchange rates (api.nbp.pl)

Status as of 2026-10-04. Technical description, **not legal advice**.

- **Endpoint (verified 2026-10-04):** `GET https://api.nbp.pl/api/exchangerates/rates/{a|b}/{code}/{from}/{to}/?format=json`. No key. A range without a published table answers 404 (`404 NotFound`), which also happens for a currency missing from the table.
- **How we use it:** the `exchange_rate` tool asks for the 14 days (then 45) before the event date and takes the last published average rate, i.e. the rate "z ostatniego dnia roboczego poprzedzającego" the event (art. 31a ust. 1–2 of the VAT Act, art. 11a ust. 1–3 of the PIT Act). Results are cached for 30 days; historical rates do not change.
- **Terms:** no terms beyond the API description were found; robots.txt answers 404. Rates are official NBP data. Attribution: "Źródło: Narodowy Bank Polski (api.nbp.pl)".
- **Known gaps:** only average rates (tables A and B); other statutes (e.g. the CIT Act, customs) may use other rules, so the tool returns the legal basis only for `purpose = vat | pit`.

## Deadline calculator (no external source)

`compute_deadline` applies Ordynacja podatkowa art. 12 § 1–5, Kodeks cywilny art. 111, 112 and 115 (also court civil procedure via art. 165 § 1 kpc) or KPA art. 57 § 1–4, and the list of statutory days off in art. 1 of the Act of 18 January 1951 (`eli:DU/1951/28`; 6 January since 2011-01-01, 24 December since 2025-02-01). It does not decide when a term starts, whether posting or electronic delivery kept it, or whether a term was suspended; dates before 1990-05-01 are rejected.

## Cellar — Publications Office of the EU

- **Endpoint:** `https://publications.europa.eu/resource/celex/{CELEX}` with `Accept: application/xhtml+xml` (or `text/html`) and `Accept-Language: pol`. The SPARQL endpoint is `https://publications.europa.eu/webapi/rdf/sparql`.
- **Terms:** reuse is allowed with attribution, according to the EUR-Lex reuse page (Commission Decision 2011/833/EU). This has not been legally verified.
- **Attribution:** "© European Union, https://eur-lex.europa.eu".
- **Known gaps (acts):**
  - We store the **Official Journal text** and, as a separate version, the **latest consolidated version**
    (found via SPARQL, CELEX `0YYYY…-YYYYMMDD`). Consolidations are documentation only; the OJ texts are binding.
  - Recitals and annexes are not stored as provisions.
  - The EUR-Lex website itself is not used, because it serves a bot challenge.
  - Acts are looked up by identifier only (CELEX, "dyrektywa 2011/83/UE", "RODO"), not by topic.

### CJEU case law (Court of Justice, General Court)

Verified live on 2026-09-28 with C-260/18 Dziubak (`62018CJ0260`), C-415/11 Aziz (`62011CJ0415`), C-70/17 (joined with C-179/17), T-302/21 (`62021TJ0302`) and the order C-137/25 (`62025CO0137`).

- **Documents:** CELEX sector 6. Stored: judgments (`CJ`, `TJ`, `FJ`), orders (`CO`, `TO`, `FO`) and Advocate General opinions (`CC`, flagged `advocate_general_opinion_not_a_ruling`; an opinion is not a ruling). Not stored: OJ notices (`CN`, `CA`), summaries (`_SUM`), `_RES` variants. The Civil Service Tribunal (`F-`, until 2016) is supported but rarely relevant.
- **Identifiers:** case number `C-260/18`, `T-123/20` (optional suffix `P`, `PPU`, `RX`, `R`…; Cellar prints the hyphen as U+2011, we accept it), CELEX `62018CJ0260`, ECLI `ECLI:EU:C:2019:819`. The CELEX is derived from the case number (`6` + year + type + 4-digit number: one SPARQL request checks `CJ`, `CO`, `CC` for `C-`, `TJ`, `TO` for `T-`). A joined-case group has the CELEX of its first case only; `C-179/17` is found through the group's `expression_case-law_identifier_case`. Cases before 1989 have no court letter (`26/62`, CELEX `61962CJ0026`) and are found by CELEX or ECLI.
- **Record:** `LegalDocument(kind=eu_judgment, document_id="celex:6…", celex, ecli)` plus a `Judgment` (`court_name` = "Trybunał Sprawiedliwości Unii Europejskiej" / "Sąd Unii Europejskiej"; `court_type` = `CJEU` / `GENERAL_COURT` / `CIVIL_SERVICE_TRIBUNAL`; `case_numbers` = case numbers as printed plus the base number without suffix and the ECLI, so all three find it; `judgment_type` = `SENTENCE` / `DECISION` / `OPINION`). Acts and judgments share the `celex:` prefix and the `cellar` source; they cannot collide because sector 3 (acts) and sector 6 (case law) CELEX numbers differ. `finality` stays `unknown`; General Court rulings are flagged `appeal_status_not_checked`.
- **Metadata (SPARQL, 2 requests):** date (`work_date_document`), ECLI (`case-law_ecli`), Polish title, parties, national referring court, keywords (`expression_case-law_indicator_decision`), subject-matter codes, and CELEX numbers of cited works (`work_cites_work`) and interpreted legislation (`case-law_interpretes_resource_legal`). They land in `LegalDocument.metadata` (`cited_legislation_celex`, `cited_cases_celex`, `interpreted_legislation_celex`) and may be incomplete. The citation graph does not use them yet.
- **Text (REST, 2 requests: 303 + 200):** Polish XHTML first, then Polish legacy `text/html` (older cases and many recent orders; flagged `legacy_html_format`), then English and French as a flagged fallback (`language_fallback:<lang>`, 404 per missing variant, e.g. T-1/15 has French only). If no variant exists nothing is stored. Paragraph numbers are joined to their paragraphs (`69. Dla stron…`) so "pkt 69" quotes verify. The text is documentary, not the authentic Reports.
- **Search:** case number, CELEX and ECLI lookups (1 SPARQL request). A title-word search runs only when `kinds` contains `eu_judgment`: it uses Virtuoso `bif:contains` over the Polish expression titles (party names, subject, keywords), every significant word ANDed, longer words as prefixes, newest first, about 1–2 s. A plain `FILTER(CONTAINS(...))` over the same titles took 40–60 s and is not used. **Cellar has no full-text search over the texts of the rulings**, so topic recall depends on the words in titles and keywords; a query on the reasoning finds nothing.
- **Cost of one stored document:** 4 requests (2 SPARQL, 303 + 200), plus one 404 per format/language tried before the one that exists (up to 6). Polite rate 1 req/s.
- **Fixtures:** `tests/fixtures/raw/cellar/case_*.core.json|rel.json|pol.xhtml|pol.html` (C-260/18, T-302/21, order C-137/25) and one title-search response; `sync --offline` builds them into the corpus.
- **Sync:** `prawnik-mcp sync --source cellar --celex 62018CJ0260` (repeatable; acts and case law), or `--query "C-260/18"` (stores the judgment, the AG opinion and any order of that number).

## EUREKA — tax information system of the Ministry of Finance (KIS interpretations)

- **Endpoints** (keyless public JSON API behind the EUREKA web app, host `eureka.mf.gov.pl`, verified live on 2026-09-28):
  - search: `POST https://eureka.mf.gov.pl/api/public/v1/wyszukiwarka/informacje/?size=N&page=N&sort=DT_WYD,desc&sort=ID_INFORMACJI,desc`. The body is JSON: `{"filter": {...}, "columns": [...], "searchInFullPhrase": false, "searchInContent": false, "searchInSynonyms": false, "warunkiDodatkowe": [], "searchQuery": "..."}`.
    - Filters: `SYG` (signature, exact or prefix), `KATEGORIA_INFORMACJI` (an **array** of numeric ids, `[1]` = individual interpretation), `DT_WYD_start` / `DT_WYD_end` (`YYYY-MM-DD`).
    - The response is `{"results": [...], "totalHits": n}`. Each result holds `ID_INFORMACJI`, `SYG`, `DT_WYD` (a date), `TEZA`, and `KATEGORIA_INFORMACJI` / `STATUS_INFORMACJI` as labels (e.g. `"Aktualna"`).
  - document: `GET https://eureka.mf.gov.pl/api/public/v1/informacje/{id}` returns `{id, versionId, nazwa, dokument: {fields: [{key, value}]}}`.
    - Fields: `SYG`, `DT_WYD` (UTC timestamp), `DATA_PUBLIKACJI`, `TEZA`, `TRESC_INTERESARIUSZ` (HTML body), `KATEGORIA_INFORMACJI` / `STATUS_INFORMACJI` (dictionary ids), `AUTOR` / `PRZEPISY` / `ZAGADNIENIA` / `SLOWA_KLUCZOWE` (dictionary id lists), `ZALACZNIKI`.
    - An unknown id returns **HTTP 404** with a JSON error body (`errorCode: NOT_FOUND`, "Nie znaleziono informacji o ID …, lub informacja niedostępna").
  - Human-facing URL: `https://eureka.mf.gov.pl/informacje/podglad/{id}`.
- **Upstream gotchas:**
  - The search path needs its trailing slash.
  - Omit `searchQuery` when it is empty.
  - Dictionary filters must be arrays of numbers.

  These three come from mcp-eureka's notes and were not re-tested here, to save requests.
  - **Sorting by `DT_WYD` alone is not stable across pages.** A live probe (size 2, 3 hits) returned the same id on pages 0 and 1 and never returned the third document. Adding `sort=ID_INFORMACJI,desc` returned all three in one probe; this was observed once, not proven.
  - The dictionary endpoint `pozycje-slownika/wyszukiwarka?kodSlownika=AUTOR` returns an empty list, so the authority id `70` stays unresolved.
  - No rate-limit headers or cookies were seen.
- **Terms:** no terms of use for EUREKA or its API were found (**unverified**).
  - There is no `robots.txt`: the app returns its HTML index page for that path.
  - The gov.pl KAS page about EUREKA (https://www.gov.pl/web/kas/system-informacji-celno-skarbowej-eureka, read 2026-09-28) says the search can be used without logging in. Its footer licenses gov.pl text under CC BY-SA 4.0. It is unverified whether that covers EUREKA documents.
  - `podatki.gov.pl/narzedzia/eureka` redirects from https to http and was not read.
  - Interpretations are official documents published anonymised by the authority. The copyright exclusion (art. 4 pt 2) and database rights have not been legally verified.
  - We ship no corpus. The fixtures are 3 recorded documents, all with 0 PII scan findings.
- **Attribution:** "Źródło: EUREKA – System Informacji Celno-Skarbowej, Ministerstwo Finansów (eureka.mf.gov.pl)".
- **How we use it:**
  - Each document is stored as `LegalDocument(kind=tax_ruling, document_id="eureka:<ID_INFORMACJI>")` plus a Judgment-shaped record through `Store.upsert_judgment`:
    - `court_name` = the issuing authority;
    - `court_type` = `TAX_AUTHORITY`;
    - `case_numbers` = `[SYG]`;
    - `judgment_date` = the issue date in Europe/Warsaw;
    - `judgment_type` = the category name;
    - `text` = the HTML body converted to text.

    This gives FTS indexing, signature lookup and quote verification with no schema change. Raw JSON snapshots are kept for every fetched document.
  - Live search (`search`) returns the thesis as the snippet. `fetch` stores the full document.
  - `sync_bulk` pages through a search scope (`query`, `signature`, `category_ids`, `since`, `until`, `page_size` ≤ 50). It checkpoints the page number, de-duplicates ids, and warns when fewer distinct ids than `totalHits` were seen.
- **Data-quality flags:**
  - `issue_date_missing` / `…_unparseable` / `…_in_future` / `…_implausible`, and `issue_date_local_differs_from_utc`;
  - `signature_missing`;
  - `status_unresolved` / `status_not_current`;
  - `issuing_authority_unresolved`;
  - `not_an_individual_interpretation`;
  - `attachments_not_parsed`;
  - `text_empty`;
  - `id_mismatch`.
- **Known gaps:**
  - **Interpretations are not a source of law.** An individual interpretation protects only its applicant (art. 14k–14nb Ordynacja podatkowa).
  - Search matches metadata, signature and thesis, not the full text (`searchInContent=false`).
  - The default scope is category 1 only.
  - The issuing authority is a **heuristic**: KIS signature prefix `0111–0115-KD…` means Dyrektor KIS. The `AUTOR` id is kept raw.
  - Only `STATUS_INFORMACJI` id 27 = "Aktualna" has been observed. Changes and annulments of interpretations are not linked.
  - Attachments are not parsed.
  - It is unverified whether `DT_WYD_end` is inclusive.
- **Integration still needed (outside the connector):**
  - `service.get_legal_document` returns text only for `kind == judgment`, so `tax_ruling` needs the same branch.
  - `_judgment_hit` hard-codes `SourceKind.judgment`.
  - The FTS kind mapping for `kinds=["tax_ruling"]` must map to `"judgment"` rows.
- **Attribution for code/knowledge:** endpoint paths, request body shape, field names and upstream gotchas were first documented by **matematicsolutions/mcp-eureka** (MIT License, © 2026 MateMatic / Wieslaw Mazur, commit `de3e64e`). No code is imported; the Python connector is a re-implementation.

## KIO — National Appeal Chamber (public procurement rulings, UZP portal)

Status as of 2026-09-28. Technical description, **not legal advice**. "Unverified" means no binding terms document was read.

- **Host (verified 2026-09-28):** `https://orzeczenia.uzp.gov.pl` (ASP.NET MVC portal of Urząd Zamówień Publicznych). No public API, no bulk export found.
- **Endpoints (all verified live 2026-09-28):**
  - search: `POST /Home/GetResults`, form-urlencoded. Fields: `Phrase`, `Sign` (e.g. `KIO 1550/25`), `Dt` (`DD-MM-YYYY - DD-MM-YYYY`, both ends required), `Art` (PZP provision, dictionary format), `ThIdx` (thematic index), `Kind=KIO`, `Pg` (1-based, fixed 10 results per page), `Srt` (`rank` | `date_asc` | `date_desc`), `CountStats=True`, checkboxes `Fle=1` (inflection) and `SCnt=1` (full-text), sent with a phrase. The answer is an HTML fragment: `#resultCounts` (`ALL,KIO,SO,SA,SN`), "Liczba znalezionych dokumentów: N", and `div.search-list-item` blocks with a link to `/Home/Details/{id}`. No CSRF token or cookie is needed.
  - metrics: `GET /Home/Details/{id}`. Structured metadata: issuing body, document type, issue date, chair, contracting authority, city, "Sygnatura akt / Sposób rozstrzygnięcia" (joined cases such as `KIO 1550/25 | KIO 1581/25` share one record), procedure, key PZP provisions, thematic index. It is the human-facing page, so it is stored as `original_url`. An unknown id returns **404**.
  - text: `GET /Home/ContentHtml/{id}?Kind=KIO&flection=0`. Word-export HTML. An unknown id returns **200 with an empty body**, so the connector always fetches Details first.
  - PDF: `GET /Home/PdfContent/{id}?Kind=KIO`. Only linked in metadata; never fetched.
  - `robots.txt`: none (404).
- **How we use it:**
  - Live search goes to `GetResults`, at most 3 pages.
  - `fetch("kio:<id>")` gets Details, then ContentHtml, and stores **both raw responses** as snapshots. The text snapshot is the document snapshot; the Details snapshot id is in `metadata.details_snapshot_id`.
  - Bulk sync pages `GetResults` with `Srt=date_asc`. The checkpoint cursor is the next page number.
  - A 200 response without the expected block (for example the page shell) raises an error. It is never read as "0 hits".
- **Terms actually read (2026-09-28):**
  - Home page and `/Home/Cookies` (cookie/privacy policy) only. **No terms of use or reuse licence was found.**
  - The rulings are official documents (art. 4 pt 2 of the Copyright Act). This is our reading and is **unverified**.
  - Database rights and the portal's own terms are **unverified**.
  - No rate limit is published. We use 1 req/s, as the reference implementation does.
- **Personal data:**
  - Rulings are **not fully anonymised**. Panel members (chair, recorder) and companies are named.
  - In the recorded samples, natural persons running a business appear only by initials ("S.N.", "W.S.").
  - Fixtures pass `scripts/pii_scan.py` (0 findings).
- **Attribution:** "Źródło: Krajowa Izba Odwoławcza – wyszukiwarka orzeczeń UZP (orzeczenia.uzp.gov.pl)".
- **Code provenance:** `connectors/kio.py` and `parsers/kio.py` are partly adapted from **kio-orzeczenia-mcp** (https://github.com/matematicsolutions/kio-orzeczenia-mcp, © MateMatic / Wiesław Mazur, Apache-2.0). The adapted parts are the endpoint map, the search form field mapping, label-based metadata extraction, `ł`-aware diacritics folding and Polish month names. The code was rewritten without selectolax. Both files carry an attribution comment.
- **Known gaps:**
  - Only a sample is stored. Each ruling costs 2 requests, plus 1 search request per 10 rulings.
  - The portal's HTML is not stable. UZP moved every endpoint in 2026-07 (see kio-orzeczenia-mcp DISCOVERY.md). A periodic live smoke check is advisable.
  - Some older records have no issue date (`Data wydania: -`). We store `judgment_date = null` with a flag.
  - The listing has shown future dates (in the reference repo's 2026-07 fixture, KIO 4983/25 is listed as 07-12-2026). Such dates are nulled and flagged, as for SAOS. We never substitute a date taken from the text; it is kept only as a `judgment_date_hint_from_text` flag.
  - Finality is unknown. A complaint lies to the Sąd Okręgowy w Warszawie (Sąd Zamówień Publicznych).
  - **Overlap with SAOS** (`courtType = NATIONAL_APPEAL_CHAMBER`): the same ruling can be stored as `saos:<id>` and `kio:<id>`. A case-number lookup then returns both, with status `ambiguous`.
  - The portal also holds court rulings on complaints (`Kind=SO/SA/SN`). The connector asks only for `Kind=KIO`. If the body is not KIO, the record is flagged `organ_not_kio`.
  - `Art` and `ThIdx` filters are dictionary-based and format-sensitive. For example `art. 226 ust. 1 pkt 5` matches, but plain `226` does not (per kio-orzeczenia-mcp; not re-verified).
  - Hyphenation at line ends ("wnie-\nsionego") is kept as in the source. Citation checks normalise it.
- **Network log for the fixtures:** 17 requests on 2026-09-28, at least 1.5 s apart, with User-Agent `prawnik-mcp/0.2 (+research; contact via GitHub)`. The requests were: robots.txt, 4 searches, 4 Details + 4 ContentHtml (id 28955 was not kept because it exceeds 300 KB), 2 checks for an unknown id, and 2 terms pages.
- **Fixtures** (`tests/fixtures/raw/kio/`, unmodified responses, 340 KB):
  - `search_day_p1.html`, `search_day_p2.html`: `Dt=15-05-2025 - 15-05-2025`, `Srt=date_asc`, 16 hits.
  - `search_phrase.html`: `Phrase=rażąco niska cena`, same day.
  - `search_sign.html`: `Sign=KIO 1550/25`.
  - `details_/content_` pairs for ids 28944, 28952 and 28956.
  - `details_404.html`.

### Needed changes outside this connector (not made here)

- `connectors/registry.py`: import `KioConnector` from `prawnik_mcp.connectors.kio` and add `KioConnector()` to the `conns` list.
- `sources/catalog.toml`: replace `[sources.kio]` with `docs/_pending/kio.catalog.toml`. Maturity `experimental` puts the host on the HTTP allowlist.
- `service.py`: `_CASE_RE` does not match `KIO 1550/25`, so a free-text query with a KIO case number does not trigger exact lookup or live case search. Add an alternative such as `KIO\s+\d{1,5}\s*/\s*\d{2,4}`. The "not found" message also names only SAOS.
- `NOTICE`: add the kio-orzeczenia-mcp attribution (see `uodo.md` for combined wording) and list the new fixtures (orzeczenia.uzp.gov.pl, 28.09.2026).

## UODO — decisions of the President of the Personal Data Protection Office

Status as of 2026-09-28. Technical description, **not legal advice**. "Unverified" means no binding terms document was read.

- **Host (verified 2026-09-28):** `https://orzeczenia.uodo.gov.pl`, "Portal Orzeczeń UODO". It is a react-router SSR app (`appVersion` 1.2.9 at check). It needs no key, cookie or JavaScript challenge.
- **Endpoints (verified live 2026-09-28):**
  - search: `GET /search.data?dcr=rodo&q=<text>&rn=<case number>&dtps=<YYYY-MM-DD>&dtpe=<YYYY-MM-DD>&page=N`.
    - `q` is full text. `rn` is the case number (exact match observed). `dtps`/`dtpe` are the **publication** date window. `dcr=rodo` means GDPR-era decisions.
    - Page size is fixed at 10. The response echoes the filters in `values`, and returns `itemsCount`, `pages {current,total,size}` and `order`.
    - Ordering is `rank` for text queries and otherwise `dateAnnouncement` descending.
    - Each item has `refid` (URN), `refname` (case number or numbers), `name`, `title` (subject), `dates` (announcement / validation / publication / defended / repealed, each with a status) and `terms` (keywords, pl/en).
  - decision: `GET /document/{urn}/content.data`. One response carries `urn`, `refname`, `status` (`final` / `nonfinal` / `repealed`), `statusHint`, `dates` and `body` (HTML: nested `dl/dt/dd`, footnotes in `div.glosses`). This response is the stored snapshot.
    - **An unknown URN returns HTTP 200** with an empty `refname`/`body` and `status: "unknown"`. The connector raises `NotFoundUpstream` and stores nothing.
    - A real HTTP 404 is returned for unknown routes.
  - human-facing page: `GET /document/{urn}/content` (SSR HTML with tabs Treść / Metryka / Orzeczenia / Akty prawne / Historia). It is used as `original_url`. `/document/{urn}.data` answers with a 202 `SingleFetchRedirect` to `/content` and is not used.
  - The response format is react-router "turbo-stream": one JSON array of values referenced by index. `parsers/uodo.py::decode_turbo_stream` rebuilds plain JSON. The fixtures are stored with the `.json` extension; each is a single valid JSON array, byte-identical to the response.
  - `robots.txt`: none (404). `/` redirects (302) to `/search`.
- **Not verified (from kio-orzeczenia-mcp SOURCES.md, not used):**
  - the snippet endpoint `/api/documents/public/items/{urn}/snippet.html?query=...&column=content_pl`;
  - suggestions at `/webapi/suggest?q=`;
  - the status filter `s`;
  - any decision-date filter parameters;
  - `dcr` values other than `rodo`.
- **Ids:** `uodo:<year>:<code>` is the URN tail. For example `urn:ndoc:gov:pl:uodo:2022:dkn_5112_28` becomes `uodo:2022:dkn_5112_28`. Case numbers go to `case_numbers`, e.g. `DKN.5112.28.2022`. `refname` can list several numbers, e.g. `ZSPR.421.2.2019 ZSPR.405.67.2019` for a re-decision.
- **Mapping:**
  - `Judgment.court_name` is "Prezes UODO" and `court_type` is `DATA_PROTECTION_AUTHORITY`.
  - `judgment_date` is the announcement date.
  - `finality` comes from the portal status: `final` → final, `nonfinal` → not_final, anything else → unknown. `repealed` also adds the flag `decision_repealed_by_court`.
  - `LegalDocument.kind` is `decision`.
  - Metadata holds the publication date, the date the decision became final, court URNs from the dates, and the listing's subject/keywords when the decision was stored from a search.
- **Terms actually read (2026-09-28):**
  - We read only the portal footer: "© UODO 2018 - 2025 Wszelkie prawa zastrzeżone." (all rights reserved). **No terms of use or reuse licence was found on the portal.**
  - The privacy-policy link (uodo.gov.pl, a different host) was **not read**.
  - Decisions are official documents (art. 4 pt 2 of the Copyright Act) and are published anonymised by UODO. This is our reading and is **unverified**.
  - Whether the "all rights reserved" notice covers the database or portal content is **unverified**.
  - No rate limit is published. We use ≤1 req/s.
  - kio-orzeczenia-mcp notes that its authors chose to notify UODO and wait 14 days before publishing their UODO connector. That is their own policy, not a UODO requirement, but it is worth considering before promoting this source beyond `experimental`.
- **Personal data:** the samples are pseudonymised at the source ("G. R. prowadzącego działalność gospodarczą pod firmą H. (…)"). Fixtures pass `scripts/pii_scan.py` (0 findings). The SSR page was **not** kept as a fixture, because its footer contains the office's contact data.
- **Attribution:** "Źródło: Prezes UODO – Portal Orzeczeń UODO (orzeczenia.uodo.gov.pl)".
- **Code provenance:** the endpoint map comes from the ledger in kio-orzeczenia-mcp `SOURCES.md` (https://github.com/matematicsolutions/kio-orzeczenia-mcp, © MateMatic, Apache-2.0). No code was copied. The turbo-stream decoder and the parser are original, and the modules carry an attribution comment. The uodo-orzeczenia-mcp repository mentioned there is not public.
- **Known gaps:**
  - The `.data` endpoints are internal to the web app, undocumented, and can change with any portal release.
  - Only GDPR-era decisions (`dcr=rodo`) are searched. Pre-2018 (GIODO) coverage is not assessed.
  - `since`/`until` in bulk sync filter the **publication** date. `date_from`/`date_to` in live search are applied locally to the returned page, so a hit list can be shorter than `limit`.
  - Without a text query the order is by decision date, newest first. New decisions shift pages, so bulk sync should use a closed past window.
  - The listing is the only source of subject and keywords, so a decision fetched directly by id has none.
  - Court challenges appear only as URN references (`court_refs`). They are not fetched or linked to CBOSA.
  - No snippets: the portal's snippet endpoint (one extra request per hit) is not used; the subject serves as the snippet.
- **Network log for the fixtures:** 13 requests on 2026-09-28, at least 1.5 s apart, with User-Agent `prawnik-mcp/0.2 (+research; contact via GitHub)`. They were: robots.txt, 5 searches, 3 `content.data`, 1 `.data` redirect probe, 1 SSR page, 1 unknown-URN check, and `/`.
- **Fixtures** (`tests/fixtures/raw/uodo/`, unmodified responses, 328 KB):
  - `search_sklep.json`: `q=sklep internetowy`.
  - `search_window_p1.json`, `search_window_p2.json`: `dtps=2025-04-01&dtpe=2025-09-30`, 13 hits.
  - `search_rn.json`: `rn=DKN.5112.28.2022`.
  - `content_2022_dkn_5112_28.json`, `content_2022_dkn_5110_14.json`, `content_2019_zspr_405_67.json`.
  - `content_404.json`: empty placeholder for an unknown URN.

### Needed changes outside this connector (not made here)

- `connectors/registry.py`: import `UodoConnector` from `prawnik_mcp.connectors.uodo` and add `UodoConnector()` to the `conns` list.
- `sources/catalog.toml`: replace `[sources.uodo]` with `docs/_pending/uodo.catalog.toml`.
- `service.py`:
  - `_CASE_RE` does not match UODO case numbers. Add an alternative such as `[A-Z]{2,6}(?:\.\d{1,5}){2,4}\.(?:19|20)\d{2}`.
  - The live case-number search passes `kinds={"judgment"}`, which excludes `decision` sources. Use `{k.value for k in RECORD_KINDS}` or add `"decision"`. The local path already handles decisions via `RECORD_KINDS`.
- `NOTICE`, proposed wording:
  > - Parts of `connectors/kio.py` and `parsers/kio.py` are adapted from matematicsolutions/kio-orzeczenia-mcp (Apache License 2.0, © MateMatic / Wiesław Mazur): KIO endpoint map, search form field mapping, label-based metadata extraction. The UODO portal endpoint map was taken from the same repository's SOURCES.md. Modifications: rewritten for this project (stdlib parsing, sync client, local store).
  > - Test fixtures in tests/fixtures/raw/kio and tests/fixtures/raw/uodo are unmodified responses from orzeczenia.uzp.gov.pl (Urząd Zamówień Publicznych / KIO) and orzeczenia.uodo.gov.pl (Prezes UODO), recorded 28.09.2026. They are not covered by the code licence.

## CBOSA — Centralna Baza Orzeczeń Sądów Administracyjnych (NSA + 16 WSA)

Connector: `src/prawnik_mcp/connectors/cbosa.py`. Parser: `src/prawnik_mcp/parsers/cbosa.py`.
Tests: `tests/test_connector_cbosa.py` (offline). Samples: `tests/fixtures/raw/cbosa/` (3 judgment pages +
`robots.txt`, with `manifest.json` provenance). Proposed catalog entry: `docs/_pending/cbosa.catalog.toml`.
Maturity: **experimental**.

**Scope: single judgments by id only.** robots.txt disallows `/cbo/search` and `/cbo/find` for every
user agent, and the connector honours that unconditionally:

- `supports_search = False`. `search()` raises `NotImplementedError` and makes no request.
- `sync_bulk()` returns `ok=False` with the warning "CBOSA robots.txt disallows /cbo/search and /cbo/find; only single /doc/{hex} fetches are supported", and makes no request.
- `fetch("cbosa:<HEX>")`, lazy fetch through `get_legal_document`, `sync_defaults` (catalog `doc_ids`) and `sync_offline` are supported.

### Endpoints

Host `orzeczenia.nsa.gov.pl`, verified live on 2026-09-28. HTTPS certificate verification succeeded with httpx/certifi; mcp-nsa's `rejectUnauthorized: false` workaround was not needed.

- **Used — judgment:** `GET /doc/{HEX}` (10 uppercase hex characters) returns 24–46 KB of HTML (UTF-8).
  - `<TITLE>` = "{sygn} - {Rodzaj} {NSA | WSA w X} z {date}". The header is `<span class="war_header">`.
  - Metadata rows `<td class="lista-label">…</td>` + `<td class="info-list-value">`:
    - Data orzeczenia (+ italic "orzeczenie prawomocne" / "orzeczenie nieprawomocne");
    - Data wpływu;
    - Sąd (full name);
    - Sędziowie (`<br/>`-separated, with roles);
    - Symbol z opisem;
    - Hasła tematyczne;
    - Sygn. powiązane (links to other `/doc/` ids);
    - Skarżony organ;
    - Treść wyniku;
    - Powołane przepisy (ISAP links + `<span class='nakt'>` act titles).
  - Body sections: `<div class="lista-label">Sentencja|Uzasadnienie|…</div><span class="info-list-value-uzasadnienie">`.
  - Samples:
    - `9FF3766DA4`: NSA, III OSK 6859/21, final;
    - `3BB1F6C423`: WSA w Łodzi, II SAB/Łd 23/25, final;
    - `4DC5BD4680`: WSA w Warszawie, II SA/Wa 1553/24, **nieprawomocne**.
- **robots.txt** (recorded in fixtures, and checked by a test with `urllib.robotparser`):
  - `User-agent: *` disallows `/cbo/search`, `/cbo/find`, `/cbo/do/search`, `/cbo/do/find`, `/cbo/do/doc`, `/cbo/pow`, `/cbo/wsp`, `/cbo/*.txt`, `/cbo/*op=file`, `/doc/*.txt`, `/servlet`.
  - `MSNbot` and `GPTBot`: `Disallow: /`.
  - `Sitemap: http://orzeczenia.nsa.gov.pl/sitemap.xml`.
- **Not used:**
  - **Search:** `POST /cbo/search` takes text-valued selects, e.g. `sad=dowolny`. Next pages are `GET /cbo/find?p=N`, with the query bound to the JSESSIONID session. Both were observed working on 2026-09-28, before robots.txt was read, but both are disallowed by robots.txt.
  - **sitemap.xml:** an index of 3 `.xml.gz` files, all with lastmod 2009-06-28. That is at most 150k URLs, against about 2.39M judgments. It is stale, so no robots-compliant discovery path exists.

### Request log

10 real requests in total. All were HTTP 200; there were no 403s and no retries, and requests were at least 3 s apart:

1. `GET /cbo/query`
2. `POST /cbo/search`
3. `GET /cbo/find?p=2`
4. `POST /cbo/search`
5. `GET /cbo/find?p=2`
6. `GET /doc/9FF3766DA4`
7. `GET /doc/3BB1F6C423`
8. `GET /doc/4DC5BD4680`
9. `GET /robots.txt`
10. `GET /sitemap.xml`

Mistake: robots.txt was read *after* requests 2–5, which went to paths it disallows. Nothing further was sent to a disallowed path. The recorded list pages were deleted, and the connector no longer contains that code.

### Terms, as actually read (status: **unverified**)

- **Search form (`/cbo/query`) notice, verbatim:** "Naczelny Sąd Administracyjny informuje, iż udostępniona w Internecie baza orzeczeń służy wyłącznie celom informacyjnym oraz edukacyjnym i nie ma statusu zbioru urzędowego. Znajdujące się w niej orzeczenia są anonimizowane z uwzględnieniem celów wynikających z przepisów ustawy o ochronie danych osobowych i innych regulacji szczególnych."
- **robots.txt**: as above, and followed.
- **Not found / not read:**
  - no reuse licence, regulamin or terms of service on the pages read;
  - `/instrukcja.html`, nsa.gov.pl's site terms and the accessibility declaration were not read.

  Judgments are official documents (art. 4 pt 2 of the Copyright Act). Database rights have not been legally verified. We ship no corpus: 3 recorded judgments with 0 PII scan findings; anonymisation is at the source, and the one e-mail is the placeholder `[...]@[...].com`.

### Ban risk and how the connector limits it

The mcp-nsa server instructions (`src/index.ts`) record the incident: on 2026-07-19, continuous traffic at 2 req/s led to a full IP ban, with 403 on every path including `/doc` and `/cbo/query`. The same notes say 0.5 req/s ran for 10 h without problems. The connector limits the risk as follows:

- **Catalog rate:** `rate_per_s = 0.5`, so `PoliteClient` waits 2 s per host.
- **Process-wide gate:** `live.py` creates a new `PoliteClient` for each lazy fetch, and per-client delays do not span clients. So every CBOSA request also goes through one connector-instance gate: requests are serialised and at least 2 s apart.
- **403 handling:** a 403 raises `SourceUnavailable` and stops the run. The instance then refuses all CBOSA requests for 1 h, without touching the network.
- **Unexpected pages:** a page without judgment metadata (a block page or a template change) is reported as `source_unavailable` and never stored.
- **Volume:**
  - `sync_defaults` fetches at most 20 ids;
  - there is no bulk mode;
  - already snapshotted pages are re-parsed, not re-fetched.
- **Remaining risk:** `PoliteClient` itself still retries 429/5xx up to 3 times with backoff, which this connector cannot change.

### How it is stored

Each judgment is stored as `Judgment` + `LegalDocument(kind=judgment)` with `document_id = "cbosa:<HEX>"`.

- **Judgment fields:**
  - `court_name`: the "Sąd" row. The short form "NSA" / "WSA w Łodzi" goes into `metadata.court_short`.
  - `court_type`: `ADMINISTRATIVE`, as in SAOS.
  - `case_numbers`: from the header.
  - `judgment_date`: from "Data orzeczenia", cross-checked with `<TITLE>`.
  - `judgment_type`: SENTENCE / DECISION / RESOLUTION; the Polish name is in metadata.
  - `finality`: `final` / `not_final` / `unknown`. `metadata.finality_as_of` holds the fetch date.
  - `original_url`: `https://orzeczenia.nsa.gov.pl/doc/<HEX>`.
  - `text`: the body sections with their headings.
- **Metadata:**
  - judges, symbols, keywords, related judgments (as `cbosa:` ids that can be fetched next), challenged authority, outcome;
  - legal bases, as text lines plus structured `{publication, provisions, act, isap_url}`;
  - the CBOSA disclaimer.
- **Snapshots:** every fetched page is snapshotted.
- **Stored text:** page text is data and is never interpreted as instructions. Scripts, styles and comments are dropped.
- **Data-quality flags:**
  - `case_number_missing`;
  - `judgment_date_missing` / `_missing_in_metadata` / `_unparseable` / `_in_future` / `_implausible` / `_title_mismatch`;
  - `court_from_title`, `court_missing`;
  - `text_empty`.

### Gaps / unverified

- No discovery: the user must supply a `cbosa:<hex>` id. Ids can also come from related-judgment links or `defaults.doc_ids`. Case-number lookup only covers judgments already stored locally.
- The response for a non-existent `/doc/` id was not observed. Such a page is handled defensively.
- Coverage (about 2.39M judgments, 1981–today) is mcp-nsa's figure and was not re-measured.
- Pre-2004 judgments and theses (`Tezy`) were not sampled. The parser handles any body section generically.

### Attribution

The connector ports material from **mcp-nsa** (https://github.com/matematicsolutions/mcp-nsa, MIT License, Copyright (c) 2026 MateMatic / Wieslaw Mazur), re-implemented in Python with no code copied verbatim:

- the `/doc/` URL scheme;
- the HTML metadata label contract;
- the ban report.

mcp-nsa credits `worldwidelaw/legal-sources` (sources/PL/NSA, MIT). The attribution comment is in both modules.

Proposed NOTICE lines:

```
- CBOSA (orzeczenia.nsa.gov.pl) URL scheme and HTML label contract were ported from
  matematicsolutions/mcp-nsa (MIT License, Copyright (c) 2026 MateMatic / Wieslaw Mazur);
  re-implemented in Python, no code copied verbatim.
- Test fixtures in tests/fixtures/raw/cbosa are unmodified responses from orzeczenia.nsa.gov.pl
  (28.09.2026; CBOSA — baza informacyjna, nie zbiór urzędowy). They are not covered by the code licence.
```

### Needed changes outside this connector's files

1. **`src/prawnik_mcp/sources/catalog.toml`:** replace `[sources.cbosa]` with `docs/_pending/cbosa.catalog.toml`.
2. **`src/prawnik_mcp/connectors/registry.py`:** add `CbosaConnector()` in the same change (`tests/test_catalog.py`).
3. **`NOTICE`:** add the lines above.
4. **`src/prawnik_mcp/service.py` `_CASE_RE`:** it does not match WSA signatures (`II SA/Wa 1553/24`, `II SAB/Łd 23/25`), so local case-number lookup misses WSA judgments. Suggested pattern, with no regressions on NSA and common-court signatures:
   `r"\b([IVXL]{1,5}\s+[A-Za-zŁłŻż]{1,6}(?:/[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{1,3})?(?:-[A-Za-z]+)?\s+\d{1,6}/\d{2,4})\b"`.
5. **Optional, `http.py`:** move the process-wide per-host spacing and the 403 circuit breaker into `PoliteClient`, so that every ban-prone source gets them.

### Verification

`tests/test_connector_cbosa.py` has 11 tests, all offline over `httpx.MockTransport` and the recorded pages. They cover:

- the robots.txt rules;
- `search` and `sync_bulk` making **zero** HTTP requests;
- metadata of all 3 judgments (court, case number, date, finality);
- fetch with snapshot, and reuse of the snapshot;
- a 403 raising `SourceUnavailable`, with no further requests even through a fresh client;
- a block page not being stored;
- spacing between requests across clients;
- `sync_offline`;
- `sync_defaults` resuming from snapshots after a 403.

`ruff check` is clean, `pii_scan` finds 0, and the full suite passes (250).
