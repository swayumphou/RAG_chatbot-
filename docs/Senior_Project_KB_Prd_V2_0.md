# RUPP Senior Project Knowledge Base — Product Requirements Document (PRD)

**Product:** RUPP Senior Project Knowledge Base (`RUPP-SPKB`, working name *Senior Project Explorer*)
**Institution:** Royal University of Phnom Penh (RUPP) — Faculty of Engineering (FE), Dept. of ITE / Data Science & Engineering (DSE)
**Course:** Project Practicum I (PP)
**Supervised by:** Lecturer Mr. Chap Chanpiseth
**Group 1 Researchers:** Ngoun Nuongswayumphou • Cheat Kimlang
**Version:** 2.0 — Semester 1 MVP
**Frontend:** **Telegram bot** (`python-telegram-bot` 22.8)
**LLM:** **Gemma 4** (open weights, self-hosted via Ollama)
**Embedding model:** `BAAI/bge-m3` (open-source, pre-trained)
**Supersedes:** `Rupp_Rag_Prd_V1_0.md` (RUPP Student Chatbot)

---

## Project charter (verbatim)

> **Project Objective:** Build a searchable knowledge base of past senior projects so students can look up seniors by name, view their project portfolios, and connect with them.
>
> **Core Focus:** Develop the ingestion pipeline (parsing and indexing project documents) and the retrieval pipeline (fetching accurate results based on student names and project metadata).
>
> **Framework:** Use LangChain to orchestrate document loading, text chunking, vector storage, and query retrieval.
>
> **Current Phase:** Use an open-source pre-trained model to power the search and retrieval system this semester.
>
> **Next Semester:** Transition to model fine-tuning using the curated senior project dataset to improve domain accuracy.

Every requirement in this document traces back to one of those five lines. §2 makes that mapping explicit, so the practicum report can show alignment without arguing about it.

---

## TL;DR

**RUPP-SPKB** indexes past RUPP senior projects into a hybrid search index and exposes them through a **Telegram bot**. A student types a topic (`khmer ocr`), a name (`Sok Dara`, or `សុខ ដារា`), or opens a shared link, and gets back real projects, the seniors who built them, and a way to reach them.

This semester delivers **two working pipelines plus one interface**:

1. **Ingestion pipeline** — manifest validation → document parsing → structure-aware chunking → dense + sparse embedding → Qdrant indexing.
2. **Retrieval pipeline** — intent routing → hybrid search (dense + sparse, RRF) → project grouping → senior entity resolution → portfolio assembly.
3. **Telegram interface** — the only surface a student ever sees. Gemma 4 writes the answer when synthesis is enabled; otherwise the system quotes the real source text.

| Commitment | Target |
| :--- | :--- |
| Retrieval quality | `Recall@5 ≥ 0.90`, `nDCG@10 ≥ 0.80` on a 60-query golden set |
| Senior name lookup | Correct senior at **top-1** for ≥ 95% of name queries (Latin, Khmer script, and typos) |
| Abstention | No fabricated project or person (`precision ≥ 0.95` on 20 negative queries) |
| Metadata integrity | 100% of indexed projects carry `senior_id`, `title`, `year`, `program`, `supervisor` |
| Interface | Telegram only — no web app, no install, works on the phone students already use |
| Cost | **$0** — open-source models, self-hosted Qdrant and Ollama |
| Model policy | Both models **frozen** this semester; **fine-tuned next semester** |

---

## 0. What changed from v1.0

v1.0 was a campus-facts chatbot (timetables, policies) answering through a web assumption and Google Gemini. v2.0 is a person-anchored project archive answering through Telegram with self-hosted Gemma 4. The differences are not cosmetic.

| Dimension | v1.0 | v2.0 | Why |
| :--- | :--- | :--- | :--- |
| **Question answered** | "What time is my Friday class?" | "Who worked on Khmer OCR, and what did they build?" | Objective redefined |
| **Primary entity** | Policy passage | **Senior** (person) + **Project** (artifact) | Portfolios and networking are person-level |
| **Corpus** | Admin handbooks, timetables | Student-authored reports, theses, slides, posters | Corpus is now student work |
| **Retrieval** | Dense-only, cosine `top_k=5` | **Hybrid**: dense + sparse + payload filters, RRF, grouped by project | Names and acronyms are rare tokens dense vectors blur |
| **Generator** | Gemini `gemini-2.5-flash` (paid API) | **Gemma 4** via Ollama; extractive quoting by default | Open weights, offline, no API bill |
| **Interface** | Unspecified (web app implied) | **Telegram bot** | Students already live in Telegram; nothing to host or install |
| **Model lifecycle** | Frozen forever | Raw model now → **fine-tuned next semester** | Matches the two-semester plan |
| **Accuracy claim** | "0% hallucination, 100% accuracy" | Measurable retrieval and abstention metrics | Absolutes cannot be tested or defended |

**Retired:** timetable table parsing, `day_of_week` filters, the Gemini dependency, the web-UI assumption, and the claim of zero hallucination.

---

## 1. Product overview

### 1.1 Problem

RUPP produces hundreds of senior projects every year. After defense they become **write-only artifacts**: a PDF in a folder, a printed copy in a cabinet, a slide deck buried in a group chat.

| Stakeholder | Today's pain |
| :--- | :--- |
| **Junior students** choosing a topic | Cannot discover prior work; ideas get re-invented every cohort |
| **Final-year students** | Cannot find related work to cite, extend, or benchmark |
| **Supervisors** | Cannot quickly check whether a proposed title duplicates an existing project |
| **Everyone** | Cannot find *who* built something, so there is no path to ask them anything |

A folder of PDFs fails because the two real questions are different in kind:

- **Semantic:** *"projects about Khmer OCR"* — needs meaning-level matching.
- **Entity:** *"what did Sok Dara build?"* — needs exact person resolution, and no amount of semantic similarity solves it.

A static directory website solves the second and fails the first. A fine-tuned chatbot alone answers both *plausibly* and grounds neither. This system solves both, and grounds both.

### 1.2 Target users

| User group | Primary need | Success looks like |
| :--- | :--- | :--- |
| **Junior DSE/ITE students** | Topic discovery | Types "khmer ocr" → 5 real past projects with abstracts and authors |
| **Final-year students** | Prior-art lookup | Finds related work and the senior behind it in one message |
| **Supervisors** | Duplication checks | Searches a proposed title → sees the closest existing projects |
| **Seniors / alumni** | Visibility, networking (opt-in) | Portfolio is discoverable; contact works if they consented |

All four arrive through one **Telegram link**. No install, no account, no app store. A portfolio link (`t.me/<bot>?start=senior_s-2024-014`) can be dropped into a class group chat, which is how discovery actually spreads on campus.

### 1.3 Why retrieval, not a chatbot alone

| Dimension | Static directory | Chatbot only | **RUPP-SPKB** |
| :--- | :--- | :--- | :--- |
| Topic search | Fails — needs exact title | Plausible, ungrounded | Semantic + lexical over real text |
| Name lookup | Works if hand-tagged | Hallucination-prone | Payload filter + alias table + fuzzy match |
| New project added | Manual page + tagging | Needs retraining | Ingest + index; **no retraining** |
| Evidence | None | None | Page-level citations from the actual report |
| Failure mode | Misses results | Invents a person or project | **Abstains and says so** |

---

## 2. Requirement traceability (charter → requirements)

This table exists so the report can defend scope decisions in one glance.

| Charter line | Implemented by | Verified by |
| :--- | :--- | :--- |
| *Searchable knowledge base of past senior projects* | FR-02, FR-03, FR-04 | Ingestion report, point-count assertions |
| *Look up seniors by name* | FR-06 | 25-query name slice (Latin, Khmer, typos) |
| *View their project portfolios* | FR-07, FR-12 | Portfolio rendering ≥ 4096 chars, pagination, metadata-only case |
| *Connect with them* | FR-10 | Consent matrix test: opt-in / opt-out / relay |
| *Ingestion pipeline (parsing, indexing)* | FR-01…FR-04 | Parse-quality report, payload schema assertions |
| *Retrieval pipeline (names + metadata)* | FR-05…FR-08 | `eval/run_eval.py` per slice |
| *LangChain orchestrates loading, chunking, storage, retrieval* | FR-02, FR-03, FR-04, FR-05, §6 stack | Code review: LangChain document loaders/objects, splitters, vector store integration, and retrieval runnable interface visible across pipelines |
| *Open-source pre-trained model this semester* | bge-m3 (embeddings) + Gemma 4 (generation) | Model tags pinned and logged in every run |
| *Fine-tuning next semester* | FR-11, §11 | `datasets/build_training_pairs.py` export validates |

---

## 3. Scope

### 3.1 In scope — this semester

- **Corpus intake** of permission-cleared senior project artifacts: `PDF` reports/theses, `DOCX`, `PPTX`/PDF slides, `MD` notes, plus optional repo/demo links.
- **Curated metadata manifest** (one row per project, one per senior) as the authoritative identity layer.
- **Structure-aware chunking** (abstract atomic; references dropped; breadcrumbs with title/author/year/page).
- **Hybrid indexing** into Qdrant: bge-m3 dense (1024-d) + sparse lexical vectors, payload indexes for filters and full-text name matching.
- **Retrieval**: topic search, senior name lookup (incl. Khmer script + typos), portfolio assembly, facet browsing.
- **Telegram bot**: `/start`, `/help`, `/search`, `/senior`, `/year`, deep links, inline keyboards, pagination, disambiguation, feedback buttons.
- **Grounded answers**: extractive by default; **Gemma 4** synthesis behind a feature flag with a citation validator.
- **Query + feedback logging** shaped for next semester's fine-tuning.

### 3.2 In scope — next semester (design must not block it)

- Fine-tune the **embedding model** contrastively on the curated query↔project dataset.
- Optional **LoRA fine-tune of Gemma 4** (Unsloth or HF Transformers) for answer phrasing and Khmer output.
- A/B evaluation of fine-tuned vs frozen on the **same** golden set.

### 3.3 Out of scope

- Grade lookups, registration, fees, transcripts.
- Scraping social media or private groups for senior contact details.
- Publishing student IDs, phone numbers, or any non-consented personal data.
- Plagiarism detection between projects.
- Group-chat bot usage in Semester 1 (private chat only).

---

## 4. Data model

Correctness depends almost entirely on this model. **A project is not "a PDF" — it is a metadata row plus one or more documents.**

| Entity | Key fields | Source of truth |
| :--- | :--- | :--- |
| **Senior** | `senior_id`, `full_name_latin`, `full_name_khmer`, `name_normalized`, `program`, `cohort_year`, `supervisor_id`, contact fields, `consent_public_contact`, `consent_relay`, `status` | Curated manifest |
| **Senior alias** | `senior_id`, `alias_normalized`, `script` (`latin`/`khmer`) | Curated manifest — the Khmer↔Latin bridge |
| **Project** | `project_id`, `title_en`, `title_khmer`, `abstract`, `year`, `term`, `program`, `domain`, `tech_stack[]`, `keywords[]`, `repo_url`, `demo_url`, `permission` | Curated manifest |
| **Authorship** | `project_id` ↔ `senior_id`, `role` | Curated manifest |
| **Document** | `doc_id`, `project_id`, `doc_type`, `file_path`, `sha256`, `page_count`, `parser`, `parse_status` | Ingestion run |
| **Chunk** | `chunk_id`, `doc_id`, `section`, `page_number`, `text`, `breadcrumb` | Indexing run |
| **Query log** | `query_id`, `surface`, `tg_user_hash`, `raw_query`, `intent`, `resolved_senior`, `retrieved_json`, `clicked_project`, `rating`, `latency_ms` | Runtime |

**Why a manifest and not PDF scraping:** author names, supervisors, and years appear inconsistently on title pages, are often in Khmer script, and are sometimes simply wrong in the file. Extraction from page 1 can *suggest*; only a human-verified, git-tracked manifest can *decide*. Portfolio correctness is a product promise, so its source of truth must be reviewable.

---

## 5. Functional requirements

### FR-01: Manifest intake and validation
- **Description:** Ingestion starts from a versioned CSV manifest (`projects.csv` + `seniors.csv`) plus a corpus directory.
- **Acceptance criteria:**
  - Rows missing `project_id`, any author `senior_id`, `title_en`, `year`, `program`, `supervisor_id`, `abstract`, or `permission` fail validation with a per-row error.
  - Duplicate `project_id`, unknown `senior_id`, and missing corpus folders are rejected **before** anything writes to Qdrant.
  - `permission != "granted"` rows are reported and skipped, never silently indexed.
  - Re-running on an unchanged corpus is **idempotent** (SHA-256 corpus hash + deterministic point IDs → zero new points).
  - A manifest diff report lists projects added, updated, and removed.

### FR-02: Multi-format document parsing (LangChain Document Loading)
- **Description:** Project artifacts are loaded and parsed into page-anchored LangChain `Document` objects with standardized metadata.
- **Acceptance criteria:**
  - `PDF` born-digital → LangChain `PyMuPDFLoader` (or PyMuPDF parser), preserving page numbers and formatting.
  - Scanned PDF → Tesseract OCR fallback (`eng+khm`) when a page yields `< 20` characters; document flagged `parse_status = "ocr"`.
  - `DOCX` → `UnstructuredWordDocumentLoader` / python-docx with heading levels; `PPTX` → python-pptx, one section per slide; `MD` → LangChain markdown header splitter.
  - Parsed output standardizes into LangChain `Document(page_content=..., metadata=...)` containing `doc_id`, `project_id`, `page_number`, `parser`, `parse_status`, `sha256`.
  - A document that parses to zero usable text is marked `failed`, named in the report, and excluded from the index — never indexed as empty.

### FR-03: Structure-aware chunking (LangChain Text Chunking)
- **Description:** Chunks respect report structure using LangChain splitters so each chunk is readable and citable on its own.
- **Acceptance criteria:**
  - Default `chunk_size = 700` chars, `chunk_overlap = 100`, using LangChain `RecursiveCharacterTextSplitter` splitting on `["\n## ", "\n### ", "\n\n", "\n", ". ", " "]`.
  - The **abstract is one atomic chunk** (`section = "abstract"`) — the highest-precision chunk in the corpus.
  - `references` / `bibliography` / `appendix` sections are **excluded** (citation noise degrades ranking).
  - Slides chunk at 300 chars using LangChain splitter and are flagged non-primary.
  - Each chunk carries a breadcrumb: `[Project: … | Author(s): … | Year: … | Program: … | Section: … | p.N]` attached to its `page_content`.
  - Chunks shorter than 80 chars are dropped (headers, footers, page numbers).

### FR-04: Hybrid indexing in Qdrant (LangChain Vector Storage)
- **Description:** Each chunk is indexed into Qdrant using LangChain vector storage orchestration, managing dense semantic vectors and sparse lexical vectors in one point.
- **Acceptance criteria:**
  - Orchestration: LangChain `QdrantVectorStore` / Qdrant client adapter manages vector store collection lifecycle, upserts, and payload indexing.
  - Dense: `BAAI/bge-m3`, 1024 dimensions, L2-normalized, named vector `dense`.
  - Sparse: bge-m3 lexical weights, named vector `lexical`.
  - Collection: `Distance.COSINE`, HNSW `m=16`, `ef_construct=200`.
  - Payload indexes: keyword on `project_id`, `doc_id`, `doc_type`, `program`, `supervisor_id`, `senior_ids`; integer on `year`; **full-text** on `author_names_normalized`.
  - Post-run assertion: indexed points == sum of chunk counts; every point has the required payload keys; every `senior_ids` value resolves to an active senior.

### FR-05: Topic search (LangChain Query Retrieval)
- **Description:** Free-text queries return the most relevant **projects** orchestrated through a LangChain retrieval interface (`BaseRetriever` / LCEL runnable chain).
- **Acceptance criteria:**
  - Dense prefetch (40) + sparse prefetch (40) fused with RRF (`k = 60`), limit 10.
  - Results **grouped by `project_id`** after fusion and before truncation, so top 5 means five distinct projects.
  - Retrieval pipeline exposes a LangChain-compatible retriever interface returning LangChain `Document` objects with scoring and project payloads.
  - Each result shows title, authors, year, program, supervisor, a verbatim snippet, the source page, and `matched_by` (`dense`, `lexical`, or both).
  - English and Khmer queries both work on a corpus containing the topic.
  - Below the relevance gate: *"No indexed project matches this query"* plus up to 3 weak matches explicitly labelled **low confidence**.

### FR-06: Senior name lookup and entity resolution
- **Description:** A name-shaped query resolves to exactly one senior, then to their projects.
- **Acceptance criteria:**
  - Normalization: Unicode NFKC, case-folding, Latin diacritic stripping, whitespace collapse; Khmer script preserved.
  - Resolution order: (1) exact `senior_alias` match → (2) Qdrant full-text index on `author_names_normalized` → (3) `rapidfuzz.token_set_ratio ≥ 88` over the directory.
  - `Sok Dara`, `សុខ ដារា`, and `sokdaraa` must reach the **same** `senior_id`.
  - Two seniors sharing a name are **never merged**. The bot shows an inline keyboard of candidates with program + cohort year.
  - An unknown name returns an explicit not-found reply plus up to 3 closest candidates marked *not confirmed*.

### FR-07: Portfolio assembly
- **Description:** One card per senior, assembled from metadata and enriched with document snippets.
- **Acceptance criteria:**
  - Shows name (Latin + Khmer), program, cohort, supervisor, and all indexed projects newest-first.
  - Renders from SQLite **without Qdrant** (metadata is the product; vectors are the search aid).
  - A senior with zero parsed documents still yields a valid, clearly-labelled metadata-only portfolio.
  - Reachable from every search result via an inline `Portfolio` button.

### FR-08: Facet browsing
- **Description:** Filter-based exploration without a semantic query.
- **Acceptance criteria:**
  - Filters: `year`, `program`, `supervisor`, `domain`, `tech_stack`.
  - Filters are **Qdrant payload filters** (pre-filtering), never Python post-filtering.
  - Active filters appear as removable chips with `Clear all`, encoded in the deep link so views can be shared.
  - Facet counts come from the metadata store (`GROUP BY`), not from the current result page.

### FR-09: Grounded answer synthesis
- **Description:** Question-shaped queries may be answered in prose, strictly from retrieved chunks.
- **Acceptance criteria:**
  - Default is **extractive**: quotes + citations, no generation, no hallucination surface.
  - LLM mode uses **Gemma 4** via Ollama (`gemma4:12b-it-qat` by default), `temperature = 0.0`, `num_ctx = 8192`, `num_predict = 600`.
  - **`num_ctx` must be set explicitly.** Ollama's small default window silently truncates retrieved evidence, which produces confident wrong answers — the exact failure this project is graded on.
  - The prompt receives only the top 5 grouped projects (~2–3K tokens). Gemma 4's 128K–256K window is **not** used as an excuse to dump the corpus: extra context costs latency and citation accuracy.
  - Every factual sentence carries `[Project: <title>, p.<n>]`.
  - A deterministic validator rejects any answer citing a project or page outside the retrieved set; on failure the system serves the extractive answer and logs it.
  - Below the gate, Gemma 4 is **never called**.

### FR-10: Connect with a senior (consent-gated)
- **Description:** The portfolio explains how to reach the senior, but only within consent.
- **Acceptance criteria:**
  - Contact fields render only when `consent_public_contact = true`.
  - Public surface is professional: GitHub, LinkedIn, personal site, portfolio link.
  - University email only if explicitly opted in; phone numbers are never stored in the searchable index.
  - Without consent: *"This senior has not opted in to public contact. Ask your department secretariat to forward a message."*
  - **Relay:** if `consent_relay = true` and the senior has started the bot, a student can send a message through the bot. Neither party sees the other's Telegram identity.

### FR-11: Query logging and feedback capture
- **Description:** Every interaction is logged in a shape that can become training data.
- **Acceptance criteria:**
  - Logged: surface, hashed Telegram user id, raw and normalized query, intent, resolved senior, retrieved ids with ranks and scores, click, rating, latency.
  - Feedback is inline (`👍` / `👎` buttons) and non-blocking.
  - `datasets/build_training_pairs.py` emits `(query, positive_project_id, hard_negatives[])` with a schema version.
  - Export is reproducible from logs alone.

### FR-12: Telegram bot surface
- **Description:** The bot is the only user-facing interface; every capability is reachable as chat.
- **Acceptance criteria:**
  - Commands: `/start`, `/help`, `/search <q>`, `/senior <name>`, `/year <yyyy>`, `/about`.
  - Plain text is treated as a topic search — no command knowledge required.
  - Deep links: `?start=senior_<senior_id>` opens a portfolio; `?start=proj_<project_id>` opens a project.
  - Results arrive as project cards with inline buttons: `Abstract`, `Portfolio`, `Repo`, `Next ▶`.
  - Ambiguous names render as a candidate keyboard, never a guess.
  - Every message stays under **4096 characters**; long portfolios paginate rather than truncate.
  - `parse_mode = HTML` (MarkdownV2 breaks on `_`, `*`, `[`, `(` in titles).
  - `sendChatAction("typing")` fires immediately — no dead chat.
  - Private chat only in Semester 1.

### FR-13: Delivery, rate limits, reliability
- **Description:** Telegram enforces hard limits; the bot degrades gracefully instead of stalling or getting banned.
- **Acceptance criteria:**
  - Outgoing messages serialized **per chat** with ≥ 1 s spacing; globally below ~30 msg/s.
  - Placeholder edits throttled to ≤ 1 per 2 s (there is no token streaming in Telegram).
  - `429` responses honour `retry_after`; retry once, then log and drop.
  - Duplicate identical queries within 30 s are served from cache.
  - Bot token comes from the environment only; never logged, never committed.

### FR-14: Semester-2 readiness
- **Description:** The architecture must make fine-tuning a re-index plus a re-train, not a redesign.
- **Acceptance criteria:**
  - The embedding model is loaded through one interface (`ingestion/embedder.py`), so swapping it is a single change + full re-index.
  - The golden set is frozen and versioned before any fine-tuning begins.
  - Every eval run records the model tags used, so before/after comparisons are legitimate.

---

## 6. Non-functional requirements

| Requirement | Target |
| :--- | :--- |
| **Search latency** | `P50 < 800 ms`, `P95 < 2.0 s` (hybrid retrieval, CPU, no LLM) |
| **Answer latency** | `P50 < 8 s` with `gemma4:12b-it-qat` on CPU; `< 2 s` on a consumer GPU |
| **First feedback to user** | Visible `typing` action in **< 1 s**, always |
| **Cost** | `$0` recurring — open weights, self-hosted Qdrant + Ollama |
| **Offline** | Full ingestion + retrieval after model pull; only Telegram needs the network |
| **Reproducibility** | Pinned model tags and Qdrant version; fixed manifest + corpus hash → identical point count |
| **Footprint** | Laptop-capable: Gemma 4 12B QAT ≈ 7.2 GB, bge-m3 ≈ 2 GB, Qdrant ≈ 0.5 GB |
| **Corpus scale** | ≥ 150 projects / ≥ 3,000 chunks without architectural change |
| **Freshness** | New project live in < 60 s, no retraining |

---

## 7. Telegram interaction contract

| Interaction | Behaviour |
| :--- | :--- |
| `/start` | One-paragraph explanation + example queries (a topic, a name, a year) |
| `/start senior_<id>` | Opens that portfolio directly (shared links) |
| `/search khmer ocr` or bare `khmer ocr` | Topic search, top 5 project cards |
| `/senior Sok Dara` | Portfolio, or candidate keyboard if ambiguous |
| `សុខ ដារា` | Same path as the Latin name, via the alias table |
| `/year 2024` | Facet browse with removable chips |
| `Portfolio` button | Portfolio of that project's author |
| `Next ▶` | Next page of a long result set or portfolio |
| `👍` / `👎` | Logged rating tied to that `query_id` |
| `/help` | Commands + what the bot can and cannot answer |

**Message anatomy for a project card:**

```text
<b>Khmer Handwritten Text Recognition with Tesseract</b>
Sok Dara, Chan Sreyneang · 2024 · DSE · sup. sup-003

"…we segment Khmer handwriting and run Tesseract with a custom khm
traineddata, reaching 81% character accuracy on 500 scans…"
<i>— Khmer Handwritten Text Recognition with Tesseract, p.4</i>

<i>matched: semantic + keyword</i>
[ Abstract ] [ Portfolio ] [ Repo ]
```

---

## 8. Privacy, ethics, consent

This system indexes **real students' names and work**. That deserves rules, not a footnote.

| Rule | Implementation |
| :--- | :--- |
| Consent gates contact | `consent_public_contact` / `consent_relay` flags per senior |
| No sensitive identifiers | Student IDs, national IDs, phone numbers, grades are never indexed |
| Takedown | `senior.status = "withdrawn"` removes the portfolio and its chunks on the next run |
| Copyright | Only projects with `permission = granted` are ingested |
| Telegram identity | Stored only as a salted hash, for rate limiting and feedback |
| No cold messaging | Relay works only after the senior starts the bot and opts in |
| Transparency | Every result states why it matched and links to its source |

---

## 9. Success metrics

| Metric | Semester 1 | Semester 2 (post fine-tune) |
| :--- | :---: | :---: |
| `Recall@5` — topic queries (35) | ≥ 0.90 | ≥ 0.95 |
| `MRR@10` — topic queries | ≥ 0.75 | ≥ 0.85 |
| `nDCG@10` — topic queries | ≥ 0.80 | ≥ 0.88 |
| Senior top-1 accuracy (25 queries) | ≥ 0.95 | ≥ 0.98 |
| Abstention precision (20 negatives) | ≥ 0.95 | ≥ 0.97 |
| Answer faithfulness (LLM mode) | ≥ 0.90 | ≥ 0.94 |
| Bot delivery success | ≥ 99.5% | ≥ 99.5% |
| Ingestion success rate | ≥ 98% of manifest rows | ≥ 99% |
| Indexed projects (demo) | ≥ 40 real projects | ≥ 150 |

Metrics are reported **per slice**, never only as an average — a strong English score can hide a broken Khmer path.

---

## 10. Acceptance criteria (Semester 1 sign-off)

- [ ] A manifest of ≥ 40 permission-cleared projects validates and ingests idempotently.
- [ ] `PDF`, `DOCX`, `PPTX`, and `MD` all parse; a scanned PDF triggers OCR and is flagged.
- [ ] Qdrant holds dense + sparse vectors, all payload indexes, and the asserted point count.
- [ ] `khmer ocr text recognition` returns ≥ 3 correct projects within the top 5.
- [ ] `Sok Dara`, `សុខ ដារា`, and `sokdaraa` all resolve to the same senior and open the same portfolio.
- [ ] A duplicated name shows a candidate keyboard and merges nothing.
- [ ] A non-consenting senior's portfolio shows no contact fields and no relay button.
- [ ] `quantum computing lab schedule 2019` abstains with zero fabricated projects.
- [ ] Facet filters pre-filter correctly and survive a shared deep link.
- [ ] A portfolio longer than 4096 characters paginates instead of truncating.
- [ ] 50 rapid queries do not trigger a ban; `retry_after` is honoured.
- [ ] With `ENABLE_LLM=true`, every citation in the answer exists in the retrieved set; a forced bad citation falls back to extractive.
- [ ] `eval/run_eval.py` prints Recall@5, MRR, nDCG@10, name top-1, and abstention precision by slice.
- [ ] `datasets/build_training_pairs.py` produces a valid training-pairs file.

---

## 11. Risks and open questions

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| **Khmer retrieval is weaker than English** | Core promise under-delivers | `khmer-nltk` segmentation before sparse encoding; curated alias table; separate Khmer eval slice |
| **Metadata curation is manual and slow** | Bottleneck past ~150 projects | Strict schema + diff report; LLM-assisted *suggestion*, human confirmation |
| **Name collisions** | Wrong portfolio about a real person | Never auto-merge; disambiguate by program + year |
| **Gemma 4 too slow on a laptop** | Demo risk | Extractive is the default; 12B QAT recommended, E4B documented fallback |
| **Telegram blocked on campus Wi-Fi** | Nobody can reach the bot | Test on mobile data; keep the offline evaluator as a fallback demo |
| **Telegram rate limits mid-demo** | Bot stalls in front of the examiner | Per-chat serialization, duplicate cache, pre-warmed model |
| **Privacy complaint** | Ethical + reputational | Consent flags, takedown path, hashed identities |
| **`latest` tag drift** | Eval numbers silently invalid | Pin every model tag; log the loaded tag at startup |

**Open questions for the supervisor:** (1) Does the department hold signed permission to archive these reports? (2) Which cohorts may be indexed for this demo? (3) Should supervisor names be publicly searchable, or restricted to logged-in RUPP users? (4) Should the knowledge-gap report (zero-result queries) be a semester deliverable?

---

## 12. Roadmap

| Week | Milestone |
| :--- | :--- |
| 1–2 | Manifest schema; collect and permission-clear 40 projects; validation script |
| 3 | Parsers (PDF/DOCX/PPTX/MD) + OCR fallback; parse-quality report |
| 4 | Chunker, breadcrumbs, section detection, references exclusion |
| 5 | bge-m3 embedder + Qdrant hybrid index; point-count assertions |
| 6 | Hybrid retriever, RRF, project grouping; **baseline eval #1** |
| 7 | Entity resolution + alias table; name-query eval |
| 8 | Telegram bot: handlers, commands, deep links, rate-safe sender |
| 9 | Portfolio pagination, facet chips, consent card + relay |
| 10 | Logging + inline feedback; **Gemma 4 behind `ENABLE_LLM`**; training-pair export |
| 11 | Golden set (60 queries) + full eval; tune `prefetch_k`, `rrf_k`, abstain threshold |
| 12 | Report, slides, demo dry run on mobile data |
| Semester 2 | Fine-tune embedder on exported pairs; LoRA-tune Gemma 4; A/B vs frozen baseline |
