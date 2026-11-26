# Shared Interfaces — RUPP-SPKB v2.0

Every task must conform to this file. If a task needs a different name, field, or return type, change this file first and record the reason.

---

## 1. Module map

| Module | Public responsibility |
|---|---|
| `ingestion/validate_manifest.py` | CSV schemas, referential integrity, permission, corpus hash, manifest diff |
| `ingestion/parsers/` | Format-specific parsing into `ParsedDocument` |
| `ingestion/chunker.py` | Structure-aware chunks + breadcrumbs + payload |
| `ingestion/embedder.py` | bge-m3 dense + sparse encoding |
| `ingestion/indexer.py` | Collection contract, payload indexes, deterministic upsert |
| `ingestion/verify.py` | Post-run integrity assertions |
| `ingestion/run.py` | End-to-end ingestion CLI |
| `store/db.py` | SQLite schema and all metadata queries |
| `retrieval/intent.py` | Normalization, Khmer segmentation, five-intent routing |
| `retrieval/hybrid.py` | Dense+sparse prefetch, RRF, project grouping |
| `retrieval/entity.py` | Senior name resolution |
| `retrieval/filters.py` | Qdrant facet filters and SQLite counts |
| `retrieval/portfolio.py` | Portfolio assembly from SQLite |
| `answer/service.py` | The single answer entry point |
| `answer/prompts.py` | Gemma 4 system/user prompts and Ollama call |
| `answer/validate.py` | Deterministic citation validator |
| `answer/consent.py` | Consent-gated contact card / relay eligibility |
| `bot/main.py` | Polling entrypoint |
| `bot/handlers.py` | Commands, plain text, callbacks, deep links |
| `bot/format.py` | HTML escape, project/portfolio cards, pagination |
| `bot/sender.py` | Per-chat throttle, 429 handling, duplicate cache |
| `bot/state.py` | TTL state for ambiguity and pagination |
| `bot/relay.py` | Consent-gated message relay |
| `eval/metrics.py` | Recall, MRR, nDCG, name top-1, abstention precision |
| `eval/run_eval.py` | Golden-set runner, per-slice report, threshold sweep |
| `eval/compare_baseline.py` | CI regression gate |
| `datasets/build_training_pairs.py` | Query log → JSONL training pairs |
| `cli.py` | Offline demo/fallback |

---

## 2. CSV schemas

### `data/projects.csv`

```csv
project_id,title_en,title_khmer,year,term,program,domain,tech_stack,keywords,supervisor_id,senior_ids,abstract,repo_url,demo_url,permission
```

Rules:
- `project_id` is unique and stable.
- `year` is the cohort year (`20xx`), not the study year.
- `program` is one of `DSE`, `ITE`, `TEE`, `BE`, `FTCE`.
- `tech_stack`, `keywords`, and `senior_ids` are semicolon-separated.
- `permission` must be `granted` to ingest; `pending` rows are reported and skipped.
- `abstract` is the hand-verified abstract and overrides any parsed abstract.

### `data/seniors.csv`

```csv
senior_id,full_name_latin,full_name_khmer,aliases,program,cohort_year,supervisor_id,contact_email,github,linkedin,consent_public_contact,consent_relay,status
```

Rules:
- `aliases` is semicolon-separated and may contain Latin and Khmer spellings.
- `consent_public_contact` and `consent_relay` are `true`/`false` (1/0 accepted when reading).
- `status` is `active` or `withdrawn`; withdrawn seniors are skipped and their chunks removed on the next run.
- Contact fields live in SQLite only; they are never copied into Qdrant payloads or LLM prompts.

---

## 3. SQLite schema

The authoritative DDL lives in `store/schema.sql`; `store/db.py` applies it. This is the exact schema.

```sql
CREATE TABLE senior (
  senior_id              TEXT PRIMARY KEY,
  full_name_latin        TEXT NOT NULL,
  full_name_khmer        TEXT,
  name_normalized        TEXT NOT NULL,
  program                TEXT NOT NULL,
  cohort_year            INTEGER NOT NULL,
  supervisor_id          TEXT,
  contact_email          TEXT,
  github                 TEXT,
  linkedin               TEXT,
  website                TEXT,
  consent_public_contact INTEGER NOT NULL DEFAULT 0,
  consent_relay          INTEGER NOT NULL DEFAULT 0,
  telegram_user_hash     TEXT,
  status                 TEXT NOT NULL DEFAULT 'active',
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL
);

CREATE TABLE senior_alias (
  senior_id        TEXT NOT NULL REFERENCES senior(senior_id),
  alias_normalized TEXT NOT NULL,
  script           TEXT NOT NULL,
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
  tech_stack    TEXT,
  keywords      TEXT,
  repo_url      TEXT,
  demo_url      TEXT,
  supervisor_id TEXT,
  permission    TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE authorship (
  project_id TEXT NOT NULL REFERENCES project(project_id),
  senior_id  TEXT NOT NULL REFERENCES senior(senior_id),
  role       TEXT,
  PRIMARY KEY (project_id, senior_id)
);

CREATE TABLE document (
  doc_id       TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(project_id),
  doc_type     TEXT NOT NULL,
  file_path    TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  page_count   INTEGER,
  parser       TEXT,
  parse_status TEXT NOT NULL,
  chunk_count  INTEGER DEFAULT 0
);

CREATE TABLE query_log (
  query_id         TEXT PRIMARY KEY,
  surface          TEXT NOT NULL DEFAULT 'telegram',
  tg_user_hash     TEXT,
  tg_message_id    INTEGER,
  raw_query        TEXT NOT NULL,
  normalized_query TEXT NOT NULL,
  intent           TEXT NOT NULL,
  resolved_senior  TEXT,
  retrieved_json   TEXT NOT NULL,
  clicked_project  TEXT,
  rating           INTEGER,
  latency_ms       INTEGER,
  created_at       TEXT NOT NULL
);
CREATE INDEX idx_qlog_time ON query_log(created_at);

CREATE TABLE relay_message (
  relay_id   TEXT PRIMARY KEY,
  senior_id  TEXT NOT NULL REFERENCES senior(senior_id),
  from_hash  TEXT NOT NULL,
  body       TEXT NOT NULL,
  status     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

`tech_stack` and `keywords` are JSON arrays in SQLite. `created_at`/`updated_at` are ISO-8601 strings.

---

## 4. Qdrant collection contract

Collection: `rupp_senior_projects_v1` (overridable with `KB_COLLECTION`).

```python
vectors_config = {
    "dense": models.VectorParams(
        size=1024,
        distance=models.Distance.COSINE,
        hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
    )
}
sparse_vectors_config = {
    "lexical": models.SparseVectorParams(
        index=models.SparseIndexParams(on_disk=False)
    )
}
```

Payload indexes:

| Field | Schema |
|---|---|
| `project_id` | keyword |
| `doc_id` | keyword |
| `doc_type` | keyword |
| `program` | keyword |
| `supervisor_id` | keyword |
| `senior_ids` | keyword |
| `domain` | keyword |
| `tech_stack` | keyword |
| `year` | integer |
| `author_names_normalized` | text (`WORD`, `min_token_len=2`, `lowercase=True`) |

`domain` and `tech_stack` are added to the spec's payload example so FR-08 can pre-filter on them.

### Chunk payload

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
  "domain": "Computer Vision",
  "tech_stack": ["Python", "Tesseract"],
  "supervisor_id": "sup-003",
  "section": "abstract",
  "page_number": 4,
  "chunk_index": 0,
  "chunk_id": "dse2024-07__report.pdf::c000",
  "breadcrumb": "[Project: Khmer Handwritten Text Recognition with Tesseract | Author(s): Sok Dara, Chan Sreyneang | Year: 2024 | Program: DSE | Section: abstract | p.4]",
  "text": "[Project: ... | p.4]\nWe build a pipeline ...",
  "is_primary_doc": true
}
```

### Point IDs

`str(uuid.uuid5(uuid.UUID("6f2a1c3e-0000-4000-8000-000000000000"), chunk_id))`

Re-running an unchanged corpus must upsert the same IDs and add zero new points.

---

## 5. Python contracts

### 5.1 Ingestion

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

@dataclass(frozen=True)
class Page:
    number: int
    text: str
    headings: tuple[str, ...] = ()

@dataclass(frozen=True)
class ParsedDocument:
    doc_id: str
    project_id: str
    doc_type: str
    file_path: str
    sha256: str
    pages: tuple[Page, ...]
    parse_status: Literal["ok", "ocr", "failed"]
    parser: str
    page_count: int

    def to_langchain_documents(self, project: dict) -> list[Document]: ...

@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    metadata: dict

    def to_langchain_document(self) -> Document: ...

@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    corpus_hash: str = ""
    projects: list[dict] = field(default_factory=list)
    seniors: list[dict] = field(default_factory=list)
    diff: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool: ...

def corpus_hash(corpus_dir: Path) -> str: ...
def validate_manifest(projects_csv: Path, seniors_csv: Path,
                      corpus_dir: Path) -> ValidationReport: ...
def diff_manifests(old_projects: list[dict], old_seniors: list[dict],
                   new_projects: list[dict], new_seniors: list[dict]) -> dict: ...

def parse_document(path: Path, project: dict) -> ParsedDocument: ...
def build_chunks(parsed: ParsedDocument, project: dict,
                 seniors: list[dict]) -> list[Chunk]: ...

class Embedder:
    revision: str
    def __init__(self, model_name: str = "BAAI/bge-m3",
                 use_fp16: bool = True) -> None: ...
    def encode(self, texts: list[str]) -> tuple[list[list[float]],
                                                list[tuple[list[int], list[float]]]]: ...

def ensure_collection(client: QdrantClient) -> None: ...
def upsert_chunks(client: QdrantClient, embedder: Embedder,
                  chunks: list[Chunk]) -> int: ...
def assert_run_integrity(client: QdrantClient, db: "DB",
                         expected_points: int,
                         project_ids: list[str]) -> None: ...
```

`Embedder.encode` returns dense vectors and sparse `(indices, values)` pairs. `indexer` converts the pairs to `models.SparseVector`.

### 5.2 Retrieval

```python
@dataclass(frozen=True)
class RoutedQuery:
    raw: str
    normalized: str
    lexical: str
    intent: Literal["TOPIC_SEARCH", "SENIOR_LOOKUP", "PORTFOLIO",
                    "FACET_BROWSE", "ANSWER"]
    years: tuple[int, ...] = ()
    programs: tuple[str, ...] = ()
    name_span: str | None = None
    topic_span: str | None = None

@dataclass(frozen=True)
class RetrievedProject:
    project_id: str
    title: str
    senior_ids: tuple[str, ...]
    year: int
    program: str
    supervisor_id: str
    page_number: int
    snippet: str
    score: float              # fused RRF score
    dense_score: float        # best dense cosine; the gate uses this
    matched_by: Literal["dense", "lexical", "both"]

@dataclass(frozen=True)
class SeniorResolution:
    status: Literal["resolved", "ambiguous", "not_found"]
    senior_id: str | None = None
    confidence: float = 0.0
    via: Literal["alias", "text_index", "fuzzy"] | None = None
    candidates: tuple[dict, ...] = ()

@dataclass(frozen=True)
class Portfolio:
    senior: dict
    projects: list[dict]
    metadata_only: bool

def normalize(text: str) -> str: ...
def contains_khmer(text: str) -> bool: ...
def segment_for_lexical(text: str) -> str: ...
def route(query: str, db: "DB") -> RoutedQuery: ...
def hybrid_retrieve(client: QdrantClient, embedder: Embedder,
                    lexical_query: str, flt: models.Filter | None = None,
                    prefetch_k: int = 40,
                    fusion_limit: int = 10) -> list[RetrievedProject]: ...
def group_by_project(points: list, top_k: int = 5) -> list[RetrievedProject]: ...
def resolve_senior(normalized: str, db: "DB", client: QdrantClient,
                   fuzzy_threshold: int = 88) -> SeniorResolution: ...
def portfolio(db: "DB", senior_id: str) -> Portfolio: ...
def build_facet_filter(facets: dict) -> models.Filter: ...
def facet_counts(db: "DB", facets: dict) -> dict[str, int]: ...
```

`matched_by` is `both` when the project appears in both prefetch lists, otherwise the list it came from.

### 5.3 Answer

```python
@dataclass(frozen=True)
class Citation:
    project_id: str
    title: str
    senior_ids: tuple[str, ...]
    page_number: int
    snippet: str
    matched_by: str
    score: float

@dataclass
class AnswerResult:
    query_id: str
    route: str
    mode: Literal["extractive", "llm", "extractive_fallback",
                  "abstain", "portfolio"]
    answer: str | None
    citations: list[Citation]
    projects: list[RetrievedProject]
    portfolio: Portfolio | None
    weak_matches: list[RetrievedProject]
    normalized_query: str
    intent: str
    resolved_senior: str | None
    latency_ms: int

    def to_dict(self) -> dict: ...

def answer(query: str, client: QdrantClient, embedder: Embedder, db: "DB",
           abstain_threshold: float = 0.45,
           enable_llm: bool = False) -> AnswerResult: ...
def synthesize(query: str, citations: list[Citation]) -> str: ...
def validate_citations(text: str, citations: list[Citation]) -> bool: ...
```

`answer()` is the only entry point used by the bot, CLI, and evaluator. Formatting happens outside it.

### 5.4 Telegram

```python
def paginate(text: str, limit: int = 4096) -> list[str]: ...
def project_card(citation: Citation, page: int = 0,
                 total: int = 1) -> tuple[str, InlineKeyboardMarkup]: ...
def portfolio_card(portfolio: Portfolio) -> str: ...

class ChatThrottle:
    def __init__(self, min_gap: float = 1.0) -> None: ...
    async def send(self, bot, chat_id: int, **kwargs): ...
```

`project_card` must escape titles and snippets with `html.escape(..., quote=False)` before adding tags.

### 5.5 Bot state

```python
@dataclass
class CandidateState:
    senior_ids: list[str]
    created_at: float

@dataclass
class PagerState:
    query_id: str
    page: int
    created_at: float
```

TTL: 600 seconds. Maximum 500 entries. Expired callback → reprompt with the original query.

### 5.6 Callback data

| Prefix | Format | Example | Used by |
|---|---|---|---|
| `ab` | `ab:<project_id>` | `ab:dse2024-07` | Abstract |
| `pf` | `pf:<senior_id>` | `pf:s-2024-014` | Portfolio |
| `pg` | `pg:<project_id>:<page>` | `pg:dse2024-07:2` | Next result page |
| `pick` | `pick:<state_key>:<index>` | `pick:7f2a:0` | Ambiguity |
| `rate` | `rate:<query_id>:<1\|-1>` | `rate:<uuid>:1` | Feedback |
| `relay` | `relay:<senior_id>` | `relay:s-2024-014` | Contact relay |

Every payload must be ≤ 64 bytes. Long candidate lists live in `bot/state.py`, not in callback data.

---

## 6. Configuration values

| Name | Value |
|---|---|
| `COLLECTION` | `rupp_senior_projects_v1` |
| `CHUNK_SIZE` | 700 |
| `CHUNK_OVERLAP` | 100 |
| `SLIDE_CHUNK_SIZE` | 300 |
| `SLIDE_CHUNK_OVERLAP` | 50 |
| `MIN_CHUNK_CHARS` | 80 |
| `PREFETCH_K` | 40 |
| `FUSION_LIMIT` | 10 |
| `TOP_K` | 5 |
| `RRF_K` | 60 |
| `FUZZY_NAME_THRESHOLD` | 88 |
| `NUM_CTX` | 8192 |
| `NUM_PREDICT` | 600 |
| `TEMPERATURE` | 0.0 |
| `ABSTAIN_THRESHOLD` | 0.45 placeholder; replaced by the sweep |
| `MAX_MESSAGE` | 4096 |
| `MIN_SEND_GAP` | 1.0 s |
| `DEDUPE_SECONDS` | 30 |
| `STATE_TTL` | 600 s |
| `LOG_LEVEL` | `INFO` (DEBUG / INFO / WARNING / ERROR / CRITICAL) |

Environment variables are in `implementation/README.md` §6.

---

## 7. Fixed strings

```python
ABSTAIN = ("No indexed project covers this question. Try a topic keyword, "
           "a senior's name, or a year filter.")
LLM_NO_ANSWER = "No indexed project covers this question."
NOT_FOUND = "No senior with that name in the indexed cohort."
CONSENT_DENIED = ("This senior has not opted in to public contact. "
                  "Ask your department secretariat to forward a message.")
```

The LLM prompt must use `LLM_NO_ANSWER` exactly; the extractive renderer must use `ABSTAIN`.

---

## 8. Naming and normalization rules

- `normalize(text)`: Unicode NFKC, casefold, strip Latin diacritics, collapse whitespace. Khmer text is preserved (do not strip Khmer combining marks).
- `name_normalized` uses `normalize()`.
- `author_names_normalized` joins normalized author names with `" / "`.
- `snippet` is the chunk body without its breadcrumb prefix, capped at 400 chars for cards.
- `year` is `int` in Python and SQLite, `2024` not `"2024"`.
- `senior_ids` is a Python list, a semicolon-separated CSV field, and a Qdrant keyword list.
- `tech_stack` and `keywords` are Python lists, JSON arrays in SQLite, and keyword lists in Qdrant.
- `doc_id` is `f"{project_id}__{filename}"`.
- `chunk_id` is `f"{doc_id}::c{index:03d}"` for body chunks and `f"{doc_id}::abstract"` for the atomic abstract.

---

## 9. Cross-task rules

- No task may read a file that another task owns unless the owning task is `DONE` and its interface is listed here.
- No task may add a new dependency without updating `requirements.txt` and recording why in the task card.
- No task may log raw Telegram IDs, tokens, contact fields, or document text beyond the snippet already in the query log.
- No task may call the LLM below the relevance gate.
- No task may put contact fields into a prompt.
- No task may merge two seniors with the same name.
- No task may filter facets in Python after retrieval; filters go into Qdrant.
- No task may group by page or source; grouping is by `project_id`.
