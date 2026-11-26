# System Architecture Specification: RUPP Senior Project Knowledge Base (RUPP-SPKB v2.0)

**Project:** Searchable knowledge base of past RUPP senior projects — name lookup, portfolios, topic search
**Institution:** Royal University of Phnom Penh (RUPP), Faculty of Engineering (FE), Dept. of ITE / Data Science & Engineering (DSE)
**Course:** Project Practicum I (PP)
**Supervised by:** Lecturer Mr. Chap Chanpiseth
**Group 1 Researchers:** Ngoun Nuongswayumphou • Cheat Kimlang
**Frontend:** Telegram bot (`python-telegram-bot` 22.8)
**LLM:** Gemma 4 via Ollama (`gemma4:12b-it-qat`)
**Embeddings:** `BAAI/bge-m3` (dense 1024-d + sparse lexical)
**Vector store:** Qdrant (hybrid, `Distance.COSINE`, HNSW)
**Orchestration:** LangChain (loaders, splitters, document objects, chain glue)
**Metadata store:** SQLite
**Companion docs:** `Senior_Project_KB_Prd_V2_0.md` • `Senior_Project_KB_End_To_End_Pipeline_V2_0.md`
**Supersedes:** `Rupp_Rag_Architecture_Spec.md` (v1.0)

---

## TL;DR

Four planes, not a single chain:

| Plane | Runs | Job | Output |
| :--- | :--- | :--- | :--- |
| **I. Interface** | Online, per message | Telegram update → command/plain text → **core service** → formatted message + inline keyboards | A chat answer, a portfolio card, or a disambiguation keyboard |
| **A. Ingestion** | Offline, per batch | Validate manifest → parse → structure-aware chunk → dense + sparse embed → upsert | A filterable hybrid index |
| **B. Retrieval** | Online, per query | Intent route → hybrid search (RRF) → project grouping → entity resolution → portfolio / answer | Ranked projects, a portfolio, citations |
| **C. Flywheel** | Passive | Log queries, clicks, ratings → export training pairs | Next semester's fine-tuning dataset |

Three decisions define this architecture. Everything else follows from them.

**1. Retrieval is hybrid, never dense-only.** A senior's name is a rare token. Dense embeddings place `"Sok Dara"` near other personal-name contexts, not near that specific person — and `"Sok Sophea"` sits just as close. Person lookup must go through **exact payload filters, a full-text index, and a curated alias table**, with lexical matching for topic queries. Cosine similarity alone cannot be the name mechanism.

**2. Identity comes from the manifest, not from the PDF.** Author names on title pages are inconsistent, often Khmer, sometimes absent. A project is a **metadata row plus evidence documents**; portfolios and contact cards are built from SQLite and stay correct even if Qdrant is offline.

**3. The Telegram bot is a thin adapter, and that is enforced.** The core is one library entry point — `answer.service.answer(query, ...)`. Handlers own state, formatting, and rate limits; they own **no retrieval logic**. This is what lets `eval/run_eval.py` measure exactly what users see, without going through Telegram. If retrieval logic leaks into a handler, evaluation stops being reproducible and the Semester-2 fine-tuning comparison becomes meaningless.

---

## How to read this document

- §1–§2 — framing and the assumptions everything depends on.
- §3–§5 — system shape and request flows. Read these first if you read only once.
- §6–§11 — stack, data contracts, pipeline internals, Telegram layer, answer layer.
- §12–§17 — fine-tuning bridge, evaluation, deploy, layout, risks.
- Appendices — code you will actually edit.

---

## 1. Overview

### 1.1 What the system is

Two pipelines over a small, high-value, **person-anchored** corpus:

- **Corpus unit:** a *senior project* — one metadata row plus one to three documents (report, slides, poster).
- **Primary entity:** the **senior**. One senior → many projects; one project → many seniors.
- **Five intents**, each with its own retrieval path: `TOPIC_SEARCH`, `SENIOR_LOOKUP`, `PORTFOLIO`, `FACET_BROWSE`, `ANSWER`. A chatbot with one path cannot serve these five well; that single-path assumption is the main lesson carried over from v1.0.

### 1.2 Design principles

| # | Principle | Consequence |
| :-- | :--- | :--- |
| P1 | Identity from metadata, never from document text | Curated manifest gates ingestion; extraction may only suggest |
| P2 | Retrieve structurally | Separate paths for person lookup (filter + lexical) and topic search (dense + sparse fusion) |
| P3 | Return projects, not chunks | Group by `project_id` before ranking is shown |
| P4 | Abstain over inventing | Deterministic relevance gate precedes any generation |
| P5 | Every answer is auditable | Results carry `project_id`, `doc_id`, `page_number`, and the match reason |
| P6 | Records accrue value | Every query logged in training-pair-compatible shape from day one |
| P7 | Free and offline-capable | Open weights, self-hosted Qdrant and Ollama, zero paid APIs |
| P8 | The interface is replaceable | Core service is UI-agnostic; Telegram is one adapter |

### 1.3 Deliberate simplifications

- **No microservices.** One bot process, one ingestion CLI, two infrastructure containers.
- **No auth layer.** Consent is enforced at the data level (flags + query filters), not by login.
- **No LLM by default.** Extractive answers are deterministic, fast, and cannot hallucinate.
- **No cross-encoder reranker yet.** RRF + grouping is sufficient at this corpus size; a reranker is a Semester-2 upgrade with measured justification, not a default.

---

## 2. Assumptions

| Assumption | Why it matters | If false |
| :--- | :--- | :--- |
| Permission-cleared documents available | Legal/ethical precondition | Demo on a consented subset; publish the pipeline, not the corpus |
| ≥ 40 projects for the demo | Evaluation needs mass | Report confidence intervals, not point estimates |
| Mostly born-digital English PDFs | bge-m3's strength | OCR + Khmer scoring path activates; expect lower recall |
| Every project has ≥ 1 author in the manifest | Portfolios are person-anchored | Project still indexes; no portfolio link |
| One laptop-class machine (8–16 GB) | Model choices | Drop to `gemma4:e4b`; extractive-only demo |
| Telegram reachable from the demo network | Interface is the product | Fall back to the CLI evaluator (documented path) |

**Deferred to next semester:** cross-encoder reranking, fine-tuned embedder, LoRA-tuned Gemma 4, authentication, multi-campus deployment.

---

## 3. Architecture at a glance

```text
==================================================================================================
PLANE I — INTERFACE (Telegram)                                   always online, stateless-ish
==================================================================================================

  [Student phone] --Telegram--> [Bot API] --> bot/handlers.py
                                                 |  commands, plain text, deep links, callbacks
                                                 |  (NO retrieval logic lives here)
                                                 v
                                          answer.service.answer(query, ctx)
                                                 ^
                                                 |  single entry point
==================================================================================================
PLANE B — RETRIEVAL
==================================================================================================

  query
    v
  +--------------------------------------------------------------------------------------------+
  | B1. ROUTE (retrieval/intent.py)                                                            |
  |   normalize (NFKC, casefold, diacritics, whitespace, Khmer preserved)                      |
  |   intent: TOPIC_SEARCH | SENIOR_LOOKUP | PORTFOLIO | FACET_BROWSE | ANSWER                  |
  |   entities: years, programs, name spans, tech terms                                        |
  +--------------------------------------------------------------------------------------------+
      |                       |                                |
      v                       v                                v
  +------------------+  +--------------------------+  +----------------------------+
  | B2a HYBRID       |  | B2b ENTITY RESOLUTION    |  | B2c FACETS                 |
  | dense prefetch 40|  | 1 alias table (exact)    |  | Qdrant payload filter      |
  | sparse prefetch40|  | 2 Qdrant TEXT index      |  | + SQLite GROUP BY counts   |
  | RRF fusion k=60  |  | 3 rapidfuzz >= 88        |  +----------------------------+
  | + payload filter |  | ambiguous -> keyboard    |
  +------------------+  +--------------------------+
      |                       |
      v                       v
  +------------------+  +--------------------------+
  | B3 PROJECT GROUP |  | B2b-ii PORTFOLIO         |
  | group by         |  | SQLite (authoritative)   |
  | project_id, max  |  | + Qdrant snippets        |
  | score, top 5     |  +--------------------------+
  +------------------+
      |                       |
      +-----------+-----------+
                  v
  +--------------------------------------------------------------------------------------------+
  | B4 ANSWER (answer/service.py)                                                              |
  |   relevance gate -> abstain (no LLM call)                                                  |
  |   default EXTRACTIVE  |  ENABLE_LLM=true -> Gemma 4 (Ollama), temp 0, num_ctx 8192          |
  |   citation validator: every [Project: X, p.N] must exist in the retrieved set              |
  |   consent gate on contact card                                                             |
  +--------------------------------------------------------------------------------------------+
                  |
                  v
            results / portfolio  -->  query_log  -->  PLANE C


==================================================================================================
PLANE A — INGESTION (offline)
==================================================================================================

  [projects.csv + seniors.csv]                [data/corpus/<project_id>/*]
              |                                          |
              +------------------+-----------------------+
                                 v
  +--------------------------------------------------------------------------------------------+
  | A1 VALIDATE   schema -> refs -> permission -> files -> corpus_hash   (fail fast)            |
  +--------------------------------------------------------------------------------------------+
  +--------------------------------------------------------------------------------------------+
  | A2 PARSE      pymupdf | tesseract(eng+khm) | python-docx | python-pptx | md splitter        |
  |               -> ParsedDocument(pages[]) with page anchors, parse_status                   |
  +--------------------------------------------------------------------------------------------+
  +--------------------------------------------------------------------------------------------+
  | A3 CHUNK      abstract atomic | body 700/100 | slides 300 | drops references               |
  |               breadcrumb + payload assembly                                                |
  +--------------------------------------------------------------------------------------------+
  +--------------------------------------------------------------------------------------------+
  | A4 EMBED+UPSERT  bge-m3 -> dense(1024) + sparse(lexical) -> Qdrant (uuid5 deterministic)   |
  |               assert points == chunks; payload complete; senior_ids valid                  |
  +--------------------------------------------------------------------------------------------+
                                 v
        [(Qdrant :6333)]  [(SQLite kb.sqlite3)]


==================================================================================================
PLANE C — FLYWHEEL (passive)
==================================================================================================
  query_log(raw, normalized, intent, resolved, retrieved+ranks+scores, click, rating, latency)
      |--> eval/golden_set.csv
      `--> datasets/build_training_pairs.py --> (query, positive, hard_negatives) [sem 2]
```

---

## 4. Components

| Component | Responsibility | Module | Critical constraint |
| :--- | :--- | :--- | :--- |
| **Manifest validator** | Schema, referential integrity, corpus hash | `ingestion/validate_manifest.py` | Abort before any Qdrant write |
| **Parsers** | Format-specific text + page anchors | `ingestion/parsers/` | Never emit an empty document silently |
| **Chunker** | Section-aware splitting + breadcrumbs | `ingestion/chunker.py` | Abstract atomic; references dropped |
| **Embedder** | bge-m3 dense + sparse | `ingestion/embedder.py` | Same model revision for index and query |
| **Indexer** | Collection contract, indexes, upsert | `ingestion/indexer.py` | Deterministic IDs → idempotent |
| **Metadata store** | Seniors, projects, authorship, consent, logs | `store/db.py` | Consent lives here, not in Qdrant payloads |
| **Intent router** | Classify + extract entities | `retrieval/intent.py` | Rules first; no LLM required |
| **Hybrid retriever** | Dense+sparse prefetch, RRF, filters | `retrieval/hybrid.py` | Group by project before truncation |
| **Entity resolver** | Name → `senior_id` | `retrieval/entity.py` | Never auto-merge ambiguous names |
| **Answer service** | Gate, extractive/LLM answer, citations | `answer/service.py` | **The one entry point for bot and eval** |
| **Gemma client** | Ollama chat call | `answer/prompts.py` | `num_ctx` explicit; tag pinned |
| **Bot handlers** | Update → core service call | `bot/handlers.py` | **No retrieval logic** |
| **Formatter** | HTML escape, pagination, keyboards | `bot/format.py` | `parse_mode=HTML`, ≤ 4096 chars, callback ≤ 64 B |
| **Sender** | Per-chat throttle, 429, dedupe | `bot/sender.py` | ≤ ~30 msg/s; never block the loop |
| **Session state** | Disambiguation + pagination cursors | `bot/state.py` | TTL'd, per chat, non-durable |
| **Relay** | Consent-gated message forwarding | `bot/relay.py` | Never reveal either identity |
| **Evaluator** | Golden-set metrics by slice | `eval/run_eval.py` | Bypasses Telegram entirely |
| **Exporter** | Query log → training pairs | `datasets/build_training_pairs.py` | Schema-versioned |

---

## 5. Request flows

### 5.0 Telegram ingress — the envelope around every flow

```text
Telegram update (message | callback_query)
        |
        v
+---------------------------------------------------------------------------+
| bot/handlers.py                                                           |
|  1. Private chat only (semester 1); ignore edited messages                |
|  2. Deduplicate: identical query from same chat within 30 s -> cache      |
|  3. sendChatAction("typing") immediately                                  |
|  4. Map to ONE core call:                                                 |
|       /search <q> | plain text  -> answer.service.answer(q)               |
|       /senior <name>            -> resolve_senior(name)                   |
|       ?start=senior_<id>        -> portfolio(id)                          |
|       callback "pf:<id>"        -> portfolio(id)                          |
|       callback "pg:<id>:2"      -> portfolio(id, page=2)                  |
|  5. Log the interaction (hashed tg user id only)                          |
+---------------------------------------------------------------------------+
        |
        v
+---------------------------------------------------------------------------+
| bot/format.py     escape -> paginate <=4096 -> inline keyboard (<=64 B)   |
+---------------------------------------------------------------------------+
        |
        v
+---------------------------------------------------------------------------+
| bot/sender.py     per-chat lock, >=1 s gap, global <30/s, honour 429      |
+---------------------------------------------------------------------------+
```

**Two Telegram constraints shape the code:**

1. **`callback_data` is capped at 64 bytes.** Buttons carry compact refs (`pf:s-2024-014`, `pg:s-2024-014:2`); long state (candidate lists, facet combinations) lives in `bot/state.py` under a short key.
2. **There is no streaming.** One final message, or a throttled placeholder edit (≤ 1 per 2 s). Fast generation ≠ fast delivery: Telegram rate-limits `editMessageText`, so an eager bot produces `429`s instead of a smoother answer.

### 5.1 Topic search — `"projects about Khmer OCR"`

1. **Route** → `TOPIC_SEARCH`; expand nothing; keep the raw query for the lexical path.
2. **Hybrid retrieval**, one Qdrant call:
   - `dense` prefetch, limit 40
   - `lexical` prefetch, limit 40
   - `Fusion.RRF`, limit 10, optional payload filter
3. **Group by `project_id`** → max score per project → top 5 projects.
4. **Gate** → best chunk below threshold: abstain text + up to 3 weak matches labelled low confidence.
5. **Render** → project cards with authors as `Portfolio` buttons and `Next ▶` pagination.

### 5.2 Senior lookup — `"Sok Dara"`, `"សុខ ដារា"`, `"sokdaraa"`

The most failure-prone flow, so it is fully specified:

```text
input
  |-- 1. normalize()  -> "sok dara"   (Khmer preserved verbatim; Latin casefolded,
  |                                    diacritics stripped, whitespace collapsed)
  |-- 2. SQLite alias table exact hit?
  |        hit  -> senior_id, confidence 1.00 ---------------------------+
  |        miss -> continue                                              |
  |-- 3. Qdrant TEXT index on author_names_normalized                    |
  |        1 senior -> confidence 0.90 ----------------------------------+
  |        several  -> continue                                          |
  |-- 4. rapidfuzz.token_set_ratio >= 88 over directory + aliases        |
  |        1 above  -> confidence 0.80 ----------------------------------+
  |        several  -> AMBIGUOUS                                         |
  |        none     -> NOT_FOUND                                         |
  +----------------------------------------------------------------------+
        AMBIGUOUS -> candidate keyboard (name, program, cohort). Never guess.
        NOT_FOUND -> explicit reply + <=3 closest, marked "not confirmed"
        resolved  -> portfolio (SQLite), newest project first
```

In Telegram, `AMBIGUOUS` is the natural case for an inline keyboard: one button per candidate, carrying `pick:<state_key>,<index>` rather than the name — a real candidate row (name + program + cohort) exceeds 64 bytes easily.

**Why the alias table exists:** one person appears as `សុខ ដារា`, `Sok Dara`, `DARA Sok`, and `Sok Dara (DSE-2024)`. Romanization is not reversible, so a curated `senior_alias` table is the only reliable bridge. It is filled during manifest curation, never guessed at runtime.

### 5.3 Portfolio — everything about one senior

Served from **SQLite** (cheap, exact, always available), enriched by Qdrant only for snippets and document links. This ordering is deliberate: metadata *is* the product; vectors are a search aid. A portfolio must still render when Qdrant is down.

### 5.4 Facet browse — `year=2024, program=DSE`

Payload filtering inside Qdrant for the ranked list; `GROUP BY` over SQLite for counts. Filtering happens **in Qdrant**, never in Python after retrieval — post-filtering destroys recall when filters are selective.

### 5.5 Multi-intent — `"Sok Dara's OCR project"`

Decompose into `SENIOR_LOOKUP("sok dara")` + `TOPIC_SEARCH("ocr")`, run both, then **intersect** on `senior_ids`. Empty intersection → return the senior's projects with an explicit note that none matched the topic. Neither half is silently dropped.

### 5.6 Failure modes

| Failure | Detection | Response |
| :--- | :--- | :--- |
| Qdrant unreachable | Connection error | Portfolio + facets still work from SQLite; search returns a degraded notice |
| Embedder unavailable | Model load error | Hard fail with a clear message; **never** silently fall back to lexical-only (changes ranking semantics) |
| Empty index | Point count == 0 | Abort startup with a readable error, not an empty chat |
| Parse produced 0 chunks | Post-parse assertion | `parse_status = "failed"`, reported, excluded |
| Gemma 4 timeout | Timeout | Extractive answer; the user always gets sources |
| Ollama down while `ENABLE_LLM=true` | Connection refused | Extractive answers, log once, never fail the search |
| Citation validator rejects | Missing/invalid citation | Serve extractive answer, log the event |
| Telegram `429` | `retry_after` | Sleep, retry once, then log and drop |
| Malformed update | Handler exception | Short capability hint; polling loop must never die |
| Callback state expired | Missing key | Reprompt with the original query |

---

## 6. Tech stack

| Layer | Choice | Why | Fallback |
| :--- | :--- | :--- | :--- |
| Language | Python 3.11 | Ecosystem maturity for all components | — |
| Orchestration | **LangChain** (`langchain`, `langchain-core`, `langchain-text-splitters`, `langchain-qdrant`) | Required by the practicum; orchestrates the 4 core stages: (1) document loading into standard `Document` objects, (2) recursive structure-aware chunking, (3) vector storage via Qdrant vector store integration, and (4) query retrieval chains (`BaseRetriever` / LCEL) | Native Qdrant client adapter where low-level RRF prefetch is optimized |
| Vector DB | **Qdrant** | Native named dense + sparse vectors, RRF fusion, payload filtering | Local mode for smoke tests only |
| Dense embeddings | `BAAI/bge-m3` (1024-d) | Strong multilingual retrieval; one model also yields sparse weights | `intfloat/multilingual-e5-large`; `all-MiniLM-L6-v2` for CPU demos |
| Sparse embeddings | bge-m3 `lexical_weights` | Learned sparse beats plain BM25 on short technical text | `rank_bm25`, `FastEmbedSparse` |
| Khmer handling | `khmer-nltk` | Khmer has no spaces; lexical matching needs segmentation | Alias table only |
| Fuzzy names | `rapidfuzz` | Fast, maintained | `difflib` |
| Metadata store | SQLite | Zero setup, transactional, right size for 10⁴ rows | PostgreSQL for multi-user |
| PDF parser | PyMuPDF (`fitz`) | Fast, reliable page anchors | `pdfplumber` when tables matter |
| OCR | Tesseract via `pytesseract` (`eng+khm`) | Only for scanned docs | `ocrmypdf` pre-pass |
| DOCX / PPTX / MD | `python-docx`, `python-pptx`, LangChain markdown splitter | Standard | — |
| LLM | **Gemma 4** via Ollama — default `gemma4:12b-it-qat` | 140 languages, native function calling, QAT keeps 4-bit quality | `gemma4:26b-a4b-it-qat` (16 GB); `gemma4:e4b` (6.6 GB) |
| Frontend | **Telegram** (`python-telegram-bot` 22.8) | Zero install, push notifications, no hosting cost | CLI (`python -m cli`) for the demo fallback |
| Optional HTTP | FastAPI + Uvicorn | Webhook receiver, health, debug JSON | Long polling needs no HTTP |
| Eval | `pandas` + custom metrics | Transparent; no black box in a report | `ranx`, `RAGAS` |

> **Ollama naming trap:** `gemma4:latest` and `gemma4:e4b` are the **same 6.6 GB edge model** — not the 31B. Pin explicit tags everywhere. A `latest` pull silently swaps the model behind your numbers.

> **Gemma 4 is the quality tier, not the baseline.** The system must be demonstrably useful with `ENABLE_LLM=false`. If the demo laptop cannot run 12B, the *correct* answer is extractive mode, not a smaller model with weaker grounding.

---

## 7. Data and storage

### 7.1 Storage allocation

| Store | Holds | Rebuildable? |
| :--- | :--- | :--- |
| **Manifest (git)** | Curated truth: seniors, projects, authorship, consent | **No — source of truth** |
| **SQLite** | Operational view + query log | Yes, from manifest |
| **Qdrant** | Chunks + vectors + payload | Yes, from manifest + corpus |
| **`data/corpus/`** | Original + parsed documents | **No — authoritative** |
| **`logs/`** | Ingestion runs, query logs, eval outputs | Training data for Semester 2 |

### 7.2 Metadata schema (SQLite)

```sql
CREATE TABLE senior (
  senior_id              TEXT PRIMARY KEY,
  full_name_latin        TEXT NOT NULL,
  full_name_khmer        TEXT,
  name_normalized        TEXT NOT NULL,          -- NFKC, casefold, diacritics stripped
  program                TEXT NOT NULL,          -- DSE | ITE | TEE | BE | FTCE
  cohort_year            INTEGER NOT NULL,
  supervisor_id          TEXT,
  contact_email          TEXT,
  github                 TEXT,
  linkedin               TEXT,
  website                TEXT,
  consent_public_contact INTEGER NOT NULL DEFAULT 0,
  consent_relay          INTEGER NOT NULL DEFAULT 0,
  telegram_user_hash     TEXT,                   -- set only after the senior starts the bot
  status                 TEXT NOT NULL DEFAULT 'active',  -- active | withdrawn
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL
);

CREATE TABLE senior_alias (                      -- the Khmer <-> Latin bridge
  senior_id        TEXT NOT NULL REFERENCES senior(senior_id),
  alias_normalized TEXT NOT NULL,
  script           TEXT NOT NULL,                -- latin | khmer
  PRIMARY KEY (senior_id, alias_normalized)
);
CREATE INDEX idx_alias ON senior_alias(alias_normalized);

CREATE TABLE project (
  project_id    TEXT PRIMARY KEY,
  title_en      TEXT NOT NULL,
  title_khmer   TEXT,
  abstract      TEXT NOT NULL,
  year          INTEGER NOT NULL,
  term          TEXT,
  program       TEXT NOT NULL,
  domain        TEXT,
  tech_stack    TEXT,                            -- JSON array
  keywords      TEXT,                            -- JSON array
  repo_url      TEXT,
  demo_url      TEXT,
  supervisor_id TEXT,
  permission    TEXT NOT NULL,                   -- granted | pending
  created_at    TEXT NOT NULL
);

CREATE TABLE authorship (
  project_id TEXT NOT NULL REFERENCES project(project_id),
  senior_id  TEXT NOT NULL REFERENCES senior(senior_id),
  role       TEXT,                               -- lead | member
  PRIMARY KEY (project_id, senior_id)
);

CREATE TABLE document (
  doc_id       TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(project_id),
  doc_type     TEXT NOT NULL,                    -- report | slides | poster | notes
  file_path    TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  page_count   INTEGER,
  parser       TEXT,
  parse_status TEXT NOT NULL,                    -- ok | ocr | failed
  chunk_count  INTEGER DEFAULT 0
);

CREATE TABLE query_log (
  query_id         TEXT PRIMARY KEY,
  surface          TEXT NOT NULL DEFAULT 'telegram',   -- telegram | cli
  tg_user_hash     TEXT,                               -- salted hash; never the raw id
  tg_message_id    INTEGER,
  raw_query        TEXT NOT NULL,
  normalized_query TEXT NOT NULL,
  intent           TEXT NOT NULL,
  resolved_senior  TEXT,
  retrieved_json   TEXT NOT NULL,
  clicked_project  TEXT,
  rating           INTEGER,                            -- -1 | 0 | 1
  latency_ms       INTEGER,
  created_at       TEXT NOT NULL
);
CREATE INDEX idx_qlog_time ON query_log(created_at);

CREATE TABLE relay_message (
  relay_id   TEXT PRIMARY KEY,
  senior_id  TEXT NOT NULL REFERENCES senior(senior_id),
  from_hash  TEXT NOT NULL,
  body       TEXT NOT NULL,
  status     TEXT NOT NULL,                      -- queued | delivered | declined
  created_at TEXT NOT NULL
);
```

### 7.3 Qdrant collection contract

```python
from qdrant_client import QdrantClient, models

COLLECTION = "rupp_senior_projects_v1"

client.create_collection(
    collection_name=COLLECTION,
    vectors_config={
        "dense": models.VectorParams(
            size=1024,
            distance=models.Distance.COSINE,
            hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
        )
    },
    sparse_vectors_config={
        "lexical": models.SparseVectorParams(
            index=models.SparseIndexParams(on_disk=False)
        )
    },
)

for field in ["project_id", "doc_id", "doc_type", "program", "supervisor_id", "senior_ids"]:
    client.create_payload_index(COLLECTION, field, field_schema=models.PayloadSchemaType.KEYWORD)
client.create_payload_index(COLLECTION, "year", field_schema=models.PayloadSchemaType.INTEGER)

# Full-text index: this is what makes name lookup work at all
client.create_payload_index(
    COLLECTION,
    "author_names_normalized",
    field_schema=models.TextIndexParams(
        type=models.TextIndexType.TEXT,
        tokenizer=models.TokenizerType.WORD,
        min_token_len=2,
        lowercase=True,
    ),
)
```

**Payload per chunk:**

```json
{
  "project_id": "dse2024-07",
  "title_en": "Khmer Handwritten Text Recognition with Tesseract",
  "senior_ids": ["s-2024-014", "s-2024-021"],
  "author_names_normalized": "sok dara / chan sreyneang",
  "doc_id": "dse2024-07__report.pdf",
  "doc_type": "report",
  "year": 2024,
  "program": "DSE",
  "supervisor_id": "sup-003",
  "section": "abstract",
  "page_number": 4,
  "chunk_index": 0,
  "chunk_id": "dse2024-07__report.pdf::c000",
  "breadcrumb": "[Project: Khmer Handwritten Text Recognition with Tesseract | Author(s): Sok Dara, Chan Sreyneang | Year: 2024 | Program: DSE | Section: abstract | p.4]",
  "text": "[Project: ... | p.4]\nWe build a pipeline that segments Khmer handwriting ...",
  "is_primary_doc": true
}
```

Point IDs are **deterministic**: `uuid5(NAMESPACE, chunk_id)`. Re-ingestion overwrites instead of duplicating, which makes retries safe and idempotency testable.

---

## 8. Ingestion internals

### 8.1 Parse → chunk, the two hard rules

1. **The abstract is never split.** It is the highest-precision chunk for topic search and the primary evidence for a project card. Atomic, flagged `section = "abstract"`.
2. **References are dropped.** A bibliography matches almost any academic query through author and title strings, injecting noise into top-k.

Section detection is cheap and heuristic: normalized heading match against `{abstract, introduction, related work, methodology, methods, results, discussion, conclusion, references, bibliography, appendix}`, with PyMuPDF font-size cues as a secondary signal. No heading found → the page is `"body"`, never dropped.

### 8.2 Chunk sizing

| Setting | Value | Rationale |
| :--- | :--- | :--- |
| `chunk_size` | 700 chars | One argument or paragraph; precise enough to match, long enough to read alone |
| `chunk_overlap` | 100 chars | ~14% overhead preserves sentence continuity |
| `min_chunk_chars` | 80 | Removes headers, footers, page numbers |
| Slides | 300 chars, non-primary | Fragmentary text: useful evidence, poor primary match |
| Abstract | atomic | §8.1 |

LangChain's `RecursiveCharacterTextSplitter` with `["\n## ", "\n### ", "\n\n", "\n", ". ", " "]` respects structure before falling back to character windows.

### 8.3 Integrity assertions (code, not discipline)

After every run:

- `qdrant_points == sum(document.chunk_count)`
- every point has `project_id`, `senior_ids`, `doc_id`, `doc_type`, `page_number`, `year`, `program`
- every `senior_ids` value exists with `status = "active"`
- an unchanged corpus hash produces **0 new points**

Any failure marks the run failed and names the offending project.

### 8.4 LangChain orchestration: Loading, Chunking, and Vector Storage

To strictly satisfy the curriculum framework requirement, LangChain orchestrates Plane A:

1. **Document Loading**: LangChain document loaders (`PyMuPDFLoader` for born-digital PDFs, `UnstructuredWordDocumentLoader` for DOCX, and `TextLoader` for notes) ingest files directly into standard `langchain_core.documents.Document` instances. Scanned PDFs leverage PyMuPDF with Tesseract OCR fallback while preserving the standard `Document` envelope.
2. **Text Chunking**: LangChain's `RecursiveCharacterTextSplitter` processes the loaded `Document` stream using structural separators (`["\n## ", "\n### ", "\n\n", "\n", ". ", " "]`). The abstract is isolated as an atomic `Document`, references are pruned, and each resulting `Document` is augmented with breadcrumb text and project metadata dictionaries.
3. **Vector Storage**: LangChain's `QdrantVectorStore` (from `langchain_qdrant` / `langchain_community.vectorstores`) interfaces with Qdrant. It manages the collection schema, registers dense (`bge-m3` 1024-d) and sparse lexical vectors, indexes payload fields (`project_id`, `year`, `program`, `author_names_normalized`), and performs deterministic idempotent batch upserts.

---

## 9. Retrieval internals

### 9.1 Why hybrid, with the math

Dense scoring:

$$\text{sim}_{\cos}(\mathbf{q}, \mathbf{d}_i) = \frac{\mathbf{q} \cdot \mathbf{d}_i}{\lVert \mathbf{q} \rVert_2 \lVert \mathbf{d}_i \rVert_2}$$

Excellent for paraphrase ("handwriting recognition" ≈ "OCR"), structurally weak for rare tokens. Sparse lexical scoring:

$$\text{score}_{\text{lex}}(q, d) = \sum_{t \in q} w_t \cdot \mathbb{1}[t \in d] \cdot \mathrm{idf}(t)$$

Fused by Reciprocal Rank Fusion, which needs only ranks — so two incomparable score scales never have to be calibrated:

$$\mathrm{RRF}(d) = \sum_{r \in \{\text{dense},\,\text{lexical}\}} \frac{1}{k + \mathrm{rank}_r(d)}, \quad k = 60$$

### 9.2 Project grouping — the bug this design exists to prevent

Raw chunk retrieval returns several chunks of one project, so a naive "top 5" can be **one project five times**:

```python
def group_by_project(points, top_k=5):
    best = {}
    for p in points:                       # already in fused RRF order
        pid = p.payload["project_id"]
        if pid not in best or p.score > best[pid].score:
            best[pid] = p
    return sorted(best.values(), key=lambda p: p.score, reverse=True)[:top_k]
```

Grouping happens **after fusion, before truncation**. Grouping first lets an early weak duplicate displace a strong unique project.

### 9.3 Intent routing

Rules first — the intent set is tiny and debuggability matters more than elegance.

| Intent | Signals | Path |
| :--- | :--- | :--- |
| `SENIOR_LOOKUP` | 2–4 tokens, no verb, token in surname list, or exact directory match | B2b |
| `PORTFOLIO` | "portfolio", "works of", "projects by `<name>`" | B2b → B2b-ii |
| `FACET_BROWSE` | year / program code / supervisor with no topic term | B2c |
| `ANSWER` | `who`, `what`, `which`, `has anyone`, `is there` | B2a + B4 |
| `TOPIC_SEARCH` | fallback | B2a |

Ambiguity resolves toward `TOPIC_SEARCH`: a wrong topic search still surfaces the right projects lexically; a wrong `SENIOR_LOOKUP` shows a confident portfolio for **the wrong person**, the worst outcome in a product about people.

### 9.4 Khmer path

Khmer has no inter-word spaces, so lexical matching needs segmentation before encoding:

```python
from khmernltk import word_tokenize
segmented = " ".join(word_tokenize(q)) if contains_khmer(q) else q
```

Dense matching handles Khmer through bge-m3's multilingual space; segmentation is what makes the **sparse** path work. Khmer queries are scored as a **separate slice** — a strong English average can otherwise hide a broken Khmer path completely.

### 9.5 Thresholds are measured, not copied

| Parameter | Default | Note |
| :--- | :--- | :--- |
| `prefetch_k` (dense/sparse) | 40 / 40 | Enough material for grouping; cheap at this scale |
| `fusion_limit` | 10 | Candidates before grouping |
| `display_top_k` | 5 projects | More than 5 is rarely read in a chat |
| `rrf_k` | 60 | Standard smoothing |
| `abstain_threshold` | **swept on golden set** | RRF scores are not comparable across queries; the gate uses the top **dense cosine** of the best project as the primary signal |
| `fuzzy_name_threshold` | 88 | `rapidfuzz.token_set_ratio`; high enough to reject different people |

**Correction carried from v1:** v1 used `score_threshold = 0.72` on cosine similarity. That value is model-specific and must not be transplanted — with bge-m3, relevant chunks commonly land in the 0.45–0.75 band. Choose the threshold by sweeping on the negative slice and reporting the sweep table.

### 9.6 LangChain orchestration: Query Retrieval Chains

LangChain wraps and orchestrates the query retrieval pipeline in Plane B:

```python
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun

class SeniorProjectKBRetriever(BaseRetriever):
    """LangChain BaseRetriever orchestrating hybrid RRF retrieval + project grouping."""
    client: object
    embedder: object
    top_k: int = 5

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        # Orchestrates intent routing, hybrid search, and project grouping
        points = hybrid_retrieve(self.client, self.embedder, query)
        return [
            Document(
                page_content=p.payload["text"],
                metadata={
                    "project_id": p.payload["project_id"],
                    "title": p.payload["title_en"],
                    "senior_ids": p.payload["senior_ids"],
                    "page_number": p.payload["page_number"],
                    "score": p.score,
                }
            )
            for p in points[:self.top_k]
        ]
```

This retriever exposes LangChain's standard `invoke(query)` and `ainvoke(query)` interface, enabling seamless composition into LCEL chains (`retriever | prompt | llm`) while maintaining the strict project grouping and abstention contracts required by the practicum.

---

## 10. Telegram layer

### 10.1 Hard limits that shape the design

| Constraint | Limit | Consequence |
| :--- | :--- | :--- |
| Message length | 4096 chars | Portfolios paginate; snippets capped ~400 chars |
| `callback_data` | **64 bytes** | Buttons carry refs, not JSON |
| Deep-link payload | 64 chars, `A-Za-z0-9_-` | `senior_<id>`, `proj_<id>` only |
| Send rate | ~30 msg/s global, ~1 msg/s per chat | Per-chat lock + global semaphore |
| Group rate | ~20 msg/min | Private chat only in Semester 1 |
| Streaming | Not supported | One final message, or edits ≥ 2 s apart |
| Parse mode | HTML (limited tags) / MarkdownV2 (18 escaped chars) | **Use HTML** — titles contain `_ * [ (` constantly |

### 10.2 Formatting rules

- Escape **before** injecting markup: `html.escape(title, quote=False)`.
- Never build a message by concatenating unescaped titles and quotes.
- Paginate on paragraph boundaries — never mid-sentence, never mid-tag.
- Buttons: `Abstract`, `Portfolio`, `Repo`, `Next ▶`; callback refs stay short.
- Every card ends with `matched: semantic + keyword` (or whichever path fired), because transparency is a requirement, not a nicety.

### 10.3 Delivery rules

- **Per-chat `asyncio.Lock`** + ≥ 1 s spacing between sends to the same chat.
- Global semaphore below Telegram's ceiling.
- `RetryAfter` → sleep `retry_after`, retry once, then log and drop. Never retry in a tight loop.
- Duplicate query within 30 s → cached reply (protects rate limits *and* Gemma latency).
- The handler must never `await` a long generation while holding a lock shared with other chats.

### 10.4 State

Disambiguation and pagination live in `bot/state.py` — an in-memory TTL map keyed by a short `state_key`, holding at most a few candidate ids. It is **not durable** and does not need to be: on restart, an expired callback simply reprompts. Storing candidate lists in `callback_data` fails at 64 bytes; storing them in SQLite is overkill.

### 10.5 Contact relay

```
student taps "Send a message"
    -> bot asks for the message text (state: awaiting_relay_body)
    -> stored in relay_message(status=queued, from_hash=sha256(tg_id+salt))
    -> bot DMs the senior (only if consent_relay=1 and telegram_user_hash is set)
    -> senior replies -> forwarded back by relay_id
```

Neither party receives the other's Telegram identity. This is a **stronger** privacy design than publishing contact details, and it is the natural fit for a chat interface.

---

## 11. Answer layer

### 11.1 Default: extractive

Per project: the best chunk verbatim, its page, and why it matched. No generation → no hallucination surface → nothing to trust. It is also the correct product behaviour: a student searching prior art wants the real abstract, not a paraphrase.

### 11.2 Gemma 4 synthesis (flagged)

```text
SYSTEM
You answer questions about past RUPP senior projects using ONLY the passages
inside <retrieved_projects>.

Rules:
1. Use only the provided passages. Do not add outside knowledge.
2. Cite every factual sentence as [Project: <title>, p.<page>].
3. If the passages do not answer the question, output exactly:
   No indexed project covers this question.
4. Never invent a senior name, supervisor, year, or repository link.
5. Keep the answer under 900 characters — it is delivered as a Telegram message.
```

Rule 5 is not cosmetic: Gemma 4 will happily write three paragraphs, which then paginates across two messages and reads badly in a chat. Capping at the prompt is cheaper than fixing it in the formatter.

Runtime settings that matter:

| Setting | Value | Why |
| :--- | :--- | :--- |
| `temperature` | `0.0` | Deterministic, reproducible |
| `num_ctx` | **8192, explicit** | Ollama's default window silently truncates retrieved evidence → confident wrong answers |
| `num_predict` | 600 | Keeps the reply inside one Telegram message |
| `model` | `gemma4:12b-it-qat` (pinned) | QAT retains quality at 4-bit; `latest` is a different model |

### 11.3 The deterministic validator

Generation is followed by a check that is deliberately dumb:

```python
CITE_RE = re.compile(r"\[Project:\s*(.+?),\s*p\.(\d+)\]")

def validate_citations(text, citations):
    allowed = {(c["title"], str(c["page"])) for c in citations}
    found = set(CITE_RE.findall(text))
    if not found:
        return False
    return found.issubset(allowed)      # no invented sources, no invented pages
```

On failure: discard the generated text, serve the extractive answer, log the event. A model does not validate another model's citations in this system.

### 11.4 Contact card is never in the prompt

Rendered from SQLite, gated by consent, and deliberately excluded from the LLM context. Personal data must not travel through a model.

---

## 12. Fine-tuning bridge (Semester 2)

Freezing the models this semester makes it tempting to skip data capture — which would make Semester 2 impossible. The flywheel is therefore a hard requirement (FR-11), not a nice-to-have.

### 12.1 What gets logged

| Field | Semester-2 use |
| :--- | :--- |
| `raw_query` / `normalized_query` | Query-side training input |
| `intent`, `resolved_senior` | Router training; negative sampling |
| `retrieved_json` (ids + ranks + scores) | Hard negatives: high-ranked but unclicked |
| `clicked_project` | Positive pair |
| `rating` | Pair quality filter |
| Zero-result queries | Knowledge-gap report for the department |

### 12.2 Pair construction

```python
# datasets/build_training_pairs.py  (shape)
# positive  : (query, project whose card was clicked or expanded)
# negatives : top-retrieved but not clicked (hard negatives)
# guard     : drop rating == -1; require >= 1 positive
{"query": "khmer ocr", "positive_id": "dse2024-07",
 "hard_negatives": ["dse2023-11", "dse2024-02"], "schema_version": 1}
```

### 12.3 Plan

1. **Synthetic queries now.** For each project, generate 5–10 plausible student queries from its abstract and keywords with Gemma 4. Human-filter a sample to bound noise. This gives a cold-start set without waiting for traffic.
2. **Contrastive fine-tune** of bge-m3: `MultipleNegativesRankingLoss`, in-batch + logged hard negatives.
3. **Evaluate on the frozen golden set**, never on training pairs.
4. **Ship only if `nDCG@10` improves** by a pre-registered margin (e.g. +0.03); otherwise keep the frozen model and report the negative result honestly.
5. Optional **LoRA on Gemma 4** (Unsloth / HF), evaluated on faithfulness and citation validity — not fluency.

Because the index is rebuildable and the harness is model-agnostic, swapping to a fine-tuned embedder is a **re-index**, not a redesign.

---

## 13. Evaluation harness

### 13.1 Golden set slices (`eval/golden_set.csv`)

| Slice | Count | Catches |
| :--- | :--- | :--- |
| Topic queries (English) | 25 | Core semantic recall |
| Topic queries (Khmer) | 10 | Broken Khmer path |
| Name queries (Latin, exact) | 10 | Baseline person lookup |
| Name queries (typos/variants) | 8 | Fuzzy resolution |
| Name queries (Khmer script) | 7 | Cross-script alias table |
| Facet queries | 10 | Filter correctness |
| Negatives | 20 | Abstention precision |

```csv
query_id,query,slice,expected_project_ids,expected_senior_id,notes
t01,khmer ocr text recognition,topic_en,dse2024-07|dse2023-04,,handwriting
n01,Sok Dara,name_latin,dse2024-07,s-2024-014,exact
n02,sokdaraa,name_typo,dse2024-07,s-2024-014,typo
n03,សុខ ដារា,name_khmer,dse2024-07,s-2024-014,khmer script
x01,quantum computing lab schedule,negative,,,must abstain
```

### 13.2 Metrics

| Metric | Definition | Target |
| :--- | :--- | :---: |
| `Recall@5` | Queries with ≥ 1 expected project in top 5 | ≥ 0.90 |
| `MRR@10` | Reciprocal rank of first expected project | ≥ 0.75 |
| `nDCG@10` | Graded ranking quality | ≥ 0.80 |
| `Name top-1` | Correct `senior_id` first | ≥ 0.95 |
| `Abstention precision` | Negatives that abstain | ≥ 0.95 |
| `Faithfulness` | Claims supported by context (LLM mode) | ≥ 0.90 |
| `Payload integrity` | Points with complete required payload | 100% |

**The evaluator calls `answer.service` directly and never touches Telegram.** That is the entire point of keeping the bot thin: the number in the report describes the same code path the student experiences, minus the transport.

---

## 14. Observability

| Signal | Where | Alert |
| :--- | :--- | :--- |
| Ingestion report | `logs/ingest_<ts>.json` | any `parse_status=failed` or count mismatch |
| Retrieval latency | `query_log.latency_ms` | `P95 > 2 s` |
| Abstention rate | aggregated | > 40% (thin index or threshold too high) |
| Zero-result queries | `query_log` | weekly corpus-gap list |
| LLM rejection rate | `logs/answer_validation.log` | > 10% |
| Telegram `429` | `logs/bot.log` | > 5/hour |
| Failed sends | `logs/bot.log` | any |
| Loaded model tag | startup log | differs from pinned tag → eval not comparable |
| Takedown requests | `senior.status` | any → rebuild and verify removal |

---

## 15. Deployment

### 15.1 `docker-compose.yml`

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.12.4          # pin the version you validated
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./data/qdrant:/qdrant/storage"]

  ollama:                                  # only needed when ENABLE_LLM=true
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: ["./data/ollama:/root/.ollama"]
    # NVIDIA GPU passthrough — uncomment if available:
    # deploy:
    #   resources:
    #     reservations:
    #       devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]

  bot:
    build: .
    environment:
      QDRANT_URL: http://qdrant:6333
      OLLAMA_URL: http://ollama:11434
      OLLAMA_MODEL: gemma4:12b-it-qat        # pin; never `latest`
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      KB_SALT: ${KB_SALT}                    # salts hashed Telegram ids
      ENABLE_LLM: "false"
    volumes: ["./data:/app/data", "./logs:/app/logs"]
    depends_on: [qdrant]

# one-time model pull:
#   docker compose exec ollama ollama pull gemma4:12b-it-qat
```

Long polling is the default and needs **no public HTTPS endpoint** — the right choice for a laptop demo. Webhook mode is only for always-on hosting.

### 15.2 Windows local path

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m ingestion.validate_manifest --manifest data/projects.csv
python -m ingestion.run --rebuild
python -m eval.run_eval --golden eval/golden_set.csv

# Bot secrets (create the bot with @BotFather)
"TELEGRAM_BOT_TOKEN=123456:ABC..." | Out-File -Encoding utf8 .env
"KB_SALT=<random>"                 | Add-Content .env

ollama pull gemma4:12b-it-qat        # only for ENABLE_LLM=true
python -m bot.main                   # long polling
```

**Caveat:** Qdrant's embedded local mode (`QdrantClient(path=...)`) is convenient for smoke tests but does not guarantee identical hybrid `prefetch` + `Fusion.RRF` behaviour. Validate hybrid retrieval against the Docker server before trusting eval numbers.

### 15.3 CI gate

1. `pytest tests/` — parser fixtures, chunker invariants, payload schema, entity resolution, formatter pagination and escaping.
2. `python -m eval.run_eval` vs committed baseline — **fail if `nDCG@10` drops > 0.02**.
3. `ruff`/`black` — style only, not a quality gate.

---

## 16. Repository layout

```text
rupp-senior-kb/
|- data/
|  |- corpus/<project_id>/{report.pdf,slides.pptx,poster.pdf}
|  |- projects.csv          # curated truth (git-tracked)
|  |- seniors.csv           # ages, aliases, consent flags
|  |- qdrant/               # gitignored
|  `- kb.sqlite3            # gitignored, rebuildable
|- ingestion/
|  |- validate_manifest.py
|  |- run.py                # validate -> parse -> chunk -> embed -> upsert
|  |- parsers/{pdf.py,docx.py,pptx.py,markdown.py,ocr.py}
|  |- chunker.py
|  |- embedder.py           # bge-m3 dense + sparse
|  |- indexer.py            # collection contract + idempotent upsert
|  `- verify.py             # post-run assertions
|- store/db.py              # SQLite schema + upserts
|- retrieval/
|  |- intent.py
|  |- hybrid.py             # prefetch -> RRF -> group
|  |- entity.py             # normalize, alias, rapidfuzz, ambiguity
|  `- filters.py
|- answer/
|  |- service.py            # THE entry point (bot + eval both call this)
|  |- prompts.py            # Gemma 4 templates + Ollama client
|  `- validate.py           # citation validator
|- bot/
|  |- main.py               # polling / webhook entrypoint
|  |- handlers.py           # commands, callbacks, deep links (no retrieval logic)
|  |- format.py             # HTML escape, pagination, keyboards
|  |- sender.py             # per-chat throttle, 429, dedupe
|  |- state.py              # TTL state for ambiguity + pagination
|  `- relay.py              # consent-gated message relay
|- cli.py                   # offline demo / fallback path
|- eval/{golden_set.csv,metrics.py,run_eval.py}
|- datasets/build_training_pairs.py
`- tests/
```

---

## 17. Risks and open questions

| # | Risk | Severity | Mitigation |
| :-- | :--- | :---: | :--- |
| R1 | Khmer retrieval weaker than English | High | Segment before sparse encoding; separate eval slice; alias table |
| R2 | Manifest curation is the real bottleneck | High | Strict schema, diff reports, LLM-assisted suggestions with human confirmation |
| R3 | Scanned/Khmer OCR garbage | Medium | `parse_status` flag; trust the hand-written abstract over OCR body |
| R4 | Name collisions between seniors | Medium | Never auto-merge; disambiguate by program + year |
| R5 | Thresholds copied from v1 (0.72) are wrong for bge-m3 | Medium | Sweep on golden set; document the chosen value with evidence |
| R6 | Gemma 4 too slow for the demo laptop | Medium | Extractive default; 12B QAT recommended; E4B fallback |
| R7 | `num_ctx` left at default | Medium | Set explicitly in code review; test that a long card is fully visible to the model |
| R8 | Privacy / consent complaint | High | Opt-in contact and relay, hashed identities, takedown path |
| R9 | Telegram blocked or rate-limited during demo | Medium | Mobile data test, caching, pre-warmed model, CLI fallback |
| R10 | Corpus too small to be meaningful (< 20 projects) | Medium | Report confidence intervals; state the limitation plainly |

**Open questions:** (1) Signed permission for archiving? (2) Which cohorts may be indexed this semester? (3) Are supervisor names public or restricted? (4) Is the knowledge-gap report a deliverable?

---

## Appendix A — Hybrid retrieval (authoritative call shape)

```python
from qdrant_client import QdrantClient, models

def hybrid_search(client, collection, dense_vec, sparse_idx, sparse_val,
                  flt=None, prefetch_k=40, limit=10):
    result = client.query_points(
        collection_name=collection,
        prefetch=[
            models.Prefetch(query=dense_vec, using="dense", limit=prefetch_k),
            models.Prefetch(
                query=models.SparseVector(indices=sparse_idx, values=sparse_val),
                using="lexical", limit=prefetch_k,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=flt,
        limit=limit,
        with_payload=True,
    )
    return result.points
```

```python
# facet-constrained topic search
flt = models.Filter(must=[
    models.FieldCondition(key="year", match=models.MatchValue(value=2024)),
    models.FieldCondition(key="program", match=models.MatchValue(value="DSE")),
])

# portfolio fetch (exact person)
flt = models.Filter(must=[
    models.FieldCondition(key="senior_ids", match=models.MatchAny(any=[senior_id]))
])
```

## Appendix B — bge-m3 dense + sparse encoding

```python
from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)   # one instance per process

def encode(texts):
    out = model.encode(texts, return_dense=True, return_sparse=True,
                       return_colbert_vecs=False, max_length=512)
    dense = out["dense_vecs"]                          # (n, 1024), L2-normalized
    sparse = [([int(t) for t in w], [float(v) for v in w.values()])
              for w in out["lexical_weights"]]
    return dense, sparse
```

The **same function and model revision** must encode chunks and queries. A revision mismatch degrades retrieval without raising any error, so log the revision in the ingestion report and in every eval run.

## Appendix C — Manifest schemas

`data/projects.csv`:

```csv
project_id,title_en,title_khmer,year,term,program,domain,tech_stack,keywords,
supervisor_id,senior_ids,abstract,repo_url,demo_url,permission
```

`data/seniors.csv`:

```csv
senior_id,full_name_latin,full_name_khmer,aliases,program,cohort_year,
supervisor_id,contact_email,github,linkedin,consent_public_contact,consent_relay,status
```

Rules:

- `senior_ids` and `aliases` are semicolon-separated; every id must exist in `seniors.csv`.
- `permission` must be `granted`; `pending` rows are reported and skipped.
- A manifest `abstract` **overrides** the parsed one (hand-verified wins).

## Appendix D — Tunable parameters

| Parameter | File | Default | Tune when |
| :--- | :--- | :--- | :--- |
| `chunk_size` | `ingestion/chunker.py` | 700 | Snippets truncated or diluted |
| `chunk_overlap` | `ingestion/chunker.py` | 100 | Facts split at boundaries |
| `prefetch_k` | `retrieval/hybrid.py` | 40 | Recall@5 plateaus and latency allows more |
| `rrf_k` | `retrieval/hybrid.py` | 60 | Rankings unstable across minor edits |
| `display_top_k` | `retrieval/hybrid.py` | 5 | Faculty wants more per page |
| `abstain_threshold` | `answer/service.py` | measured | Abstention/recall trade-off |
| `fuzzy_name_threshold` | `retrieval/entity.py` | 88 | Wrong-person matches (raise) / typos missed (lower) |
| `ENABLE_LLM` | env | `false` | Only after the extractive path is solid |
| `OLLAMA_MODEL` | env | `gemma4:12b-it-qat` | Smaller hardware, never `latest` |
| `num_ctx` | `answer/prompts.py` | 8192 | Longer cards, but keep it justified |

## Appendix E — Gemma 4 tag reference

| Tag | Size | Context | Use |
| :--- | :--- | :--- | :--- |
| `gemma4:latest` / `gemma4:e4b` | 6.6 GB | 128K | Weak-laptop fallback (**not** the 31B) |
| `gemma4:12b-it-qat` | 7.2 GB | 256K | **Default** |
| `gemma4:26b-a4b-it-qat` | 16 GB | 256K | MoE, ~4B active; 16 GB RAM/VRAM |
| `gemma4:31b-it-qat` | 19 GB | 256K | Workstation only |

## Appendix F — Migration notes from v1.0

| v1.0 | v2.0 |
| :--- | :--- |
| `Rupp_Rag_Prd_V1_0.md` | Superseded; timetable FRs retired, RAG rationale rewritten |
| `Rupp_Rag_Architecture_Spec.md` | Superseded by this document |
| `Rupp_Rag_End_To_End_Pipeline.md` | Superseded by `..._Pipeline_V2_0.md` |
| Gemini `gemini-2.5-flash` | Replaced by self-hosted **Gemma 4** |
| `pdfplumber` timetable extractor | Retired; generic PDF parser retained |
| Dense-only retrieval | Replaced by dense + sparse + RRF |
| `score_threshold = 0.72` | Retired as a constant; measured per model |
| Web-UI assumption | Replaced by the **Telegram** adapter |
| "0% hallucination" | Replaced by measurable abstention precision and faithfulness |
