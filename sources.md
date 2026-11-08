# Source registry and citation policy

Registry file: `data/00_sources/sources.csv`

Current snapshot: **17 sources** — **12 official** and **5 Groww seeds**. Of the 12 official pages, **9 are citable answer sources** and **3 are non-citable official cross-checks**. All sources were loaded and cleaned on **2026-11-07**.

## Policy

- Only official HDFC AMC, SEBI, AMFI, and registrar pages may be cited.
- Groww URLs are **seed/cross-check only** and are never citable.
- Every factual answer must have exactly one citation from an approved, citable official source.
- Citation URLs always point to the official page, never to a reader/proxy URL.
- HDFC pages are fetched through the public `r.jina.ai` reader only as transport because the origin blocks direct requests from this environment. The official HDFC URL remains the canonical citation target.
- Performance, return, CAGR, ranking, NAV-history, portfolio, and fund-manager content is removed from the corpus by the cleaners.
- Source rows with `status=approved` and `citable=true` are indexed in the main Chroma collection. Seed rows are inspected separately and never queried for answers.

## Official citable sources (9)

| Source ID | Publisher | Type | Purpose | URL |
|---|---|---|---|---|
| `official_hdfc_amc` | HDFC AMC | amc | AMC overview, investor count, SEBI registration | <https://www.hdfcfund.com/> |
| `official_hdfc_large_cap` | HDFC AMC | amc | Large Cap scheme facts | <https://www.hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct> |
| `official_hdfc_flexi_cap` | HDFC AMC | amc | Flexi Cap scheme facts | <https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct> |
| `official_hdfc_elss` | HDFC AMC | amc | ELSS scheme facts and lock-in | <https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct> |
| `official_hdfc_mid_cap` | HDFC AMC | amc | Mid Cap scheme facts | <https://www.hdfcfund.com/explore/mutual-funds/hdfc-mid-cap-fund/direct> |
| `official_hdfc_statement` | HDFC AMC | amc | Account-statement download process | <https://www.hdfcfund.com/services/consolidated-account-statement> |
| `official_hdfc_request_statement` | HDFC AMC | amc | Capital-gains/account statement request options | <https://www.hdfcfund.com/services/additional-info/request-statement> |
| `official_sebi_investor_education` | SEBI | sebi | Official investor-education support for advice/PII refusals | <https://investor.sebi.gov.in/> |
| `official_hdfc_factsheets` | HDFC AMC | amc | Official factsheet index for performance refusals | <https://www.hdfcfund.com/mutual-funds/factsheets> |

## Official non-citable cross-checks (3)

These pages are official and were loaded/cleaned, but they are not used as answer citations. They provide cross-check context only.

| Source ID | Publisher | Type | Purpose | URL |
|---|---|---|---|---|
| `official_hdfc_ter_reports` | HDFC AMC | amc | Total expense ratio reports | <https://www.hdfcfund.com/statutory-disclosure/total-expense-ratio-of-mutual-fund-schemes/reports> |
| `official_hdfc_services` | HDFC AMC | amc | Services index cross-check | <https://www.hdfcfund.com/services> |
| `official_hdfc_disclosures` | HDFC AMC | amc | Statutory disclosures cross-check | <https://www.hdfcfund.com/statutory-disclosure> |

## Groww seed/cross-check sources (5, never citable)

| Source ID | Publisher | Type | Purpose | URL |
|---|---|---|---|---|
| `seed_hdfc_amc` | Groww | seed | AMC overview seed | <https://groww.in/mutual-funds/amc/hdfc-mutual-funds> |
| `seed_hdfc_large_cap` | Groww | seed | Large Cap seed | <https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth> |
| `seed_hdfc_flexi_cap` | Groww | seed | Flexi Cap seed | <https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth> |
| `seed_hdfc_elss` | Groww | seed | ELSS seed | <https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth> |
| `seed_hdfc_mid_cap` | Groww | seed | Mid Cap seed | <https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth> |

## Coverage matrix

Verified facts in `data/03_chunks/facts.jsonl`:

| Scheme | Expense ratio | Exit load | Minimum SIP | Lock-in | Riskometer | Benchmark |
|---|---|---|---|---|---|---|
| HDFC Large Cap Fund Direct Growth | ✅ | ✅ | ✅ | n/a | ✅ | ✅ |
| HDFC Flexi Cap Direct Plan Growth | ✅ | ✅ | ✅ | n/a | ✅ | ✅ |
| HDFC ELSS Tax Saver Fund Direct Plan Growth | ✅ | ✅ | ✅ | ✅ 3 years | ✅ | ✅ |
| HDFC Mid Cap Fund Direct Growth | ✅ | ✅ | ✅ | n/a | ✅ | ✅ |

AMC-level verified facts:

| Fact | Value |
|---|---|
| AMC overview | HDFC Mutual Fund; 1.7 Crore+ investors; SEBI registration `MF/044/00/6` |
| Statement process | Choose PAN or Folio, send OTP, select duration, click `SEND EMAIL` |
| Capital-gains statement | Listed as an option on the official request-statement page |

The corpus has no missing required facts: `missing_facts: []` in `data/03_chunks/chunk_report.json`.

## Citation resolution

`src/citation_resolver.py` rejects a citation when the source is:

- not `citable=true`
- not `status=approved`
- not an official type (`amc`, `sebi`, `amfi`, `registrar`)
- not `https`
- outside the allowlist
- mismatched to the requested scheme

If no safe citation resolves, the assistant returns a no-answer and does not invent a source.

## Refusal support sources

| Refusal intent | Support citation |
|---|---|
| Advice | SEBI Investor Education |
| PII | SEBI Investor Education |
| Performance/returns | HDFC factsheet index |
| Out of scope | HDFC AMC overview |

Refusals never call the LLM and never include performance numbers.
