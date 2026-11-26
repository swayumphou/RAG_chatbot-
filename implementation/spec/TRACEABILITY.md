# Traceability — Spec → Task

Use this file to prove that every requirement has an owner. If a spec line has no task, the implementation is incomplete even if every task is green.

---

## 1. Functional requirements

| FR | Requirement | Tasks |
|---|---|---|
| **FR-01** | Manifest intake and validation | T1.1, T1.2, T1.3, T1.4, T1.5, T1.6, T2.9 |
| **FR-02** | Multi-format parsing into page-anchored LangChain Documents | T2.1, T2.2, T2.3, T2.4, T2.5 |
| **FR-03** | Structure-aware chunking, abstract atomic, references dropped | T2.6 |
| **FR-04** | Hybrid indexing: bge-m3 dense + sparse, payload indexes, idempotency | T2.7, T2.8 |
| **FR-05** | Topic search, RRF, group by project, top 5 distinct projects | T3.2, T3.3, T3.8, T5.3 |
| **FR-06** | Senior name lookup and entity resolution | T3.1, T3.5, T5.5 |
| **FR-07** | Portfolio assembly from SQLite | T3.6, T5.4 |
| **FR-08** | Facet browsing with Qdrant filters and SQLite counts | T3.4, T5.6 |
| **FR-09** | Grounded answer synthesis, extractive default, citation validator | T4.1, T4.2, T4.3, T4.4, T4.5, T4.7 |
| **FR-10** | Consent-gated contact and relay | T4.6, T5.8 |
| **FR-11** | Query logging, feedback, training-pair export | T6.4, T6.5 |
| **FR-12** | Telegram bot surface: commands, deep links, cards, pagination | T5.1, T5.2, T5.3, T5.4, T5.5, T5.6 |
| **FR-13** | Delivery, rate limits, reliability | T5.7 |
| **FR-14** | Semester-2 readiness: embedder interface, frozen golden set, model tags | T2.7, T6.1, T6.3, T6.6 |

---

## 2. PRD acceptance criteria (Semester 1 sign-off)

| # | Acceptance criterion | Tasks / evidence |
|---|---|---|
| 1 | A manifest of ≥ 40 permission-cleared projects validates and ingests idempotently | T1.1–T1.6, T2.9, T6.3 |
| 2 | PDF, DOCX, PPTX, and MD parse; a scanned PDF triggers OCR and is flagged | T1.3, T2.2–T2.5 |
| 3 | Qdrant holds dense + sparse vectors, all payload indexes, and the asserted point count | T2.7, T2.8 |
| 4 | `khmer ocr text recognition` returns ≥ 3 correct projects in the top 5 | T2.6–T2.9, T3.3, T6.1–T6.3 |
| 5 | `Sok Dara`, `សុខ ដារា`, and `sokdaraa` resolve to the same senior | T3.1, T3.5 |
| 6 | A duplicated name shows a candidate keyboard and merges nothing | T3.5, T5.5 |
| 7 | A non-consenting senior’s portfolio shows no contact fields and no relay button | T4.6, T5.8 |
| 8 | `quantum computing lab schedule 2019` abstains with zero fabricated projects | T4.2, T6.1–T6.3 |
| 9 | Facet filters pre-filter correctly and survive a shared deep link | T3.4, T5.6 |
| 10 | A portfolio longer than 4096 characters paginates instead of truncating | T5.4 |
| 11 | 50 rapid queries do not trigger a ban; `retry_after` is honoured | T5.7 |
| 12 | With `ENABLE_LLM=true`, every citation exists in the retrieved set; a forced bad citation falls back to extractive | T4.4, T4.5 |
| 13 | `eval/run_eval.py` prints Recall@5, MRR, nDCG@10, name top-1, and abstention precision by slice | T6.2, T6.3 |
| 14 | `datasets/build_training_pairs.py` produces a valid training-pairs file | T6.5 |

---

## 3. Pipeline stages

| Stage | Requirement | Tasks |
|---|---|---|
| **00 Validate** | projects + seniors + corpus; refs, permission, files, corpus hash | T1.4, T1.5, T1.6 |
| **01 Parse** | paginated, format-specific, OCR flagged, no silent empty doc | T2.1–T2.5 |
| **02 Chunk** | abstract atomic, references dropped, breadcrumbs, payload | T2.6 |
| **03 Embed + Index** | dense + sparse, collection contract, payload indexes, uuid5, verify | T2.7, T2.8, T2.9 |
| **04 Route** | normalize, five intents, entities | T3.1, T3.2 |
| **05 Retrieve** | hybrid RRF, group by project, entity resolution, facets, portfolio | T3.3, T3.4, T3.5, T3.6, T3.7, T3.8 |
| **06 Answer** | gate, extractive default, Gemma, citation validator, consent | T4.1–T4.7 |
| **07 Deliver** | HTML, 4096, cards, deep links, throttle, relay | T5.1–T5.9 |
| **08 Log** | query log, clicks, ratings, training pairs | T6.4, T6.5 |

---

## 4. Architecture components

| Component | Module | Tasks |
|---|---|---|
| Manifest validator | `ingestion/validate_manifest.py` | T1.4–T1.6 |
| Parsers | `ingestion/parsers/` | T2.1–T2.5 |
| Chunker | `ingestion/chunker.py` | T2.6 |
| Embedder | `ingestion/embedder.py` | T2.7 |
| Indexer | `ingestion/indexer.py` | T2.8 |
| Metadata store | `store/db.py` | T1.4, T6.4 |
| Intent router | `retrieval/intent.py` | T3.1, T3.2 |
| Hybrid retriever | `retrieval/hybrid.py` | T3.3 |
| Entity resolver | `retrieval/entity.py` | T3.5 |
| Facets | `retrieval/filters.py` | T3.4 |
| Portfolio | `retrieval/portfolio.py` | T3.6 |
| Answer service | `answer/service.py` | T4.1–T4.3, T4.7 |
| Gemma client | `answer/prompts.py` | T4.4 |
| Citation validator | `answer/validate.py` | T4.5 |
| Consent | `answer/consent.py` | T4.6 |
| Bot handlers | `bot/handlers.py` | T5.2, T5.6 |
| Formatter | `bot/format.py` | T5.3, T5.4 |
| Sender | `bot/sender.py` | T5.7 |
| State | `bot/state.py` | T5.5 |
| Relay | `bot/relay.py` | T5.8 |
| Evaluator | `eval/run_eval.py` | T6.2, T6.3 |
| Exporter | `datasets/build_training_pairs.py` | T6.5 |

---

## 5. Non-functional requirements

| NFR | Target | Tasks / verification |
|---|---|---|
| Search latency | P50 < 800 ms, P95 < 2.0 s (hybrid, CPU, no LLM) | T3.3, T7.3 |
| Answer latency | P50 < 8 s on CPU with 12B QAT; < 2 s on GPU | T4.4, T7.3 |
| First feedback | `typing` action < 1 s | T5.2, T5.7 |
| Cost | $0 recurring; open weights; self-hosted | T2.7, T7.1 |
| Offline | ingestion + retrieval after model pull; only Telegram needs network | T2.9, T4.7 |
| Reproducibility | pinned model tags + fixed corpus hash → identical point count | T2.7, T2.8, T6.3, T7.3 |
| Footprint | laptop-capable: 12B QAT ~7.2 GB, bge-m3 ~2 GB, Qdrant ~0.5 GB | T2.7, T7.1 |
| Corpus scale | ≥ 150 projects / ≥ 3,000 chunks without redesign | T2.8, T2.9 |
| Freshness | new project live in < 60 s, no retraining | T2.9 |

---

## 6. Privacy and security requirements

| Requirement | Tasks |
|---|---|
| Consent gates contact | T4.6, T5.8 |
| No sensitive identifiers indexed | T1.5, T2.6, T2.8 |
| Takedown via `status=withdrawn` removes chunks | T1.5, T2.9 |
| Only `permission=granted` is ingested | T1.5 |
| Telegram identity stored as salted hash | T5.2, T6.4 |
| No cold messaging; relay only after senior opts in | T5.8 |
| Every result states why it matched and links to its source | T3.3, T5.3 |
| Contact data never enters the LLM prompt | T4.4, T4.6 |

---

## 7. Open spec gaps that need an ADR if they change

| Gap | Current ruling | Task |
|---|---|---|
| 60 vs 90 golden queries | 90-query Architecture composition | T6.1 |
| Python 3.11 vs 3.15 | target 3.11 | T0.3 |
| FlagEmbedding vs Ollama fallback | FlagEmbedding default; fallback needs ADR | T2.7 |
| `domain` / `tech_stack` missing from payload example | add them for FR-08 | T2.8, T3.4 |
| `abstain_threshold` constant vs measured | 0.45 placeholder then sweep | T4.2, T6.3 |
| Web app in the legacy code | already quarantined; verify no V2.0 import | T8.1 |
| DSE-only refusal in the legacy code | retire; V2.0 covers DSE/ITE/TEE/BE/FTCE | T8.2 |

---

## 8. Coverage check

At the end of every phase, update this table:

| Phase | FRs covered | Acceptance criteria covered | Status |
|---|---|---|---|
| 0 | — | — | TODO |
| 1 | FR-01, FR-14 (partial) | AC-1 | TODO |
| 2 | FR-02, FR-03, FR-04 | AC-2, AC-3 | TODO |
| 3 | FR-05, FR-06, FR-07, FR-08 | AC-4, AC-5, AC-6, AC-9 | TODO |
| 4 | FR-09, FR-10 | AC-7, AC-8, AC-12 | TODO |
| 5 | FR-12, FR-13 | AC-6, AC-9, AC-10, AC-11 | TODO |
| 6 | FR-11, FR-14 | AC-13, AC-14 | TODO |
| 7 | NFRs | deployment / reproducibility | TODO |
| 8 | cleanup | no spec coverage bypass | TODO |

If any PRD acceptance criterion is not covered by a task, add the task before starting Phase 1.
