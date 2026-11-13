# HDFC Mutual Fund Facts Assistant

A local, facts-only RAG demo for public HDFC Mutual Fund information. It answers a small set of factual questions about HDFC Mutual Fund and four direct-growth schemes, always shows exactly one official source, and refuses investment advice, performance/return questions, rankings, and personal data.

**Facts-only. No investment advice.** See [DISCLAIMER.md](DISCLAIMER.md).

## What it covers

- HDFC Mutual Fund overview
- HDFC Large Cap Fund Direct Growth
- HDFC Flexi Cap Direct Plan Growth
- HDFC ELSS Tax Saver Fund Direct Plan Growth
- HDFC Mid Cap Fund Direct Growth

Required facts: expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, and the statement-download process. The assistant also answers the AMC's SEBI registration number and investor count.

Every factual answer has:

- at most **3 sentences**
- exactly **one official citation**
- `Last updated from sources: YYYY-MM-DD`

## Current status

| Item | Value |
|---|---|
| Sources | 17 total: 12 official + 5 Groww seeds |
| Citable official sources | 9 |
| Non-citable official cross-checks | 3 |
| Chunks | 120 (61 citable) |
| Fact cards | 45 (23 verified, 22 seed-only) |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions |
| Vector store | Chroma `mf_facts_v1_bc14e60c4766` |
| Gold set | 25 questions, **25/25 passed** |
| Local LLM | Gemini `gemini-3.5-flash-lite` via `.env` (key not committed) |
| UI | Streamlit: FundFacts chat, topic sidebar, inline sources, developer views |

The latest evaluation is in [eval_report.md](eval_report.md); generated artifacts are in `data/08_eval/`.

## Safety rules

- No buy/sell/hold, portfolio, ranking, tax, or performance/return advice.
- No performance numbers or return comparisons in the answer corpus.
- PAN, Aadhaar, account/folio numbers, OTPs, emails, and phone numbers are blocked before retrieval, logging, or generation.
- Groww pages are seed/cross-check only and are never cited.
- Only official HDFC AMC and SEBI pages are citable.
- The UI has no PII fields, uploads, login, persistent chat history, system prompt, hidden reasoning, or secrets.

## Quickstart

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set one provider and model in `.env`, or leave the model blank to use deterministic extractive answers. Example:

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
GEMINI_API_KEY=your-key-here
```

Do not commit `.env`; it is ignored by git.

Build and verify the corpus:

```bash
python -m src.cli check
python -m src.cli validate-sources
python -m src.cli ingest --stage all
python -m src.cli smoke
python -m src.cli eval
python -m pytest -q
```

Run the UI:

```bash
streamlit run app.py
```

Open `http://localhost:8501`.

## Deploy on Render

The repository includes the prebuilt retrieval artifacts under `data/03_chunks/`, `data/04_embeddings/`, `data/05_vector/`, and `data/08_eval/`, so a Render deploy does not need to re-run ingestion. Raw HTML snapshots and query logs remain local-only.

Use a Render Blueprint with [render.yaml](render.yaml), or create a Web Service with these settings:

| Setting | Value |
|---|---|
| Root Directory | Leave blank (repository root) |
| Build Command | `pip install --upgrade pip && pip install -r requirements.txt` |
| Start Command | `streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true` |
| Health Check Path | `/_stcore/health` |

Set these environment variables:

```text
PYTHON_VERSION=3.12.8
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
GEMINI_API_KEY=<your-secret-key>
```

Keep `GEMINI_API_KEY` as a Render secret. The free 512 MB plan may not have enough memory for Streamlit, PyTorch, MiniLM, and ChromaDB; use a 2 GB instance if the service runs out of memory.

## CLI

| Command | Purpose |
|---|---|
| `python -m src.cli check` | Validate config, paths, collection, and LLM settings |
| `python -m src.cli validate-sources` | Check source counts, official sources, allowlist, and coverage |
| `python -m src.cli ingest --stage load` | Fetch raw snapshots |
| `python -m src.cli ingest --stage clean` | Extract safe visible text |
| `python -m src.cli ingest --stage chunk` | Build chunks and fact cards |
| `python -m src.cli ingest --stage embed` | Embed chunks with MiniLM |
| `python -m src.cli ingest --stage index` | Build the Chroma collections |
| `python -m src.cli ingest --stage all` | Run all ingestion stages |
| `python -m src.cli smoke` | Query one fact per scheme |
| `python -m src.cli retrieve "question"` | Inspect guardrails, routing, retrieval, and the evidence gate |
| `python -m src.cli answer "question"` | Run one end-to-end answer |
| `python -m src.cli eval` | Run the 25-question gold set |
| `python -m src.cli report` | Show artifact and answer counts |
| `python -m src.cli clean-logs` | Delete retrieval and answer logs |

## Documentation

| Document | Contents |
|---|---|
| [architecture.md](architecture.md) | Components, data flow, retrieval, validation, UI architecture, evaluation, deployment |
| [implementation.md](implementation.md) | Build order, module notes, guardrails, UI implementation, acceptance checklist |
| [prd.md](prd.md) | Product requirements and acceptance criteria |
| [sources.md](sources.md) | Source registry, citation policy, and coverage |
| [sample_qa.md](sample_qa.md) | Representative factual answers and refusals |
| [eval_report.md](eval_report.md) | Latest gold-set report |
| [DISCLAIMER.md](DISCLAIMER.md) | Legal and usage disclaimer |

## UI design reference

The UI follows the linked Lovable prototype at (insert link): a 300 px FundFacts sidebar with the shield logo, New chat, two stat cards, `EXPLORE A TOPIC` pills, and a `WHY TRUST IT` checklist; a centered hero with three suggestion buttons; a gradient facts-only banner; mint user bubbles and white assistant cards with an inline source URL and last-updated date; and a fixed rounded composer with a 500-character input and round green send button.

The Streamlit implementation is in `app.py`, `src/ui/ask.py`, and `src/ui/common.py`; the lavender/green theme is in `.streamlit/config.toml`; the logo is `assets/logo.svg`. The linked prototype does not include a dark-mode toggle, RECENT history, source-card expander, or copy/feedback controls, so those were removed to match it. Pipeline, Sources, and Evaluation remain available through the collapsed System status expander in the sidebar. See [architecture.md](architecture.md) §9.6, [implementation.md](implementation.md) §9.9, and `design/README.md`.

The older StitchWithGoogle export was never present in the workspace and is not required for the restyle.
## Limitations

- Local demo only; no hosted public link or recording is included.
- The corpus is intentionally small and limited to the approved source registry.
- Factual values can change at the official source; the date on each answer tells you when it was retrieved.
- The assistant does not retrieve or display personal statements, folios, PANs, or account data.
- Capital-gains statement support points to the official HDFC request-statement page; the assistant does not perform the download.

## Repository layout

```text
app.py                  Streamlit entry point
config.yaml             Non-secret configuration
src/                    Ingestion, retrieval, generation, validation, UI
data/00_sources/        Source registry
data/01_raw/            Raw snapshots
data/02_clean/          Clean documents
data/03_chunks/         Chunks and fact cards
data/04_embeddings/     Embedding artifacts
data/05_vector/         Chroma collections and reports
data/06_retrieval/      Retrieval traces
data/07_answers/        Answer artifacts
data/08_eval/           Gold set, results, metrics, report
tests/                  Unit and integration tests
design/                 Lovable restyle notes
```
