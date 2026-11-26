---
name: rag-project-context
description: Use at the start of any RUPP-SPKB session to load the product, current repo state, data rules, and non-negotiable RAG invariants before touching code.
---

# RUPP-SPKB Project Context

## What this project is

A **person-anchored knowledge base of past RUPP senior projects**, exposed through a Telegram bot.

A student can:
- search by topic (`khmer ocr text recognition`);
- look up a senior by name (`Sok Dara`, `សុខ ដារា`, `sokdaraa`);
- open a portfolio;
- browse by year/program/supervisor/domain/tech stack;
- see evidence with project/page citations;
- contact a senior only through consent.

**Not** a DSE curriculum chatbot. That product is quarantined in `legacy/dse-curriculum/`.

## Read order

1. `docs/Senior_Project_KB_Prd_V2_0.md`
2. `docs/Senior_Project_KB_Architecture_Spec_V2_0.md`
3. `docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md`
4. `docs/REPO_VS_SPEC_COMPARISON.md`
5. `implementation/README.md`
6. `implementation/spec/INTERFACES.md`
7. `implementation/spec/TESTING.md`
8. `implementation/tasks/README.md`
9. The task card for the task you are doing
10. The matching skill in `skills/`

## Current repository state (2026-11-26)

- The V2.0 skeleton exists with **empty placeholder files** for every module.
- `legacy/dse-curriculum/` holds the pulled DSE assistant. It is reference only.
- `data/reference/dse-curriculum/` holds the migrated DSE curriculum data (51 course records, 5 documents, old golden set). It is **not** senior-project data and **must not** be indexed.
- `data/projects.csv` and `data/seniors.csv` contain **headers only**. No real senior/project rows exist yet.
- Integration branch: `refactored`.

## The architecture in one screen

```text
data/projects.csv + data/seniors.csv + data/corpus/<project_id>/
        |
        v
ingestion: validate -> parse -> chunk -> embed -> Qdrant + SQLite
        |
        v
retrieval: route (5 intents) -> hybrid RRF -> group by project
           -> entity resolution / facets / portfolio
        |
        v
answer: relevance gate -> extractive (default) or Gemma 4
        -> citation validator -> consent card
        |
        v
bot: HTML card -> 4096 pagination -> per-chat throttle -> Telegram
        |
        v
query_log -> click/rating -> training pairs (Semester 2)
```

## Non-negotiables

1. **Identity comes from the manifest, never from PDF text.** A project is a metadata row plus evidence documents.
2. **Names are resolved by alias/full-text/fuzzy matching, not cosine similarity.** Never auto-merge an ambiguous name.
3. **The bot is a thin adapter.** `answer.service.answer()` is the single entry point for bot, CLI, and evaluator. No retrieval logic in handlers.
4. **Hybrid retrieval is dense + bge-m3 sparse, fused by RRF, grouped by `project_id` before truncation.**
5. **Extractive is the default.** Below the gate, the LLM is never called.
6. **Every factual sentence is citable.** The validator accepts only `[Project: <title>, p.<n>]` that exists in the retrieved set.
7. **Consent gates contact.** Contact fields never enter Qdrant payloads, LLM prompts, or logs.
8. **Telegram only.** No web app in V2.0.
9. **Never import `legacy/`.** It is a reference copy of a different product.
10. **Never invent senior names, projects, testimonials, contact details, or test data.**

## Where to look

| Need | File |
|---|---|
| CSV/SQLite/Qdrant/Python contracts | `implementation/spec/INTERFACES.md` |
| Task execution rules | `implementation/spec/TESTING.md`, `implementation/README.md` |
| Task index | `implementation/tasks/README.md` |
| Phase goals/exit gates | `implementation/spec/PHASES.md` |
| FR/acceptance mapping | `implementation/spec/TRACEABILITY.md` |
| Decisions | `docs/adr/` |
| Legacy reference | `legacy/dse-curriculum/` (read-only) |

## Stop and ask when

- A task requires a new field, signature, callback, or payload key not in `INTERFACES.md`.
- A requirement conflicts with the spec.
- A change would index `data/reference/` or `legacy/`.
- A change would put real personal data into a fixture, log, prompt, or Qdrant payload.
- A task asks you to invent senior/project rows.
- The work would touch secrets, commit, or push and the human has not explicitly asked.

## Branch and reporting

- Integration branch: `refactored`.
- Task branch: `task/T<id>-<slug>` from `refactored`.
- Do not commit, stage, or push unless the human explicitly asks.
- Report: files changed, what was verified, what was not, and concerns.
