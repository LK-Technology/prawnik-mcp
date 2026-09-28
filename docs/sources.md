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
  - Coverage and freshness have not been measured.

## Cellar — Publications Office of the EU

- **Endpoint:** `https://publications.europa.eu/resource/celex/{CELEX}` with `Accept: application/xhtml+xml` and `Accept-Language: pol`. The SPARQL endpoint is `https://publications.europa.eu/webapi/rdf/sparql`.
- **Terms:** reuse is allowed with attribution, according to the EUR-Lex reuse page (Commission Decision 2011/833/EU). This has not been legally verified.
- **Attribution:** "© European Union, https://eur-lex.europa.eu".
- **Known gaps:**
  - We store the **original Official Journal text, not the consolidated version**.
  - Recitals and annexes are not stored as provisions.
  - The EUR-Lex website itself is not used, because it serves a bot challenge.
