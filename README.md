# RUPP Senior Project Knowledge Base (RUPP-SPKB v2.0)

A Telegram bot that lets RUPP students search past senior projects by topic, look up a senior by
name, open a senior's portfolio, browse by year or program, and contact a senior only when that
senior agreed. Answers quote the project documents with `[Project: <title>, p.<n>]` citations.

**Status:** Phases 1–7 are implemented and tested on a **synthetic** fixture corpus
(`tests/fixtures/`). No real senior data exists yet: `data/projects.csv` and `data/seniors.csv`
contain headers only, and `data/corpus/` is empty. The old DSE curriculum assistant is kept in
`legacy/dse-curriculum/` as reference only ([ADR-001](docs/adr/ADR-001-product-scope.md)).

## How it works

```text
 data/projects.csv + seniors.csv + corpus/<project_id>/   (the manifest is the source of truth)
          │
          ▼  python -m ingestion.run
 validate ─► parse (PDF/OCR, DOCX, PPTX, MD) ─► chunk ─► bge-m3 dense + sparse ─► Qdrant
          └────────────────────────────────────────────────────────────────────► SQLite (seniors, projects, aliases)

 Telegram / CLI ─► answer.service.answer()  (ONE call per interaction)
                     ├─ route: topic · senior · portfolio · facet · question
                     ├─ topic/question: hybrid search (RRF, grouped by project) ─► relevance gate
                     │       ├─ below the gate: abstain (no LLM)
                     │       └─ above: extractive passages (default) or Gemma 4 with checked citations
                     ├─ senior/portfolio: alias ─► full-text ─► fuzzy name match ─► portfolio (SQLite)
                     └─ facet: filter inside Qdrant + counts from SQLite
```

## Setup (Windows, PowerShell)

```powershell
# Python 3.11
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"            # pytest, ruff, black (pinned in pyproject.toml)

# Settings: copy the template and fill in TELEGRAM_BOT_TOKEN and KB_SALT (never commit .env)
Copy-Item .env.example .env

# Qdrant server (vector store)
docker compose up -d qdrant

# Models
# - bge-m3 downloads from Hugging Face on first use (~2.3 GB).
# - Gemma 4 is only needed for ENABLE_LLM=true:
docker compose --profile llm up -d ollama
docker compose exec ollama ollama pull gemma4:12b-it-qat
```

OCR for scanned PDFs needs the Tesseract program (`winget install UB-Mannheim.TesseractOCR`) with
the English and Khmer language data; without it, scanned PDFs are reported as `failed`.

## Everyday commands

```powershell
# 1. Check and ingest the manifest + documents
python -m ingestion.validate_manifest --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus
python -m ingestion.run --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus --rebuild
python -m ingestion.verify

# 2. Evaluate retrieval (90-query golden set) and compare with the committed baseline
python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold
python -m eval.compare_baseline eval/baseline.json eval/last_report.json

# 3. Run the bot (long polling) — or check readiness without contacting Telegram
python -m bot.main --check
python -m bot.main

# 4. Offline demo without Telegram (same answer() as the bot)
python cli.py --query "khmer ocr"
python cli.py --query "who built a Khmer OCR system" --llm

# 5. Before a demo: twelve go/no-go checks (never sends a Telegram message)
python scripts/pre_demo_check.py

# 6. Export training pairs from the query log (clicks and ratings)
python -m datasets.build_training_pairs --db data/kb.sqlite3 --out datasets/training_pairs.jsonl
```

`--db`, `--qdrant-url`, and `--qdrant-path` point the commands at another SQLite file or Qdrant
index. `--qdrant-path` opens an embedded Qdrant folder: fine for smoke tests, never for evaluation.

**Docker:** `docker compose up -d bot` runs the bot with the settings from `.env`
(`docker-compose.yml`, `Dockerfile`). Data and logs stay in `./data` and `./logs` on the host.

## Bot commands

| Command | What it does |
|---|---|
| `/start`, `/help` | Help; `/start senior_<id>` and `/start proj_<id>` are shareable deep links |
| plain text, `/search <topic>` | Topic search (a name or a question is recognised automatically) |
| `/senior <name>` | A senior's portfolio; several seniors with that name → the user chooses |
| `/year <yyyy>` | Projects of one cohort year, with program chips |
| `/about` | What the bot is |

Buttons on a result: **Abstract**, **Portfolio**, **Repo** (when a repository exists),
**Next ▶**, and 👍/👎. A portfolio shows **Send a message** only when the senior agreed to the relay.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — (required for the bot) | BotFather token; never logged |
| `KB_SALT` | — (required for the bot) | Salt for hashing Telegram user IDs |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant server |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API |
| `EMBED_MODEL` | `BAAI/bge-m3` | Dense + sparse encoder |
| `OLLAMA_MODEL` | `gemma4:12b-it-qat` | Answer model (pinned) |
| `ENABLE_LLM` | `false` | `false` = extractive answers; `true` = Gemma 4 |
| `ABSTAIN_THRESHOLD` | `0.45` | Relevance gate; replace with the value chosen by the eval sweep |
| `KB_COLLECTION` | `rupp_senior_projects_v1` | Qdrant collection |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |

## Tests

```powershell
pytest tests/unit -q
pytest tests/integration -q      # uses in-memory Qdrant, or the server at QDRANT_URL when set
ruff check .
black --check .
```

Tests never contact Telegram, never download a model, and use only the synthetic fixtures.
CI (`.github/workflows/ci.yml`) runs the same steps with a Qdrant service; set the repository
variable `RUN_EVAL=true` to add the evaluation gate once `eval/baseline.json` is committed.

## Layout

```text
.
├── data/                 # manifest CSVs, corpus/<project_id>/, kb.sqlite3 (not committed), reference/ (never indexed)
├── ingestion/            # validate -> parse -> chunk -> embed -> index -> verify
├── store/                # SQLite metadata store
├── retrieval/            # intents, hybrid search, facets, name resolution, portfolios
├── answer/               # answer() service, Gemma prompt, citation check, consent
├── bot/                  # Telegram adapter: handlers, formatting, sending, state, relay
├── eval/                 # golden set, metrics, runner, baseline gate
├── datasets/             # query log -> training pairs
├── scripts/              # pre-demo check, legacy data migration
├── tests/                # unit, integration, synthetic fixtures
├── cli.py                # offline demo
├── config.py             # settings from the environment / .env
├── logging_config.py     # structured logs, counters, redaction
├── docs/                 # product specs, gap analysis, ADRs
├── implementation/       # task cards, shared interfaces, testing spec
└── legacy/dse-curriculum/  # the old curriculum assistant, reference only
```

## Known limitations

- **No real data yet.** Everything is tested on synthetic fixtures; the evaluation baseline
  (`eval/baseline.json`) must come from an approved run on real data against the Qdrant server.
- **`datasets/` name clash (decision pending).** The repo's `datasets/` package has the same name
  as the Hugging Face `datasets` library that bge-m3 needs. Commands that load bge-m3 from the
  repository root (`ingestion.run`, `eval.run_eval`, `bot.main`, `cli.py`) fail with
  `module 'datasets' has no attribute 'Dataset'` until the folder is renamed. The Docker image,
  the CI evaluation step, and `scripts/pre_demo_check.py` already work around it.
- **DOCX and Markdown have no pages**, so their citations read `p.0`.
- **Keyword search is case-sensitive** (bge-m3 sparse weights), so `khmer` and `Khmer` differ.
- **Facet views** count every matching project, but list only the top search results.
- **Gemma 4** is tested with fake Ollama responses only; `ENABLE_LLM=false` is the default.
- **Relay:** an admin must link a senior's Telegram account (`DB.set_telegram_user_hash`).
- `langchain`, `langchain-qdrant`, and `pandas` are pinned for the practicum stack but not imported.

## Read next

1. [`docs/Senior_Project_KB_Prd_V2_0.md`](docs/Senior_Project_KB_Prd_V2_0.md) — product requirements
2. [`docs/Senior_Project_KB_Architecture_Spec_V2_0.md`](docs/Senior_Project_KB_Architecture_Spec_V2_0.md) — architecture
3. [`docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md`](docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md) — pipeline
4. [`implementation/README.md`](implementation/README.md) — implementation handbook and rulings
5. [`implementation/tasks/README.md`](implementation/tasks/README.md) — task board
6. [`CLAUDE.md`](CLAUDE.md) — rules for coding agents

Never import from `legacy/`, and never commit real student documents, contact details, or secrets.
