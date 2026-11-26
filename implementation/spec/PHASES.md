# Phase Overviews

Each phase has a goal, dependencies, and an exit gate. The individual task cards live in `../tasks/`.

## Phase 0 — Scope and Foundations

**Goal:** Decide what is being built, freeze the reference code, and create the package/tooling skeleton.
**Depends on:** nothing.
**Exit gate:** an accepted product-scope ADR, a `refactored` integration branch, a package skeleton, and a documented environment.

---

## Phase 1 — Data Model and Manifest

**Goal:** Make the manifest and SQLite database the source of truth for seniors, projects, authorship, documents, consent, and the query log.
**Depends on:** Phase 0.
**Exit gate:** a valid fixture manifest passes validation, every malformed fixture is rejected before any Qdrant write, and the SQLite schema is queryable.

---

## Phase 2 — Project-Aware Ingestion and Indexing

**Goal:** Parse every supported format into page-anchored documents, chunk by structure, embed with bge-m3 dense + sparse, and upsert deterministically into Qdrant.
**Depends on:** Phase 1.
**Exit gate:** the fixture corpus indexes; every chunk has the required payload; a second run adds zero points; a scanned PDF is flagged `ocr`; a zero-text document is `failed` and excluded.

---

## Phase 3 — Five-Intent Retrieval

**Goal:** Route queries into the five V2.0 intents and return projects, seniors, portfolios, and facet results with the correct evidence.
**Depends on:** Phase 2.
**Exit gate:** three name spellings resolve to one senior; ambiguous names produce candidates; topic results are grouped by project; facet filters pre-filter in Qdrant; a portfolio renders from SQLite alone.

---

## Phase 4 — Extractive-First Answer Layer

**Goal:** One service entry point that routes, gates, answers extractively or with Gemma, validates citations, and enforces consent.
**Depends on:** Phase 3.
**Exit gate:** a negative query abstains without calling the LLM; a bad citation falls back to extractive; a non-consenting portfolio has no contact block; the CLI works offline.

---

## Phase 5 — Telegram Interface

**Goal:** Deliver every V2.0 capability as a thin Telegram adapter: commands, plain text, cards, portfolios, facets, deep links, pagination, feedback, and consent relay.
**Depends on:** Phase 4.
**Exit gate:** the PRD §7 interaction contract works against a fake Telegram transport; no handler contains retrieval logic; every message is HTML-escaped and ≤ 4096 characters; per-chat throttling and `retry_after` are tested.

---

## Phase 6 — Evaluation and Training-Pair Flywheel

**Goal:** Measure the product by slice, freeze a baseline, fail CI on regression, and export next semester's training pairs from real logs.
**Depends on:** Phase 5.
**Exit gate:** `run_eval.py` prints Recall@5, MRR@10, nDCG@10, name top-1, and abstention precision by slice; a committed baseline gates CI; `build_training_pairs.py` produces valid JSONL.

---

## Phase 7 — Deployment, Configuration, and Observability

**Goal:** Run the system from a clean checkout with pinned infrastructure, safe secrets, structured logs, and a pre-demo checklist.
**Depends on:** Phase 6.
**Exit gate:** `docker compose up -d qdrant`, ingestion, evaluation, and the bot work from a clean environment; the loaded model tags are logged; CI gates the baseline.

---

## Phase 8 — Retire the Reference Product

**Goal:** Verify the DSE curriculum assistant and the web app are quarantined in `legacy/dse-curriculum/` so the repository describes only V2.0.
**Depends on:** Phase 7.
**Exit gate:** no v2.0 path imports the retired modules; the repository layout matches Architecture §16; the README describes only the senior-project KB.

---
