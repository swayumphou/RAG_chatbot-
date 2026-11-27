# Repository vs. RUPP-SPKB v2.0 — Gap Analysis

**Repository:** `swayumphou/RAG_chatbot-`  
**Baseline commit:** `d1b5fac` (`add new project structure`)  
**Legacy code location:** `legacy/dse-curriculum/` (pulled from GitHub on 2026-11-26 and quarantined)  
**Path note:** all `src/...`, `data/...`, `evaluation/...`, `tests/...`, and `web/...` paths below refer to the legacy folder, not the V2.0 packages.  
**Compared against:**
- `docs/Senior_Project_KB_Prd_V2_0.md`
- `docs/Senior_Project_KB_Architecture_Spec_V2_0.md`
- `docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md`

**Method:** static review of every Python file, the data files, tests, requirements, and the web page. The application was not executed; the repository's own evaluation numbers in `README.md` were not independently reproduced.

---

## 0. Verdict

**This repository is not an implementation of RUPP-SPKB v2.0.**

It implements a **DSE curriculum assistant**: it answers questions about the Data Science and Engineering bachelor program from a 51-course JSON file, a few official PDFs/Markdown pages, and a practice slide deck.

The V2.0 documents describe a **person-anchored archive of past senior projects** — seniors, aliases, authorship, projects, portfolios, name lookup, consent, and hybrid project search.

**Functional-requirement compliance: 0 fully met, 9 partial, 5 missing.**

**Decision:** build V2.0 and keep this code as reference only — see [ADR-001: product scope](adr/ADR-001-product-scope.md).

| Category | Result |
|---|---|
| Fully compliant FRs | **0 / 14** |
| Partially compliant FRs | FR-02, FR-03, FR-04, FR-05, FR-09, FR-11, FR-12, FR-13, FR-14 |
| Missing FRs | FR-01, FR-06, FR-07, FR-08, FR-10 |
| Core product | Different product |

The legacy code is coherent as a DSE curriculum assistant. It is not a partial senior-project KB; the domain model, corpus, retrieval intents, and interface contract are different.

---

## 1. Headline mismatch

| Dimension | V2.0 spec | This repository |
|---|---|---|
| Primary entity | **Senior** + **Project** | **Course** / **Program** |
| Corpus | Student reports, theses, slides, posters | DSE curriculum JSON, RUPP pages, one Springer article, one slide deck |
| Unit of retrieval | Project chunk (`project_id`) | Source page (`source_id`, `page`) |
| Main questions | "Who worked on Khmer OCR?", "What did Sok Dara build?" | "How many credits is Deep Learning?", "What do DSE graduates do?" |
| Interface | Telegram only | Telegram + local web app + CLI |
| Name lookup | Alias table + Qdrant full-text + rapidfuzz | None |
| Portfolios | From SQLite | None |
| Consent / relay | Required | None |
| Data scale target | ≥ 40 projects, ≥ 3,000 chunks | 51 course records, ~4 official sources |
| Fine-tuning bridge | Query log → training pairs | Query log only; no pair exporter |

The repository's `README.md` openly describes the DSE curriculum assistant, its 59-question golden set, and its DSE-only refusal rules. Those are legitimate for that product but contradict the V2.0 scope.

---

## 2. File-structure comparison

| V2.0 spec path | This repository | Status |
|---|---|---|
| `data/projects.csv` | none | **Missing** |
| `data/seniors.csv` | none | **Missing** |
| `data/corpus/<project_id>/{report.pdf,slides.pptx,...}` | `data/official/` + `data/practice/` DSE files | **Wrong domain** |
| `ingestion/validate_manifest.py` | `src/ingest.py::validate` validates `sources.csv` | **Wrong schema** |
| `ingestion/parsers/{pdf,docx,pptx,markdown,ocr}.py` | inline `_pdf`, `_docx`, `_pptx`, `_markdown`, `_json`, `_ocr` in `src/ingest.py` | Partial, not packaged |
| `ingestion/chunker.py` | `src/ingest.py::chunk_pages`, `course_documents` | Partial |
| `ingestion/embedder.py` | `src/hybrid.py::OllamaDense` | Present, different interface |
| `ingestion/indexer.py` | `src/hybrid.py::write_collection` | Partial |
| `ingestion/verify.py` | one point-count assertion in `write_collection` | Partial |
| `store/db.py` | `src/curriculum.py::DB` (`source`, `course`, `query_log`, `user_language`) | **Wrong schema** |
| `retrieval/intent.py` | `src/curriculum.py::route` — 3 intents, no senior entities | **Wrong scope** |
| `retrieval/hybrid.py` | `src/hybrid.py::Searcher`, `HybridRetriever` | Partial |
| `retrieval/entity.py` | none | **Missing** |
| `retrieval/filters.py` | none | **Missing** |
| `answer/service.py` | `src/answer.py::answer` | Present, different semantics |
| `answer/prompts.py` | inline `RULES` in `src/answer.py` | Partial |
| `answer/validate.py` | `src/answer.py::citations_valid` | **Wrong contract** |
| `bot/main.py`, `handlers.py`, `format.py`, `sender.py`, `state.py`, `relay.py` | one `src/telegram_bot.py`; rendering in `src/answer.py`; no sender/state/relay | Partial / missing |
| `cli.py` | `src/answer.py` CLI mode | Equivalent |
| `eval/golden_set.csv`, `metrics.py`, `run_eval.py` | `evaluation/golden_set.csv` + `src/evaluate.py` | Partial |
| `datasets/build_training_pairs.py` | none | **Missing** |
| `tests/` | `tests/test_core.py` (34 tests) | Present, testing the wrong product |
| `docker-compose.yml`, Qdrant server | none; embedded local Qdrant copied into memory | **Missing** |
| Telegram-only | adds `src/web_app.py` + `web/index.html` | **Out of scope** |

---

## 3. Functional requirements matrix

| FR | Status | Evidence / finding |
|---|---|---|
| **FR-01 Manifest validation** | **Missing** | `src/ingest.py:81` validates `sources.csv` (`source_id/title/doc_type/file/status`), not `projects.csv`/`seniors.csv`. No `senior_id`, `permission`, `corpus_hash`, duplicate-project check, manifest diff, or skip-non-granted rule. |
| **FR-02 Multi-format parsing** | **Partial** | PDF (`src/ingest.py:156`), PPTX (`:192`), DOCX (`:204`), MD/TXT (`:221`), JSON (`:234`) are loaded with page/slide/section anchors. Missing per-`Document` `doc_id`, `project_id`, `parser`, `parse_status`, `sha256`; OCR is called silently (`:173-174`, `:181`) with no `parse_status="ocr"` flag; `pytesseract` is not in `requirements.txt`. |
| **FR-03 Structure-aware chunking** | **Partial / mis-coded** | 700/100 body (`src/config.py:38`), 300/50 slides (`:39`), correct splitter separators (`src/ingest.py:266-270`). But no atomic abstract, no references/bibliography/appendix exclusion, `MIN_CHUNK_CHARS=60` not 80, no primary-document flag, and the breadcrumb is `[Source: … | Section: … | p.N]` instead of `[Project | Author(s) | Year | Program | Section | p.N]` (`src/ingest.py:273-274`). |
| **FR-04 Hybrid Qdrant indexing** | **Partial / mis-coded** | Dense + sparse in one collection, cosine, deterministic UUID5, point-count assertion (`src/hybrid.py:121-145`). Sparse is a hand-rolled BM25/CRC32 scheme (`src/hybrid.py:36-61`), not bge-m3 lexical weights. No payload indexes at all; no full-text index on `author_names_normalized`; HNSW config not set; embedded local Qdrant copied into RAM (`src/hybrid.py:104-169`) instead of the specified server; full delete-and-rebuild instead of idempotent upsert. |
| **FR-05 Topic search + project grouping** | **Partial / mis-coded** | Dense + sparse RRF exists (`src/hybrid.py:196-203`). `select` groups by `(source_id, page, course_id)` (`:181`), not `project_id`, so one source/project can fill several top-5 slots. No `matched_by`; no project cards; no "top 5 distinct projects." |
| **FR-06 Senior name lookup / entity resolution** | **Missing** | No senior directory, alias table, Qdrant full-text lookup, rapidfuzz, ambiguity keyboard, or not-found candidates. `route()` only knows `COURSE_LOOKUP`, `CURRICULUM_LIST`, `QUESTION` (`src/curriculum.py:60-102`). |
| **FR-07 Portfolio assembly** | **Missing** | No `senior`/`project`/`authorship` tables, no portfolio renderer, no metadata-only portfolio. |
| **FR-08 Facet browsing** | **Missing** | No year/program/supervisor/domain/tech filters, no Qdrant payload filtering, no `GROUP BY` counts, no removable chips, no deep-link encoding. `/year` is only "courses in year N" (`src/telegram_bot.py:146-147`). |
| **FR-09 Grounded answer synthesis** | **Partial / mis-coded** | Extractive and Gemma paths exist (`src/answer.py:159-201`, `:247-259`), temp 0, `num_ctx=8192`, `num_predict=600`. But `ENABLE_LLM` defaults to **true** (`src/config.py:58`) while the spec default is extractive; default model is `gemma4:e4b` (`src/config.py:34`), not `gemma4:12b-it-qat`; the prompt gets top-5 **passages**, not top-5 **grouped projects**; citations are numeric `[1]` (`src/answer.py:230`) and `citations_valid` only checks that the number is in range (`:298-301`) — it never checks `[Project: title, p.N]` or that the cited passage supports the sentence. |
| **FR-10 Consent-gated contact / relay** | **Missing** | No consent flags, contact card, relay, senior identity mapping, or takedown path. |
| **FR-11 Query logging / feedback** | **Partial** | `query_log` exists (`src/curriculum.py:120-123`), with salted user hash, rating, latency, retrieved JSON, `top_score` (`:165-170`; `src/answer.py:90-100`). Missing `clicked_project`, `resolved_senior`, `tg_message_id`; only 👍/👎, no card-click events; no `datasets/build_training_pairs.py`. |
| **FR-12 Telegram surface** | **Partial / mis-coded** | Has `/start`, `/help`, `/language`, `/ask`, `/search`, `/course`, `/year` (`src/telegram_bot.py:246-249`), plain text, HTML, 4096 pagination, private-only, typing action. Missing `/senior`, `/about`, deep links (`?start=senior_…` / `proj_…`), project cards with Abstract/Portfolio/Repo/Next, an ambiguous-name keyboard, and portfolio pagination. |
| **FR-13 Delivery / rate limits** | **Partial** | 30 s in-memory dedupe (`src/telegram_bot.py:81`, `:114-122`) and `AIORateLimiter(max_retries=1)` (`:243`). No per-chat `asyncio.Lock` + ≥1 s spacing, no global semaphore, no explicit `RetryAfter` sleep/log/drop, and no placeholder-edit throttle. |
| **FR-14 Semester-2 readiness** | **Partial** | The eval report records embedding/LLM model tags (`src/evaluate.py:118-120`), and model selection is centralized. But there is no training-pair export, no frozen/versioned golden set, and no enforced embedder interface/revision. |

---

## 4. Pipeline stage comparison

| Stage | V2.0 spec | This repository | Verdict |
|---|---|---|---|
| **00 Validate** | `projects.csv` + `seniors.csv` + corpus; referential integrity, permission, files, corpus hash, fail fast | `sources.csv` only; missing fields, duplicate `source_id`, file existence (`src/ingest.py:81-115`) | **Wrong schema / missing** |
| **01 Parse** | Page-anchored `ParsedDocument`; OCR fallback flagged; never silently empty | Formats + anchors present; no `doc_id/project_id/parser/parse_status/sha256`; OCR not flagged; a source with zero chunks can still be reported `ok` (`src/ingest.py:330-336`) | **Partial** |
| **02 Chunk** | Abstract atomic; references dropped; 700/100; 300/50; project/author/year/program breadcrumb; min 80 | 700/100 + 300/50 + splitter present; abstract/references rules absent; breadcrumb fields wrong; min 60 | **Partial / wrong rules** |
| **03 Embed + Index** | bge-m3 dense 1024 + bge-m3 sparse; named vectors; payload indexes; uuid5; idempotent | Dense bge-m3 via Ollama + BM25-style sparse; uuid5; count assert; no payload indexes; destructive rebuild; local embedded Qdrant | **Partial / mis-coded** |
| **04 Route** | 5 intents (`TOPIC_SEARCH`, `SENIOR_LOOKUP`, `PORTFOLIO`, `FACET_BROWSE`, `ANSWER`) + entities | 3 intents (`COURSE_LOOKUP`, `CURRICULUM_LIST`, `QUESTION`) + year/semester/program only (`src/curriculum.py:60-102`) | **Wrong scope** |
| **05 Retrieve** | Hybrid RRF → group by `project_id`; entity resolution; facets; portfolio | Hybrid RRF present; dedupe by source/page; no entity/facets/portfolio | **Partial** |
| **06 Answer** | Gate → abstain; extractive default; Gemma + `[Project, p.N]` validator | Gate + extractive/Gemma present; numeric citations and range-only validator; LLM default on | **Partial / wrong contract** |
| **07 Deliver** | HTML escape, ≤4096, per-chat throttle, inline cards, deep links | Escape + paginate + HTML + thin bot; no cards/deep links/per-chat throttle | **Partial** |
| **08 Log** | `query_log` with click/rating/intent/resolved senior + training pairs | Query log with route/mode/note/score/rating/latency; no clicks, no resolved senior, no pair exporter | **Partial** |

---

## 5. What is missing entirely

### 5.1 Domain and data model

- `projects.csv`, `seniors.csv`, `senior_alias`, `authorship`, and `document` tables.
- `data/corpus/<project_id>/` and real permission-cleared senior-project artifacts.
- Permission (`granted`/`pending`), consent (`consent_public_contact`, `consent_relay`), takedown (`status=withdrawn`).
- `project_id`, `senior_ids`, `author_names_normalized`, `year` (20xx), `program`, `supervisor_id`, `is_primary_doc` in chunk payloads.

### 5.2 Retrieval

- Senior entity resolution: alias exact → Qdrant full-text → rapidfuzz ≥ 88 → ambiguous keyboard.
- Portfolio assembly from SQLite, including metadata-only portfolios.
- Facet browsing with Qdrant payload filters + SQLite `GROUP BY` counts + deep-link chips.
- Project grouping after RRF.
- `matched_by` reason on each result.
- Qdrant full-text and keyword payload indexes.

### 5.3 Answer and interface

- `[Project: <title>, p.N]` citation validator with an allowed-set check.
- Project cards: title, authors, year, program, supervisor, snippet, page, `matched`, Abstract/Portfolio/Repo/Next buttons.
- `/senior`, `/about`, deep links `?start=senior_<id>` / `proj_<id>`.
- Per-chat send serialization and `retry_after` handling.
- Portfolio pagination and ambiguity state (`bot/state.py`).

### 5.4 Operations, evaluation, flywheel

- `docker-compose.yml`, Dockerfile, and a Qdrant **server**. The spec explicitly warns that embedded local mode may not reproduce hybrid `prefetch` + RRF behaviour.
- `eval/run_eval.py` with Recall@5, MRR@10, nDCG@10, name top-1, abstention precision, faithfulness, and per-slice reporting.
- Frozen baseline + CI regression gate (`nDCG` drop > 0.02 fails).
- `datasets/build_training_pairs.py`.
- Structured logs, P95 latency, 429 counters, LLM rejection rate, loaded-model-tag startup log.
- Consent / relay / takedown flow.

---

## 6. What is mis-coded against the docs

1. **Sparse retrieval is BM25, not bge-m3 lexical.** The spec requires bge-m3 `lexical_weights`; the repo hashes tokens with CRC32 and applies BM25-style TF (`src/hybrid.py:36-61`). Khmer is character trigrams, not `khmernltk` word segmentation (`:46-47`).
2. **Grouping key is wrong.** `select()` groups by `(source_id, page, course_id)` (`src/hybrid.py:181`), so multiple pages of one source/project can fill the top 5.
3. **Breadcrumb fields are wrong.** `[Source | Section | p.N]` instead of `[Project | Author(s) | Year | Program | Section | p.N]` (`src/ingest.py:273-274`).
4. **Citation contract is wrong.** Numeric `[1]` plus range check (`src/answer.py:230`, `:298-301`) instead of `[Project: title, p.N]` plus an allowed-set check.
5. **Chunking rules are incomplete.** No atomic abstract, no references/bibliography/appendix drop, min 60 vs 80, no primary-document flag.
6. **Parser metadata is incomplete.** No `doc_id`, `project_id`, `parser`, `parse_status`, `sha256` on `Document`s; OCR is not recorded.
7. **`/year` is not facet browse.** It rewrites to "courses in year N" and hits the course table (`src/telegram_bot.py:146-147`), not Qdrant payload filters.
8. **Intent routing is a different taxonomy.** Only curriculum intents; no names, portfolios, facets, or multi-intent intersection.
9. **LLM defaults contradict the spec.** `ENABLE_LLM=true` (`src/config.py:58`) and `gemma4:e4b` (`src/config.py:34`); the spec default is extractive with `gemma4:12b-it-qat`, E4B documented fallback only.
10. **Indexing is a destructive rebuild, not idempotent upsert.** `write_collection` deletes the collection first (`src/hybrid.py:126-127`); re-running is not "zero new points" and cannot preserve the old index on failure.
11. **The product refuses ITE/AIE** (`src/config.py:53`, `src/answer.py:79-83`), while V2.0's corpus spans DSE/ITE senior projects.
12. **The web app is out of scope.** PRD §3.3 and the architecture say Telegram only, no web UI; the repo ships `web/index.html` + `src/web_app.py`.

---

## 7. Wrong implementation and defects

| Severity | Finding | Evidence |
|---|---|---|
| **Critical** | **Destructive rebuild:** the docstring says "the old index stays untouched if anything fails," but `write_collection` deletes the collection before embedding/upserting. A model or upsert failure leaves an empty collection while SQLite still describes the old data. | `src/ingest.py:313` vs `src/hybrid.py:126-145` |
| **Critical** | **No two-store atomicity:** Qdrant is written first, then SQLite is rebuilt. If `db.rebuild` fails, the vector index and metadata disagree. | `src/ingest.py:342-346` |
| **High** | **Zero-chunk sources marked "ok":** `stats[...] = "ok" if docs else "failed"` checks pages, not chunks. A source with pages but no chunk ≥ 60 chars is silently "ok" with 0 chunks. | `src/ingest.py:330-336` |
| **High** | **Global lock serializes every query**, including a 120 s LLM call, across all Telegram/web/CLI users. The spec asks for per-chat serialization, not global. | `src/answer.py:50`, `:76` |
| **High** | **No degraded mode:** `KB` opens Qdrant before the DB, so if Qdrant is unavailable even SQLite-only course answers cannot run. The spec says portfolios/records should still work. | `src/answer.py:46-49` |
| **Medium** | **`paginate` silently drops the tail** of any single paragraph longer than 4096 chars (`buf = para[:limit]` discards the remainder). | `src/answer.py:464-475` |
| **Medium** | **Unbounded dedupe cache:** `_recent` grows without eviction for the life of the process. | `src/telegram_bot.py:81-82`, `:122` |
| **Medium** | **No payload indexes**, so full-text name lookup, keyword filters, and facet filters are impossible even if the data existed. | `src/hybrid.py:128-131` |
| **Medium** | **Tests encode the current behavior**, not the V2.0 contracts. There are 34 good offline tests, but none for manifest/permission, OCR flag, atomic abstract, references drop, project grouping, alias/ambiguity, portfolio, consent, deep links, per-chat throttle, or training pairs. | `tests/test_core.py` |
| **Low** | **Unbounded single-request embedding:** `OllamaDense.embed_documents` posts the whole batch in one HTTP request (`src/hybrid.py:79-88`). LangChain batches at 16, so current scale is survivable; at 3,000 chunks this needs explicit batching and timeouts. | `src/hybrid.py:79-88` |

---

## 8. What is reusable

The repo is coherent as the DSE curriculum assistant, and several parts are directly reusable for V2.0:

- **LangChain plumbing:** `Document`, `BaseLoader`, `RecursiveCharacterTextSplitter`, `QdrantVectorStore`, `BaseRetriever` are already exercised (`src/ingest.py`, `src/hybrid.py`).
- **Hybrid retrieval skeleton:** dense + sparse prefetch, RRF, dense-cosine gate, `top_k` (`src/hybrid.py:188-214`).
- **Deterministic IDs and count assertion:** `uuid5(NAMESPACE, chunk_id)` + `count == len(docs)` (`src/hybrid.py:138-145`).
- **Thin answer entry point:** bot, web, CLI, and evaluator all call `answer()` (`src/answer.py:72`).
- **Telegram hygiene:** HTML escaping, 4096 pagination, typing action, 30 s dedupe, Resource button, 👍/👎 logging.
- **Khmer work:** translation-before-search, Thai/Burmese lookalike repair, Khmer typography/numerals (`src/answer.py:262-301`). Valuable if adapted, though V2.0 prescribes `khmer-nltk` segmentation rather than translation.
- **SQLite logging + language persistence** and a working 59-query evaluator.

---

## 9. Recommended phase order

The implementation phases and individual task cards are tracked in [`implementation/tasks/README.md`](../implementation/tasks/README.md). Phase goals and exit gates are in [`implementation/spec/PHASES.md`](../implementation/spec/PHASES.md).

| Phase | Goal |
|---|---|
| 0 | Lock the product decision and repo scope |
| 1 | Build the senior/project data model and manifest |
| 2 | Rebuild the ingestion pipeline around project artifacts |
| 3 | Implement the five-intent retrieval pipeline |
| 4 | Implement extractive-first answer synthesis with project citations |
| 5 | Implement the full Telegram contract |
| 6 | Implement evaluation, baseline, and the training-pair flywheel |
| 7 | Deploy with Qdrant server and add observability |
| 8 | Remove or quarantine the DSE-curriculum and web-app scope |

---

## 10. Verification notes

- Static review only. No test suite, ingestion run, or evaluation was executed.
- The repository's `README.md` reports "59 questions, all passing" for the DSE curriculum corpus. That result is **not** evidence for any V2.0 acceptance criterion; the queries and corpus are different.
- The cloned commit is `d1b5fac` on `main`. The pulled code now lives in `legacy/dse-curriculum/`; the V2.0 skeleton is at the repository root, and the three specifications plus this report are under `docs/`.
- Exact line references above refer to the cloned revision; they will move once implementation starts.
