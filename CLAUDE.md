# CLAUDE.md — RUPP-SPKB v2.0

Project instructions for Claude Code. Read this file before any task.

## What this repository is

**RUPP Senior Project Knowledge Base (RUPP-SPKB v2.0):** a Telegram bot that lets RUPP students search past senior projects by topic, look up a senior by name, open a portfolio, browse facets, and contact a senior only with consent.

It is **not** the DSE curriculum chatbot. The old product is quarantined in `legacy/dse-curriculum/` and must never be imported.

**Integration branch:** `refactored`.

## Read first, in this order

1. `docs/Senior_Project_KB_Prd_V2_0.md`
2. `docs/Senior_Project_KB_Architecture_Spec_V2_0.md`
3. `docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md`
4. `docs/REPO_VS_SPEC_COMPARISON.md`
5. `implementation/README.md`
6. `implementation/spec/INTERFACES.md`
7. `implementation/spec/TESTING.md`
8. `implementation/tasks/README.md`
9. The task card you are about to implement
10. The matching skill in `.agents/skills/`

## Skills

Load the smallest matching skill before editing:

| Task | Skill |
|---|---|
| Any session start | `.agents/skills/rag-project-context/SKILL.md` |
| Picking up a task card | `.agents/skills/implement-rag-task/SKILL.md` |
| Manifests, corpus, parsers, chunking, embeddings, Qdrant, ingestion | `.agents/skills/rag-data-and-indexing/SKILL.md` |
| Intent routing, hybrid retrieval, entity resolution, portfolios, gate, Gemma, citations, consent | `.agents/skills/rag-retrieval-and-answer/SKILL.md` |
| Golden set, metrics, threshold sweep, baseline, training pairs | `.agents/skills/rag-evaluation/SKILL.md` |
| Telegram handlers, cards, pagination, sender, state, relay, privacy | `.agents/skills/rag-telegram-and-privacy/SKILL.md` |
| Reviewing a task branch | `.agents/skills/rag-code-review/SKILL.md` |

The skill index is `.agents/skills/README.md`.

## Current state

- The V2.0 package skeleton exists. Most modules are **empty placeholders**; fill only the files named in the task card.
- `legacy/dse-curriculum/` is the pulled DSE assistant. Reference only.
- `data/reference/dse-curriculum/` is migrated DSE curriculum data: 51 course records, 5 documents, old curriculum golden set. It is **not** senior-project data and **must not** be indexed.
- `data/projects.csv` and `data/seniors.csv` contain **headers only**. Do not invent rows to make a feature work.
- `data/corpus/` is empty until permission-cleared senior-project documents arrive.
- The task board is `implementation/tasks/README.md`; statuses are maintained there.

## Non-negotiable rules

1. **Never import from `legacy/`.** It is a different product.
2. **Never invent senior names, projects, supervisors, years, contact details, or test data.**
3. **Manifest is the source of truth.** A PDF may only supply evidence.
4. **Only `permission=granted` projects are indexed.** `status=withdrawn` removes a senior and their chunks.
5. **Hybrid retrieval is dense + bge-m3 sparse, RRF, grouped by `project_id` before truncation.** Not page-level, not source-level.
6. **Names resolve by alias → Qdrant full-text → rapidfuzz ≥ 88.** Never auto-merge an ambiguous name.
7. **Extractive is the default.** Below the relevance gate, the LLM is never called.
8. **Citations are `[Project: <title>, p.<n>]`.** The validator accepts only retrieved `(title, page)` pairs.
9. **The bot is a thin adapter.** One `answer.service.answer()` call per interaction; no retrieval logic in handlers.
10. **Telegram only.** No web app in V2.0.
11. **Consent gates contact.** Contact data never enters Qdrant payloads, prompts, logs, or training pairs.
12. **No secrets, real student documents, phone numbers, student IDs, or grades** in code, fixtures, logs, or commits.

## How to execute a task

1. Pick the lowest-numbered unblocked task in `implementation/tasks/README.md`.
2. Read the card's **Depends on**, **Spec**, **Files**, **Interfaces**, **Acceptance**, **Tests**, and **Out of scope**.
3. Read `implementation/spec/INTERFACES.md` and the matching skill.
4. Branch from `refactored`:
   `git switch -c task/T<id>-<slug>`
5. Implement only the card's files.
6. Write the tests named in the card.
7. Run `python -m py_compile` on every changed Python file.
8. Run the card's primary test only if the human asks. Do not run the full suite unprompted.
9. Update the card checkboxes and `**Status:** REVIEW`.
10. Run `python implementation/tools/rebuild_index.py`.
11. Report: files changed, tests written, what was verified, what was not, concerns.
12. Do not commit, stage, or push unless the human explicitly asks.

When a task is approved, the manager moves its card to `implementation/done/` and reruns the index script.

## Definition of done

- [ ] Files match the card; no unrelated changes.
- [ ] Public interfaces match `implementation/spec/INTERFACES.md`.
- [ ] Named tests exist and cover the acceptance criteria.
- [ ] `python -m py_compile` passes.
- [ ] `ruff check` and `black --check` pass if installed.
- [ ] No secrets or real student data.
- [ ] No `legacy/` import.
- [ ] No reference data indexed.
- [ ] Card and index updated.
- [ ] Report states what was not verified.

## Commands

```powershell
# Environment
python --version                 # target 3.11; record any deviation
python -m py_compile config.py
pytest tests/unit -q
ruff check .
black --check .

# Ingestion
python -m ingestion.validate_manifest --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus
python -m ingestion.run --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus --rebuild
python -m ingestion.verify

# Evaluation
python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold
python -m eval.compare_baseline eval/baseline.json eval/last_report.json

# Bot / CLI
python -m bot.main
python -m bot.main --check
python cli.py --query "khmer ocr"
```

## Data policy

- `data/projects.csv`: one row per project; `senior_ids` semicolon-separated; `year` is 20xx; `permission` must be `granted`.
- `data/seniors.csv`: one row per senior; aliases semicolon-separated; consent flags are explicit.
- `data/corpus/<project_id>/`: permission-cleared reports, slides, posters, notes. One folder per project.
- `data/reference/`: migrated legacy data. Never indexed.
- `tests/fixtures/`: synthetic only.
- If the data does not exist, leave the manifest header-only and report the gap. Do not fabricate.

## Branch, commit, and test policy

- Integration branch: `refactored`.
- Task branches: `task/T<id>-<slug>`.
- Do not commit, stage, push, merge, reset, or clean unless the human explicitly asks.
- Do not run the full test suite or start Docker/Ollama unless asked. A syntax check is always allowed.
- Do not delete or rewrite the legacy folder.
- If a task conflicts with the spec or `INTERFACES.md`, stop and ask; record the decision in `docs/adr/`.

## Windows note

This project is developed on Windows. If a command uses a Unix-only shell feature, use PowerShell or Git Bash explicitly. Do not assume WSL can resolve Windows paths.

## Where decisions live

- Product/scope: `docs/adr/ADR-001-product-scope.md`
- Embedding runtime fallback: `docs/adr/ADR-002-embedding-runtime.md` if needed
- Eval baseline/threshold: `docs/adr/ADR-003-eval-baseline.md` if the 90-query ruling changes
- Shared interface changes: update `implementation/spec/INTERFACES.md` first, then the task cards.
