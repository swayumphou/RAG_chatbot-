# Implementation Handbook — RUPP-SPKB v2.0

**Audience:** the junior engineer or agent continuing this repository.
**Legacy code:** `legacy/dse-curriculum/` — the pulled `RAG_chatbot-` at `d1b5fac`, quarantined as reference only.
**Target:** the RUPP Senior Project Knowledge Base described in the three V2.0 documents.

This handbook is the working manual. The specs say **what** the product must do; the task cards say **what to build next**, in what order, in which files, with which interfaces and tests.

---

## 1. Read in this order

1. `docs/Senior_Project_KB_Prd_V2_0.md` — product requirements and acceptance criteria.
2. `docs/Senior_Project_KB_Architecture_Spec_V2_0.md` — components, data model, collection contract.
3. `docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md` — stage-by-stage engineering pipeline.
4. `docs/REPO_VS_SPEC_COMPARISON.md` — what the pulled legacy code did and what is missing.
5. `implementation/README.md` — this file: rules, environment, definition of done.
6. `implementation/spec/INTERFACES.md` — the shared contracts every task must obey.
7. `implementation/spec/TESTING.md` — fixtures, test commands, evaluation protocol.
8. `implementation/spec/TRACEABILITY.md` — FR / pipeline / acceptance mapping.
9. `implementation/spec/PHASES.md` — phase goals and exit gates.
10. `implementation/tasks/README.md` — the task index and status board.
11. The individual task card in `implementation/tasks/` that you are picking up.
12. `CLAUDE.md` — project instructions for Claude Code.
13. The matching skill in `.agents/skills/` — project-specific implementation guardrails.

Do not skip `spec/INTERFACES.md`. Most integration bugs in this project will come from two tasks disagreeing about a field name, a payload key, or a return shape.

### Folder layout

```text
implementation/
├── README.md              # this handbook
├── spec/                  # shared contracts and phase overviews
│   ├── INTERFACES.md
│   ├── TESTING.md
│   ├── TRACEABILITY.md
│   ├── PHASES.md
│   ├── TASK_TEMPLATE.md
│   └── README.md
├── tasks/                 # active task cards, one file per task
│   ├── README.md          # index + status board
│   └── T0.1-....md
├── done/                  # completed task cards, moved here after review
│   ├── README.md
│   └── .gitkeep
└── tools/
    └── rebuild_index.py   # regenerates tasks/README.md and done/README.md
```

---

## 2. Current state vs. target

| Area | Legacy reference (pulled) | Target (V2.0) |
|---|---|---|
| Product | DSE curriculum assistant | Senior-project knowledge base |
| Primary entity | Course/program | Senior + project |
| Data | `rupp-dse-degree.json`, 4 official sources | `projects.csv`, `seniors.csv`, `data/corpus/<project_id>/` |
| Retrieval | Dense + BM25, source/page dedupe | Dense + bge-m3 sparse, RRF, group by `project_id` |
| Name lookup | None | Alias → Qdrant full-text → rapidfuzz |
| Portfolio | None | SQLite, newest project first |
| Facets | None | Qdrant payload filters + SQLite counts |
| Answer | Numeric `[1]` citations | Extractive default; `[Project: title, p.N]` validator |
| Interface | Telegram + web + CLI | Telegram only (+ CLI fallback) |
| Eval | 59 curriculum questions | 90-query senior-project golden set, per slice |
| Deployment | Embedded Qdrant in process | Qdrant server via Docker Compose |

The pulled code is quarantined in `legacy/dse-curriculum/`; it is reference only, not a starting domain model. Do not import it from the V2.0 packages. Phase 8 verifies that the quarantine is complete.

---

## 3. How to execute a task card

1. Pick the lowest-numbered unblocked task from `implementation/tasks/README.md`.
2. Read the task card and every document listed in its **Spec** and **Interfaces** fields.
3. Create a branch from `refactored`:
   `git switch -c task/T1.5-manifest-validator`
4. Implement **only** the files listed in the card. If another file must change, update the card first (or ask for a ruling).
5. Write the tests named in the card. Tests are part of the deliverable, not an optional extra.
6. Run the card's allowed checks. If you are a dispatched subagent, follow the project policy in §8: write tests, do not run the full suite; the manager runs verification.
7. Update the task's checkboxes and the status column in `implementation/tasks/README.md`.
8. Commit one task per commit:
   `feat(T1.5): add manifest validator`
9. Do **not** merge or rebase onto `main`. The manager reviews the branch and decides.
10. When the task is marked `DONE`, move its card from `tasks/` to `done/` and update both indexes.

### Status values

| Value | Meaning |
|---|---|
| `TODO` | Not started |
| `IN PROGRESS` | Branch exists; implementation underway |
| `BLOCKED` | Waiting on a decision or another task |
| `REVIEW` | Implementation and tests are ready for the manager |
| `DONE` | Reviewed and merged into `refactored` |

### Moving a completed task

1. The manager approves the task and changes its `**Status:**` to `DONE`.
2. Add `**Completed:** YYYY-MM-DD`, `**Commit:** <hash>`, and `**Reviewed by:** <name>` under the status line.
3. Move the file from `implementation/tasks/` to `implementation/done/`.
4. Run `python implementation/tools/rebuild_index.py`. It rebuilds both indexes from the files on disk.

The `done/README.md` file is generated; do not edit its table by hand.

---

## 4. Task card format

Every card contains:

- **Depends on** — tasks that must be `DONE` first.
- **Goal** — one sentence.
- **Spec** — exact requirements, values, and edge cases the implementer cannot guess.
- **Files** — create / modify / test paths. Stay inside this list.
- **Interfaces** — what it consumes and produces, with exact names and types.
- **Steps** — checkable implementation actions.
- **Acceptance** — the observable result.
- **Tests** — the exact test file and test names that prove it.
- **Out of scope** — what this task deliberately does not do.

A card is small enough to review in one sitting. If a card grows beyond that, split it and update `tasks/README.md`. Use `spec/TASK_TEMPLATE.md` for new cards.

---

## 5. Global constraints

These apply to every task, even when a card does not repeat them.

- **Python 3.11** target. The pulled repo documents Python 3.15 for its Windows laptop; record any deviation in an ADR.
- **Telegram is the only user-facing surface.** No web app in v2.0. `cli.py` exists only as an offline demo/fallback.
- **LangChain** must orchestrate document loading, chunking, vector storage, and retrieval. Use `langchain_core.documents.Document`, `RecursiveCharacterTextSplitter`, `QdrantVectorStore`, and a `BaseRetriever` adapter.
- **Embeddings:** bge-m3 dense (1024-d) + bge-m3 sparse lexical weights. The same model revision must encode chunks and queries.
- **Vector store:** Qdrant with named vectors `dense` and `lexical`, `Distance.COSINE`, HNSW `m=16`, `ef_construct=200`.
- **Qdrant server** is required for evaluation. Embedded/local mode is allowed only for smoke tests and must be labelled as such.
- **Generator:** Gemma 4 via Ollama, default `gemma4:12b-it-qat`, `temperature=0.0`, `num_ctx=8192`, `num_predict=600`.
- **Extractive is the default.** `ENABLE_LLM=false` must give a useful, cited answer. Below the relevance gate, the LLM is never called.
- **Identity comes from the manifest.** Never infer a senior, supervisor, year, or title from PDF text when the manifest has an answer.
- **Permission `granted` is required to ingest.** Consent gates contact and relay. Student IDs, phone numbers, grades, and national IDs are never indexed.
- **Telegram limits:** ≤ 4096 characters per message; `parse_mode=HTML`; `callback_data` ≤ 64 bytes; per-chat send lock with ≥ 1 s spacing; global under ~30 msg/s; honour `retry_after`; duplicate query within 30 s served from cache.
- **No secrets in code or logs.** `TELEGRAM_BOT_TOKEN` and `KB_SALT` come from the environment or `.env`; `.env` is gitignored.
- **All tests use synthetic or permission-cleared fixtures.** Never commit a real student's document or personal data.

---

## 6. Environment setup

```powershell
# Python 3.11
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pytest ruff black

# Models (Ollama)
ollama pull bge-m3
ollama pull gemma4:12b-it-qat

# Qdrant server
docker compose up -d qdrant

# Validation and ingestion
python -m ingestion.validate_manifest --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus
python -m ingestion.run --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus --rebuild
python -m ingestion.verify

# Evaluation
python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold

# Bot (long polling)
$env:TELEGRAM_BOT_TOKEN="123456:ABC..."
$env:KB_SALT="random-string"
python -m bot.main

# Offline fallback
python cli.py --query "khmer ocr"
```

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `QDRANT_URL` | `http://localhost:6333` | Qdrant server |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API |
| `EMBED_MODEL` | `BAAI/bge-m3` | Dense + sparse encoder |
| `OLLAMA_MODEL` | `gemma4:12b-it-qat` | Answer model |
| `ENABLE_LLM` | `false` | Extractive vs Gemma synthesis |
| `ABSTAIN_THRESHOLD` | `0.45` | Placeholder; replaced by the swept value |
| `TELEGRAM_BOT_TOKEN` | — | BotFather token |
| `KB_SALT` | — | Salt for hashed Telegram IDs |
| `KB_COLLECTION` | `rupp_senior_projects_v1` | Qdrant collection |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR / CRITICAL |

---

## 7. Definition of done

A task is done only when all of these are true:

- [ ] The files in the card exist and no unrelated files changed.
- [ ] The public interfaces match `spec/INTERFACES.md` exactly.
- [ ] The named tests exist and cover the acceptance criteria.
- [ ] `python -m py_compile` passes for every changed Python file.
- [ ] `ruff check` and `black --check` pass (or the card explains why not).
- [ ] No secrets, real student data, or generated indexes are committed.
- [ ] The task card checkboxes are updated.
- [ ] `implementation/tasks/README.md` status is `REVIEW`.
- [ ] The commit message follows `feat(T<id>): <summary>`.

The manager then runs the card's primary test, reviews spec compliance and code quality, and marks the task `DONE`. The card is then moved to `implementation/done/`.

---

## 8. Roles and verification policy

- **Implementer:** reads the card, writes code and tests, updates the card, commits. Does not merge.
- **Manager:** checks the spec against the diff, runs the primary test named in the card, and either requests changes or merges into `refactored`.
- **Verification runs** are the manager's job. If you are dispatched as a subagent, write the tests but do not run the full suite; a syntax check (`python -m py_compile <file>`) is allowed. If you are the human owner working locally, the commands in `spec/TESTING.md` are yours to run.
- **Reviewer:** checks both spec compliance and code quality. A task is not approved on passing tests alone.

---

## 9. Spec conflicts and rulings

The V2.0 documents disagree in a few places. These rulings are binding until a newer ADR supersedes them.

| # | Conflict | Ruling |
|---|---|---|
| 1 | Golden set is "60 queries" in the TL;DR but Architecture §13.1 slices sum to 90 | Use the Architecture §13.1 slice composition (90 queries). 60 is the minimum, not the target. Record the final count in `eval/golden_set.csv` and the report. |
| 2 | Spec says Python 3.11; pulled repo documents Python 3.15 | Target 3.11. If the Windows laptop cannot install the required wheels, record the deviation and the reason in an ADR. |
| 3 | Spec says `FlagEmbedding.BGEM3FlagModel`; the pulled laptop blocks PyTorch under Smart App Control | Default to FlagEmbedding. If blocked, an ADR must approve a fallback. Ollama's `/api/embed` returns dense only; the fallback must still supply sparse lexical weights and must be compared on the same golden set. |
| 4 | Spec default Gemma is `gemma4:12b-it-qat`; pulled repo uses `gemma4:e4b` | 12B QAT is the default. E4B is a documented fallback only when the demo laptop cannot run 12B. Log the loaded tag. |
| 5 | Extractive default vs pulled repo `ENABLE_LLM=true` | `ENABLE_LLM=false` is the default in code, `.env.example`, and the demo. |
| 6 | PRD says Telegram only; pulled repo ships a web app | The web app is already quarantined in `legacy/dse-curriculum/web/`. Phase 8 verifies no V2.0 code imports it. |
| 7 | PRD covers DSE/ITE projects; pulled repo refuses ITE | V2.0 accepts DSE, ITE, TEE, BE, and FTCE projects. The DSE-only refusal is retired. |
| 8 | `abstain_threshold` 0.45 in pipeline pseudo-code, but spec says "measured" | 0.45 is only the initial placeholder. Phase 6 sweeps it on the negative slice and commits the chosen value. |
| 9 | Payload example omits `domain` and `tech_stack`, but FR-08 filters on them | Add `domain` (keyword) and `tech_stack` (keyword list) to the chunk payload and payload indexes. Record the addition in `spec/INTERFACES.md`. |
| 10 | `doc_id` examples are inconsistent | Use `f"{project_id}__{filename}"`, e.g. `dse2024-07__report.pdf`. |
| 11 | Year means cohort year (20xx), not study year (1–4) | `year` is the graduation/cohort year, e.g. 2024. Study year only appears in curriculum text, which is out of scope for V2.0. |
| 12 | `senior_ids` separator | Semicolon-separated in CSV; a list in Python and Qdrant payload. |

If a new conflict appears, write an ADR in `docs/adr/` and link it from this table. Do not silently pick a side.

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **Senior** | A RUPP student who authored a past project; the primary person entity. |
| **Project** | A metadata row (`project_id`) plus one or more documents. |
| **Portfolio** | All indexed projects for one senior, served from SQLite. |
| **Manifest** | `projects.csv` + `seniors.csv`; the authoritative identity layer. |
| **Alias** | A curated Latin/Khmer spelling of a senior's name. |
| **Facet** | A filter such as year, program, supervisor, domain, or tech stack. |
| **Extractive** | The answer is the retrieved passage, quoted with a citation. |
| **Gate** | The relevance threshold below which the system abstains and never calls the LLM. |
| **Grouping** | Collapsing chunks to one result per `project_id` before truncation. |
| **Flywheel** | Query/click/rating logging that becomes next semester's training pairs. |
| **RLHF / pair** | A `(query, positive_project_id, hard_negatives)` training example. |

---

## 11. Target repository map

```text
rupp-senior-kb/
├── data/
│   ├── projects.csv
│   ├── seniors.csv
│   ├── corpus/<project_id>/{report.pdf,slides.pptx,poster.pdf,notes.md}
│   └── reference/dse-curriculum/   # migrated legacy data; never indexed
├── ingestion/
│   ├── validate_manifest.py
│   ├── run.py
│   ├── parsers/{base.py,pdf.py,docx.py,pptx.py,markdown.py,ocr.py}
│   ├── chunker.py
│   ├── embedder.py
│   ├── indexer.py
│   └── verify.py
├── store/
│   ├── db.py
│   └── schema.sql
├── retrieval/
│   ├── intent.py
│   ├── hybrid.py
│   ├── entity.py
│   ├── filters.py
│   └── portfolio.py
├── answer/
│   ├── service.py
│   ├── prompts.py
│   ├── validate.py
│   └── consent.py
├── bot/
│   ├── main.py
│   ├── handlers.py
│   ├── format.py
│   ├── sender.py
│   ├── state.py
│   └── relay.py
├── eval/
│   ├── golden_set.csv
│   ├── metrics.py
│   ├── run_eval.py
│   ├── compare_baseline.py
│   └── baseline.json
├── datasets/
│   └── build_training_pairs.py
├── cli.py
├── config.py
├── logging_config.py
├── scripts/
│   └── pre_demo_check.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── .github/workflows/ci.yml
├── .env.example
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── .dockerignore
├── requirements.txt
└── legacy/dse-curriculum/          # pulled reference code, never imported
```

`docs/`, `implementation/`, `.agents/`, `CLAUDE.md`, and `legacy/` stay outside the package tree. The old `src/` tree already lives in `legacy/dse-curriculum/src/`; Phase 8 verifies that no V2.0 code imports it.

---

## 12. Where decisions live

Start from `docs/adr/ADR-TEMPLATE.md`; the folder rules are in `docs/adr/README.md`.

- Product/scope: `docs/adr/ADR-001-product-scope.md`.
- Embedding runtime fallback: `docs/adr/ADR-002-embedding-runtime.md` if needed.
- Golden-set size/threshold: `docs/adr/ADR-003-eval-baseline.md` if the 90-query ruling is changed.
- Any new cross-task interface change: update `implementation/spec/INTERFACES.md` first, then the task cards.

A decision that is not written down does not exist for the next engineer.
