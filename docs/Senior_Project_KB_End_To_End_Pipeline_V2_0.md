# End-to-End Engineering & Runtime Pipeline Specification: RUPP Senior Project Knowledge Base (v2.0)

**Project:** Searchable knowledge base of past RUPP senior projects — name lookup, portfolios, topic search
**Institution:** Royal University of Phnom Penh (RUPP), Faculty of Engineering (FE), ITE / DSE
**Course:** Project Practicum I (PP)
**Supervised by:** Lecturer Mr. Chap Chanpiseth
**Group 1 Researchers:** Ngoun Nuongswayumphou • Cheat Kimlang
**Frontend:** Telegram bot (`python-telegram-bot` 22.8)
**LLM:** Gemma 4 via Ollama (`gemma4:12b-it-qat`)
**Embeddings:** `BAAI/bge-m3` (dense 1024-d + sparse lexical)
**Vector store:** Qdrant (hybrid, COSINE, HNSW)
**Orchestration:** LangChain (loaders, splitters, document objects, chain glue)
**Companion docs:** `Senior_Project_KB_Prd_V2_0.md` • `Senior_Project_KB_Architecture_Spec_V2_0.md`
**Supersedes:** `Rupp_Rag_End_To_End_Pipeline.md` (v1.0)

---

## TL;DR

Three planes, nine stages, one loop back into the data.

| Stage | Plane | Input → Output | Blocking check |
| :--- | :--- | :--- | :--- |
| **00. Validate** | Ingestion | `projects.csv` + `seniors.csv` + `corpus/` → validated rows + run report | Referential integrity, permission, files exist |
| **01. Parse** | Ingestion | Documents → `ParsedDocument(pages[])` with page anchors | No silent empty document; OCR flagged |
| **02. Chunk** | Ingestion | Pages → chunks + breadcrumbs + payload | Abstract atomic; references dropped |
| **03. Embed + Index** | Ingestion | Chunks → dense + sparse → Qdrant | Point count == chunk count; ids deterministic |
| **04. Route** | Retrieval | Query → intent + normalized entities | Rules first; ambiguity → `TOPIC_SEARCH` |
| **05. Retrieve** | Retrieval | Intent → hybrid RRF → grouped projects / resolved senior | Group by `project_id` before truncation |
| **06. Answer** | Retrieval | Candidates → extractive, or Gemma 4 + citation validator | Abstain below measured threshold |
| **07. Deliver** | Interface | Answer → HTML message ≤ 4096 chars + inline keyboard | Rate-limit safe; escaped; paginated |
| **08. Log** | Flywheel | Interaction → `query_log` → training pairs | Schema-versioned export |

**Three invariants everything else depends on:**

1. **Identity comes from the manifest, never from document text.** A project is a metadata row plus evidence.
2. **Names are resolved by exact/lexical/fuzzy matching, not cosine similarity.**
3. **The bot owns delivery, never retrieval.** `answer.service.answer()` is the single entry point for both the bot and the evaluator.

---

## 1. Master blueprint

```text
==================================================================================================
PLANE A — INGESTION
==================================================================================================

  data/projects.csv + data/seniors.csv        data/corpus/<project_id>/*.{pdf,docx,pptx,md}
                |                                            |
                +----------------------+---------------------+
                                       v
  +--------------------------------------------------------------------------------------------+
  | 00 VALIDATE   schema -> refs -> permission -> files -> corpus_hash      (fail fast)        |
  +--------------------------------------------------------------------------------------------+
                                       v
  +--------------------------------------------------------------------------------------------+
  | 01 PARSE      pymupdf | tesseract(eng+khm) | python-docx | python-pptx | md splitter       |
  |               -> ParsedDocument(pages=[Page(no, text, headings)], parse_status)            |
  +--------------------------------------------------------------------------------------------+
                                       v
  +--------------------------------------------------------------------------------------------+
  | 02 CHUNK      abstract atomic | body 700/100 | slides 300 | drop references                |
  |               breadcrumb + payload (project_id, senior_ids[], year, program, page, text)   |
  +--------------------------------------------------------------------------------------------+
                                       v
  +--------------------------------------------------------------------------------------------+
  | 03 INDEX      bge-m3 -> dense(1024) + sparse(lexical) -> Qdrant upsert (uuid5 ids)         |
  |               assert points == chunks; payload complete; senior_ids valid                  |
  +--------------------------------------------------------------------------------------------+
                                       v
        [(Qdrant :6333)]        [(SQLite: seniors, projects, documents)]


==================================================================================================
PLANE B — RETRIEVAL  (single entry point: answer.service.answer)
==================================================================================================

  [query]
     v
  +--------------------------------------------------------------------------------------------+
  | 04 ROUTE   normalize -> intent -> entities                                                 |
  |   TOPIC_SEARCH | SENIOR_LOOKUP | PORTFOLIO | FACET_BROWSE | ANSWER                          |
  +--------------------------------------------------------------------------------------------+
        |                    |                                   |
        v                    v                                   v
  +----------------+   +--------------------+          +----------------------+
  | 05a HYBRID     |   | 05b ENTITY         |          | 05c FACETS           |
  | dense+sparse   |   | alias -> text idx  |          | Qdrant filter +      |
  | RRF -> group   |   | -> rapidfuzz       |          | SQLite counts        |
  +----------------+   +--------------------+          +----------------------+
        |                    |
        |                    v
        |            +--------------------+
        |            | 05d PORTFOLIO      |
        |            | SQLite + snippets  |
        |            +--------------------+
        +----------+---------+
                   v
  +--------------------------------------------------------------------------------------------+
  | 06 ANSWER   gate -> abstain | extractive | Gemma 4 + citation validator                     |
  +--------------------------------------------------------------------------------------------+
                   v
==================================================================================================
PLANE I — INTERFACE
==================================================================================================
  +--------------------------------------------------------------------------------------------+
  | 07 DELIVER  escape -> paginate <=4096 -> inline keyboard <=64B -> per-chat throttled send  |
  +--------------------------------------------------------------------------------------------+
                   v
          [Telegram message] --> 08 LOG --> training pairs (Semester 2)
```

---

## 2. Stage 00 — Manifest validation

The cheapest place to catch a portfolio bug. Nothing enters the index unless the row is defensible.

```python
# ingestion/validate_manifest.py
from __future__ import annotations
import csv, hashlib
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_PROJECT = ["project_id", "title_en", "year", "program", "supervisor_id",
                    "senior_ids", "abstract", "permission"]
REQUIRED_SENIOR = ["senior_id", "full_name_latin", "program", "cohort_year"]

@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    corpus_hash: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

def corpus_hash(corpus_dir: Path) -> str:
    """Order-independent hash of file contents -> re-runs are provably idempotent."""
    h = hashlib.sha256()
    for p in sorted(corpus_dir.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(corpus_dir).as_posix().encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()

def validate(projects_csv: Path, seniors_csv: Path, corpus_dir: Path) -> ValidationReport:
    rep = ValidationReport(corpus_hash=corpus_hash(corpus_dir))
    seniors = {r["senior_id"]: r for r in csv.DictReader(seniors_csv.open(encoding="utf-8"))}
    seen: set[str] = set()

    for line, row in enumerate(csv.DictReader(projects_csv.open(encoding="utf-8")), start=2):
        pid = row.get("project_id", "").strip()

        for col in REQUIRED_PROJECT:
            if not row.get(col, "").strip():
                rep.errors.append(f"line {line}: missing required field '{col}'")

        if pid in seen:
            rep.errors.append(f"line {line}: duplicate project_id '{pid}'")
        seen.add(pid)

        if row.get("permission", "").strip() != "granted":
            rep.warnings.append(f"line {line}: '{pid}' permission != granted -> SKIPPED")

        for sid in filter(None, (s.strip() for s in row.get("senior_ids", "").split(";"))):
            if sid not in seniors:
                rep.errors.append(f"line {line}: unknown senior_id '{sid}'")
            elif seniors[sid].get("status", "active") == "withdrawn":
                rep.warnings.append(f"line {line}: '{sid}' withdrawn -> SKIPPED")

        if pid and not (corpus_dir / pid).exists():
            rep.errors.append(f"line {line}: corpus folder missing for '{pid}'")

    return rep
```

`validate()` is pure. If `report.ok` is false, ingestion exits with a per-row error list — it never half-indexes a corpus.

---

## 3. Stage 01 — Document loading & parsing (page-anchored)

Page anchors are non-negotiable: without them there are no citations, and without citations the answer layer cannot be audited. Documents are ingested into standard LangChain `Document` abstractions.

```python
# ingestion/parsers/pdf.py
from dataclasses import dataclass
from pathlib import Path
import fitz                       # PyMuPDF
import pytesseract
from PIL import Image
from langchain_core.documents import Document
from langchain_community.document_loaders import PyMuPDFLoader

@dataclass
class Page:
    number: int
    text: str
    headings: list[str]

@dataclass
class ParsedDocument:
    doc_id: str
    pages: list[Page]
    parse_status: str             # "ok" | "ocr" | "failed"
    parser: str
    page_count: int

    def to_langchain_documents(self, project: dict) -> list[Document]:
        """Convert parsed pages to standard LangChain Document stream."""
        return [
            Document(
                page_content=p.text,
                metadata={
                    "doc_id": self.doc_id,
                    "project_id": project["project_id"],
                    "page_number": p.number,
                    "parse_status": self.parse_status,
                    "parser": self.parser,
                }
            )
            for p in self.pages
        ]

def _ocr_page(page) -> str:
    pix = page.get_pixmap(dpi=300)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return pytesseract.image_to_string(img, lang="eng+khm")   # needs khm traineddata

def parse_pdf(path: Path) -> ParsedDocument:
    doc = fitz.open(path)
    pages: list[Page] = []
    used_ocr = False

    for idx, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if len(text) < 20:                        # scanned page -> OCR
            text = _ocr_page(page).strip()
            used_ocr = True
        if not text:
            continue                              # blank page, never emit empty text
        pages.append(Page(number=idx, text=text, headings=_detect_headings(page)))

    status = "failed" if not pages else ("ocr" if used_ocr else "ok")
    return ParsedDocument(
        doc_id=path.name, pages=pages, parse_status=status,
        parser="pymupdf+ocr" if used_ocr else "pymupdf", page_count=doc.page_count,
    )

def _detect_headings(page) -> list[str]:
    """Cheap heuristic: spans noticeably larger than the body font."""
    sizes = [s["size"] for b in page.get_text("dict")["blocks"]
             for l in b.get("lines", []) for s in l["spans"]]
    if not sizes:
        return []
    body = max(set(sizes), key=sizes.count)
    return [" ".join(s["text"] for s in l["spans"]).strip()
            for b in page.get_text("dict")["blocks"]
            for l in b.get("lines", [])
            if l["spans"] and l["spans"][0]["size"] > body * 1.15]
```

`parse_status == "failed"` is a **reported outcome**, not an exception: the run continues, the document is excluded, and the report names it. Silent success on an empty parse is the worst failure mode in a retrieval pipeline — the index looks healthy while answering nothing.

---

## 4. Stage 02 — Structure-aware chunking (LangChain Text Splitters)

```python
# ingestion/chunker.py
from dataclasses import dataclass
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

BODY_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=700, chunk_overlap=100,
    separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " "],
)
SLIDE_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=50)
MIN_CHUNK_CHARS = 80
DROP_SECTIONS = {"references", "bibliography", "appendix"}

@dataclass
class Chunk:
    chunk_id: str
    text: str          # breadcrumb + body
    metadata: dict

    def to_langchain_document(self) -> Document:
        """Expose standard LangChain Document representation."""
        return Document(page_content=self.text, metadata=self.metadata)

def section_of(text: str, headings: list[str]) -> str:
    for h in headings:
        hl = h.lower().strip(" .:0123456789")
        for s in ("abstract", "introduction", "related work", "methodology", "methods",
                  "results", "discussion", "conclusion",
                  "references", "bibliography", "appendix"):
            if hl.startswith(s):
                return s
    return "body"

def build_chunks(parsed, project: dict, seniors: list[dict]) -> list[Chunk]:
    authors = ", ".join(s["full_name_latin"] for s in seniors)
    names_norm = " / ".join(s["name_normalized"] for s in seniors)
    is_primary = project["doc_type"] == "report"
    chunks: list[Chunk] = []
    i = 0

    for page in parsed.pages:
        section = section_of(page.text, page.headings)
        if section in DROP_SECTIONS:
            continue                                        # citation noise

        # Rule 1: the abstract is atomic — highest-value chunk in the corpus
        if section == "abstract":
            bodies = [page.text]
        elif project["doc_type"] == "slides":
            bodies = SLIDE_SPLITTER.split_text(page.text)
        else:
            bodies = BODY_SPLITTER.split_text(page.text)

        for body in bodies:
            if len(body.strip()) < MIN_CHUNK_CHARS:
                continue
            breadcrumb = (f"[Project: {project['title_en']} | Author(s): {authors} | "
                          f"Year: {project['year']} | Program: {project['program']} | "
                          f"Section: {section} | p.{page.number}]")
            chunks.append(Chunk(
                chunk_id=f"{parsed.doc_id}::c{i:03d}",
                text=f"{breadcrumb}\n{body.strip()}",
                metadata={
                    "project_id": project["project_id"],
                    "title_en": project["title_en"],
                    "senior_ids": project["senior_ids"],
                    "author_names_normalized": names_norm,
                    "doc_id": parsed.doc_id,
                    "doc_type": project["doc_type"],
                    "year": int(project["year"]),
                    "program": project["program"],
                    "supervisor_id": project["supervisor_id"],
                    "section": section,
                    "page_number": page.number,
                    "chunk_index": i,
                    "chunk_id": f"{parsed.doc_id}::c{i:03d}",
                    "breadcrumb": breadcrumb,
                    "is_primary_doc": is_primary,
                },
            ))
            i += 1
    return chunks
```

Why breadcrumbs: a chunk reading *"the model reached 92% accuracy"* is useless alone. With the prefix it is self-describing — better embedding, and a readable extractive answer.

---

## 5. Stage 03 — Embedding and idempotent indexing

```python
# ingestion/embedder.py
from FlagEmbedding import BGEM3FlagModel

class Embedder:
    """One process-wide model. The SAME instance encodes chunks and queries."""
    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = True):
        self.model = BGEM3FlagModel(model_name, use_fp16=use_fp16)
        self.revision = model_name          # log this in every run and every eval

    def encode(self, texts: list[str]):
        out = self.model.encode(texts, return_dense=True, return_sparse=True,
                                return_colbert_vecs=False, max_length=512)
        dense = out["dense_vecs"].tolist()
        sparse = [([int(t) for t in w], [float(v) for v in w.values()])
                  for w in out["lexical_weights"]]
        return dense, sparse
```

```python
# ingestion/indexer.py
import uuid
from qdrant_client import QdrantClient, models

COLLECTION = "rupp_senior_projects_v1"
NS = uuid.UUID("6f2a1c3e-0000-4000-8000-000000000000")   # fixed -> stable point ids

def ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(COLLECTION):
        return
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={"dense": models.VectorParams(
            size=1024, distance=models.Distance.COSINE,
            hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200))},
        sparse_vectors_config={"lexical": models.SparseVectorParams(
            index=models.SparseIndexParams(on_disk=False))},
    )
    for f in ["project_id", "doc_id", "doc_type", "program", "supervisor_id", "senior_ids"]:
        client.create_payload_index(COLLECTION, f, field_schema=models.PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION, "year", field_schema=models.PayloadSchemaType.INTEGER)
    client.create_payload_index(
        COLLECTION, "author_names_normalized",
        field_schema=models.TextIndexParams(
            type=models.TextIndexType.TEXT, tokenizer=models.TokenizerType.WORD,
            min_token_len=2, lowercase=True),
    )

def upsert_chunks(client, embedder, chunks) -> int:
    dense, sparse = embedder.encode([c.text for c in chunks])
    points = [
        models.PointStruct(
            # Deterministic id -> re-ingestion overwrites instead of duplicating
            id=str(uuid.uuid5(NS, c.chunk_id)),
            vector={"dense": d,
                    "lexical": models.SparseVector(indices=s[0], values=s[1])},
            # chunk text lives in the payload so cards render without a second lookup
            payload={**c.metadata, "text": c.text},
        )
        for c, d, s in zip(chunks, dense, sparse)
    ]
    client.upsert(COLLECTION, points=points, wait=True)
    return len(points)
```

Assertions are code, not discipline:

```python
# ingestion/verify.py
def assert_run_integrity(client, db, expected_points: int, project_ids: list[str]):
    count = client.count(COLLECTION, exact=True).count
    assert count == expected_points, f"points {count} != expected {expected_points}"

    for pid in project_ids:
        pts, _ = client.scroll(
            COLLECTION,
            scroll_filter=models.Filter(must=[
                models.FieldCondition(key="project_id", match=models.MatchValue(value=pid))]),
            limit=1, with_payload=True)
        assert pts, f"project {pid} produced zero indexed chunks"
        payload = pts[0].payload
        for key in ("project_id", "senior_ids", "doc_id", "doc_type",
                    "page_number", "year", "program", "text"):
            assert key in payload, f"{pid}: payload missing '{key}'"
        for sid in payload["senior_ids"]:
            assert db.senior_exists(sid), f"{pid}: unknown senior_id {sid}"
```

---

## 6. Stage 04 — Query understanding

```python
# retrieval/intent.py
import re, unicodedata

YEAR_RE = re.compile(r"\b(20\d{2})\b")
PROGRAM_RE = re.compile(r"\b(DSE|ITE|TEE|BE|FTCE)\b", re.IGNORECASE)
QUESTION_RE = re.compile(r"^(who|what|which|when|where|why|has anyone|is there|are there)\b", re.I)
PORTFOLIO_RE = re.compile(r"\b(portfolio|projects? (?:by|of)|works of)\b", re.I)

def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).casefold()
    t = "".join(ch for ch in unicodedata.normalize("NFD", t)
                if unicodedata.category(ch) != "Mn")     # strip Latin diacritics
    return " ".join(t.split())

def contains_khmer(text: str) -> bool:
    return any("\u1780" <= ch <= "\u17ff" for ch in text)

def segment_for_lexical(text: str) -> str:
    """Khmer has no spaces; the sparse encoder needs word boundaries."""
    if not contains_khmer(text):
        return text
    from khmernltk import word_tokenize
    return " ".join(word_tokenize(text))

def route(query: str, known_names: set[str], known_surnames: set[str]) -> dict:
    norm = normalize(query)
    years = [int(y) for y in YEAR_RE.findall(query)]
    programs = [p.upper() for p in PROGRAM_RE.findall(query)]

    if PORTFOLIO_RE.search(query):
        intent = "PORTFOLIO"
    elif norm in known_names:
        intent = "SENIOR_LOOKUP"
    elif QUESTION_RE.search(query.strip()):
        intent = "ANSWER"
    elif 2 <= len(norm.split()) <= 4 and not any(
            w in norm for w in ("project", "thesis", "system", "using", "with")):
        intent = "SENIOR_LOOKUP" if set(norm.split()) & known_surnames else "TOPIC_SEARCH"
    elif (years or programs) and len(norm.split()) <= 3:
        intent = "FACET_BROWSE"
    else:
        intent = "TOPIC_SEARCH"

    return {"raw": query, "normalized": norm, "lexical": segment_for_lexical(query),
            "intent": intent, "years": years, "programs": programs}
```

Bias note: when in doubt, route to `TOPIC_SEARCH`. A wrong topic search still surfaces the right projects lexically; a wrong `SENIOR_LOOKUP` shows a confident portfolio for **the wrong person**.

---

## 7. Stage 05a — Hybrid retrieval with project grouping

```python
# retrieval/hybrid.py
from qdrant_client import models

def hybrid_retrieve(client, embedder, lexical_query: str, flt=None,
                    prefetch_k: int = 40, fusion_limit: int = 10):
    dense, sparse = embedder.encode([lexical_query])
    result = client.query_points(
        collection_name="rupp_senior_projects_v1",
        prefetch=[
            models.Prefetch(query=dense[0], using="dense", limit=prefetch_k),
            models.Prefetch(query=models.SparseVector(indices=sparse[0][0],
                                                      values=sparse[0][1]),
                            using="lexical", limit=prefetch_k),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=flt,
        limit=fusion_limit,
        with_payload=True,
    )
    return group_by_project(result.points)

def group_by_project(points, top_k: int = 5):
    """Prevent 'one project, five times' — the classic chunk-ranking bug."""
    best: dict[str, object] = {}
    for p in points:                       # fused (RRF) order
        pid = p.payload["project_id"]
        if pid not in best or p.score > best[pid].score:
            best[pid] = p
    return sorted(best.values(), key=lambda p: p.score, reverse=True)[:top_k]

# LangChain BaseRetriever adapter
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun

class SeniorProjectKBRetriever(BaseRetriever):
    """LangChain BaseRetriever adapter orchestrating hybrid search + project grouping."""
    client: object
    embedder: object
    top_k: int = 5

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        projects = hybrid_retrieve(self.client, self.embedder, query)
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
            for p in projects[:self.top_k]
        ]
```

Grouping happens **after fusion, before truncation**. Grouping first lets an early weak duplicate displace a strong unique project.

---

## 8. Stage 05b — Senior entity resolution

```python
# retrieval/entity.py
from rapidfuzz import fuzz, process
from qdrant_client import models

def resolve_senior(normalized: str, db, client, fuzzy_threshold: int = 88) -> dict:
    # 1) curated alias table — the Khmer <-> Latin bridge
    if (sid := db.lookup_alias(normalized)):
        return {"status": "resolved", "senior_id": sid, "confidence": 1.00, "via": "alias"}

    # 2) Qdrant full-text index over indexed author names
    pts, _ = client.scroll(
        "rupp_senior_projects_v1",
        scroll_filter=models.Filter(must=[models.FieldCondition(
            key="author_names_normalized", match=models.MatchText(text=normalized))]),
        limit=50,
        with_payload=["senior_ids"],
    )
    candidate_ids = {sid for p in pts for sid in p.payload["senior_ids"]}
    if len(candidate_ids) == 1:
        return {"status": "resolved", "senior_id": candidate_ids.pop(),
                "confidence": 0.90, "via": "text_index"}

    # 3) fuzzy over the full directory
    directory = {s["senior_id"]: s["name_normalized"] for s in db.all_seniors()}
    matches = process.extract(normalized, directory, scorer=fuzz.token_set_ratio,
                              score_cutoff=fuzzy_threshold, limit=5)
    if len(matches) == 1:
        return {"status": "resolved", "senior_id": matches[0][2],
                "confidence": matches[0][1] / 100, "via": "fuzzy"}

    if matches:
        # Never merge people. Ask the user, via an inline keyboard.
        return {"status": "ambiguous", "candidates": [
            {"senior_id": sid, "score": round(sc, 1), **db.senior_card(sid)}
            for _, sc, sid in matches]}

    return {"status": "not_found", "closest": [
        {"senior_id": sid, "score": round(sc, 1), **db.senior_card(sid)}
        for _, sc, sid in process.extract(normalized, directory,
                                          scorer=fuzz.token_set_ratio, limit=3)]}
```

**Ambiguity is a feature.** RUPP cohorts contain repeated names; picking one silently produces a wrong portfolio — a factual error about a real person, worse than any retrieval miss.

---

## 9. Stage 06 — Gate and answer

```python
# answer/service.py
ABSTAIN = ("No indexed project covers this question. Try a topic keyword, "
           "a senior's name, or a year filter.")

def answer(query: str, client, embedder, db,
           abstain_threshold: float = 0.45,      # MEASURED on the golden set
           enable_llm: bool = False) -> dict:
    routed = route(query, db.known_names(), db.known_surnames())

    if routed["intent"] in ("SENIOR_LOOKUP", "PORTFOLIO"):
        res = resolve_senior(routed["normalized"], db, client)
        if res["status"] != "resolved":
            return {"route": routed["intent"], **res, "answer": None}
        return {"route": "PORTFOLIO", "portfolio": db.portfolio(res["senior_id"]),
                "answer": None, "citations": []}

    projects = hybrid_retrieve(client, embedder, routed["lexical"],
                               flt=build_filter(routed))
    if not projects or projects[0].score < abstain_threshold:
        return {"route": routed["intent"], "answer": ABSTAIN, "citations": [],
                "weak_matches": [p.payload["project_id"] for p in projects[:3]]}

    citations = [{"project_id": p.payload["project_id"],
                  "title": p.payload["title_en"],
                  "senior_ids": p.payload["senior_ids"],
                  "page": p.payload["page_number"],
                  "snippet": p.payload["text"],
                  "matched": "dense+lexical",
                  "score": round(p.score, 4)} for p in projects]

    if not enable_llm:
        return {"route": routed["intent"], "mode": "extractive",
                "answer": None, "results": citations, "citations": citations}

    draft = synthesize(query, citations)             # Gemma 4, temperature 0
    if not validate_citations(draft, citations):     # deterministic guard
        return {"route": routed["intent"], "mode": "extractive_fallback",
                "answer": None, "results": citations, "citations": citations}
    return {"route": routed["intent"], "mode": "llm",
            "answer": draft, "results": citations, "citations": citations}
```

### 9.1 Gemma 4 synthesis

```python
# answer/prompts.py
import httpx

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:12b-it-qat"      # pinned; never "latest"

SYSTEM = """You answer questions about past RUPP senior projects using ONLY the
passages inside <retrieved_projects>.

Rules:
1. Use only the provided passages. Do not add outside knowledge.
2. Cite every factual sentence as [Project: <title>, p.<page>].
3. If the passages do not answer the question, output exactly:
   No indexed project covers this question.
4. Never invent a senior name, supervisor, year, or repository link.
5. Keep the answer under 900 characters — it is delivered as a Telegram message."""

def synthesize(query: str, citations: list[dict]) -> str:
    context = "\n\n".join(
        f"<project title=\"{c['title']}\" page=\"{c['page']}\">\n{c['snippet']}\n</project>"
        for c in citations
    )
    r = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content":
                    f"<retrieved_projects>\n{context}\n</retrieved_projects>\n\n{query}"},
            ],
            "stream": False,
            "options": {
                "temperature": 0.0,
                "top_p": 0.9,
                "num_ctx": 8192,      # MUST be explicit; Ollama's default truncates evidence
                "num_predict": 600,   # keeps the reply inside one Telegram message
            },
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["message"]["content"].strip()
```

### 9.2 The citation validator

Deliberately dumb, deterministic, and not another model:

```python
# answer/validate.py
import re

CITE_RE = re.compile(r"\[Project:\s*(.+?),\s*p\.(\d+)\]")

def validate_citations(text: str, citations: list[dict]) -> bool:
    allowed = {(c["title"], str(c["page"])) for c in citations}
    found = set(CITE_RE.findall(text))
    if not found:
        return False
    return found.issubset(allowed)      # no invented sources, no invented pages
```

---

## 10. Stage 07 — Telegram delivery

```python
# bot/format.py
import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MAX_LEN = 4096

def esc(text: str) -> str:
    return html.escape(text, quote=False)          # titles are untrusted input

def paginate(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on paragraph boundaries, never mid-sentence, never mid-tag."""
    if len(text) <= limit:
        return [text]
    pages, buf = [], ""
    for para in text.split("\n\n"):
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > limit:
            if buf:
                pages.append(buf)
            buf = para[:limit]                     # pathological single paragraph
        else:
            buf = candidate
    if buf:
        pages.append(buf)
    return pages

def project_card(c: dict, page: int = 0, total: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    title = esc(c["title"])
    snippet = esc(c["snippet"][:400])
    body = (f"<b>{title}</b>\n"
            f"<i>— {title}, p.{c['page']}</i>\n\n"
            f"“{snippet}…”\n\n"
            f"<i>matched: {c['matched']}</i>")
    row = [InlineKeyboardButton("Abstract", callback_data=f"ab:{c['project_id']}")]
    if c.get("senior_ids"):
        row.append(InlineKeyboardButton("Portfolio", callback_data=f"pf:{c['senior_ids'][0]}"))
    if page < total - 1:
        row.append(InlineKeyboardButton("Next ▶", callback_data=f"pg:{c['project_id']}:{page + 1}"))
    return body, InlineKeyboardMarkup([row])
```

```python
# bot/sender.py
import asyncio, time
from telegram.error import RetryAfter

class ChatThrottle:
    """Per-chat serialization + 429 handling. Never blocks other chats."""
    def __init__(self, min_gap: float = 1.0):
        self.min_gap = min_gap
        self._locks: dict[int, asyncio.Lock] = {}
        self._last: dict[int, float] = {}

    async def send(self, bot, chat_id: int, **kwargs):
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            wait = self._last.get(chat_id, 0) + self.min_gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            for _ in (1, 2):
                try:
                    msg = await bot.send_message(chat_id=chat_id, **kwargs)
                    self._last[chat_id] = time.monotonic()
                    return msg
                except RetryAfter as e:          # honour Telegram's own backoff
                    await asyncio.sleep(e.retry_after)
            return None                          # logged upstream, never raised into the loop
```

```python
# bot/handlers.py
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
import answer.service as core
from bot import format

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.message.text
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    result = core.answer(q, ctx.bot_data["client"], ctx.bot_data["embedder"],
                         ctx.bot_data["db"], enable_llm=ctx.bot_data["enable_llm"])

    if result.get("portfolio"):
        text = format.portfolio_card(result["portfolio"])
        for page in format.paginate(text):
            await ctx.bot_data["throttle"].send(ctx.bot, update.effective_chat.id,
                                                text=page, parse_mode="HTML")
        return

    if result.get("results"):
        for c in result["results"]:
            body, kb = format.project_card(c)
            await ctx.bot_data["throttle"].send(ctx.bot, update.effective_chat.id,
                                                text=body, parse_mode="HTML", reply_markup=kb)
        return

    await ctx.bot_data["throttle"].send(ctx.bot, update.effective_chat.id,
                                        text=format.esc(result["answer"]),
                                        parse_mode="HTML")
```

`on_text` contains **no ranking, no query rewriting, no threshold logic** — it calls one service function and renders the result. That is the whole contract.

---

## 11. Stage 08 — Logging and export

```python
# bot logging (called once per interaction, non-blocking)
db.log_query(
    query_id=str(uuid.uuid4()),
    surface="telegram",
    tg_user_hash=hashlib.sha256(f"{tg_id}{SALT}".encode()).hexdigest()[:32],
    raw_query=raw,
    normalized_query=result.get("normalized", ""),
    intent=result.get("route", ""),
    resolved_senior=result.get("senior_id"),
    retrieved_json=json.dumps([{"project_id": c["project_id"], "rank": i, "score": c["score"]}
                               for i, c in enumerate(result.get("results", []), 1)]),
    clicked_project=None,
    rating=None,
    latency_ms=int((time.perf_counter() - t0) * 1000),
)
```

```python
# datasets/build_training_pairs.py  (shape)
# positive  : (query, project whose card was clicked)
# negatives : top-retrieved but not clicked  -> hard negatives
# guard     : drop rating == -1; require >= 1 positive
{"query": "khmer ocr", "positive_id": "dse2024-07",
 "hard_negatives": ["dse2023-11", "dse2024-02"], "schema_version": 1}
```

---

## 12. Worked traces

Corpus: 40 projects. Scores are illustrative but consistent with bge-m3 + RRF behaviour (dense cosine typically 0.45–0.75 for on-topic academic text).

### Trace 1 — Topic search in Telegram

**Student types:** `khmer ocr text recognition`

| Step | Result |
| :--- | :--- |
| Route | `TOPIC_SEARCH` (no name hit, no question word) |
| Prefetch | dense 40 / lexical 40 → RRF → 10 candidates |
| Grouping | 10 chunks → **4 distinct projects** |
| Gate | top project score `0.0321` ≫ threshold |
| Delivery | 4 cards, ≤ 4096 chars each, throttled ≥ 1 s apart |

**Bot reply (card 1):**

```text
Khmer Handwritten Text Recognition with Tesseract
Sok Dara, Chan Sreyneang · 2024 · DSE · sup. sup-003

"…we build a pipeline that segments Khmer handwriting and run Tesseract with a
custom khm traineddata, reaching 81% character accuracy on 500 scanned forms…"
— Khmer Handwritten Text Recognition with Tesseract, p.4

matched: semantic + keyword
[ Abstract ] [ Portfolio ] [ Repo ]
```

Note what happened: the report's title says *"Handwritten Text Recognition"*, not "OCR". Dense retrieval catches the paraphrase; sparse retrieval supplies the exact-token safety net for `khmer`. Neither alone would rank this first.

### Trace 2 — Name lookup, three spellings

| Input | Path | Result |
| :--- | :--- | :--- |
| `Sok Dara` | alias exact | resolved, confidence `1.00` |
| `សុខ ដារា` | alias exact (Khmer) | resolved, confidence `1.00` |
| `sokdaraa` | text index → 1 candidate | resolved, confidence `0.90` |

**Portfolio reply:**

```text
Sok Dara  ·  សុខ ដារា
DSE · Cohort 2024 · Supervisor sup-003

Projects (2)
1. Khmer Handwritten Text Recognition with Tesseract (2024)
2. Khmer OCR Dataset Curation Tool (2023)

Connect: GitHub · LinkedIn
```

With `consent_public_contact = false`, the final block is replaced by:

```text
This senior has not opted in to public contact.
Ask your department secretariat to forward a message.
```

### Trace 3 — Ambiguous name (the case that matters most)

**Input:** `Sok Dara` when two seniors share the name.

```text
I found 2 seniors named Sok Dara. Which one?

[ Sok Dara — DSE 2024 ]
[ Sok Dara — ITE 2023 ]
```

Callback payload: `pick:st_7f2a,0` — 15 bytes, comfortably inside the 64-byte limit. The candidate list itself lives in `bot/state.py`, because two full candidate rows would exceed it.

The bot **does not** pick the higher-scoring candidate. A 0.51-vs-0.49 score gap is noise, and showing the wrong person's portfolio is a factual error about a real human.

### Trace 4 — Facet browse

**Input:** `/year 2024`

```text
2024  ·  12 projects          [ DSE 8 ] [ ITE 4 ] [ Clear all ]
...
```

Counts come from SQLite (`GROUP BY year, program`), so they describe the corpus, not the current page. The result list length must equal the SQLite count — that equality is the filter bug detector.

### Trace 5 — Multi-intent

**Input:** `Sok Dara's OCR project`

Decomposed into `SENIOR_LOOKUP("sok dara")` + `TOPIC_SEARCH("ocr")`, then intersected on `senior_ids`.

- Non-empty intersection → the matching project first, then "other projects by Sok Dara".
- Empty intersection → all of Sok Dara's projects plus *"None of Sok Dara's indexed projects matched 'OCR'"*. Neither half is silently dropped.

### Trace 6 — Abstention

**Input:** `quantum computing lab schedule 2019`

| Step | Result |
| :--- | :--- |
| Route | `TOPIC_SEARCH` |
| Retrieval | best fused project score `0.0098` < threshold |
| Gemma 4 | **not called** |
| Reply | `No indexed project covers this question. Try a topic keyword, a senior's name, or a year filter.` + 3 weak matches marked low confidence |

Zero fabricated projects, zero LLM tokens spent, and the honesty is visible to the user.

### Trace 7 — Unknown senior

**Input:** `/senior Sok Panha`

```text
No senior with that name in the indexed cohort.

Closest matches (not confirmed):
· Sok Panha — ITE 2022   (similarity 71)
· Sok Phanha — DSE 2023  (similarity 68)
```

A knowledge base about people must never invent a person.

### Trace 8 — Rate-limit storm

A student sends 20 messages in 10 seconds:

1. Per-chat lock serializes them; the first three answer normally.
2. Messages 2–20 hit the 30 s duplicate cache when identical, and return instantly.
3. Distinct queries proceed at ≥ 1 s intervals.
4. If Telegram returns `429`, the sender sleeps `retry_after` (typically a few seconds) and retries once.
5. Nothing is dropped silently; anything dropped is logged.

---

## 13. Evaluation harness

```python
# eval/run_eval.py  (shape)
import pandas as pd

def recall_at_k(rows, k=5):
    hit = sum(1 for r in rows
              if r["expected"] and set(r["expected"]) & set(r[f"top{k}"]))
    return hit / len(rows)

def mrr_at_k(rows, k=10):
    total = 0.0
    for r in rows:
        for rank, pid in enumerate(r[f"top{k}"], start=1):
            if pid in r["expected"]:
                total += 1 / rank
                break
    return total / len(rows)

def evaluate_by_slice(golden: pd.DataFrame, runner) -> pd.DataFrame:
    """Per slice — an English-only average hides a broken Khmer path."""
    out = []
    for name, group in golden.groupby("slice"):
        rows = [{**runner(r["query"]), "expected": set((r["expected_project_ids"] or "").split("|")) - {""}}
                for _, r in group.iterrows()]
        out.append({"slice": name, "n": len(group),
                    "recall@5": round(recall_at_k(rows), 3),
                    "mrr@10": round(mrr_at_k(rows), 3)})
    return pd.DataFrame(out)
```

**Procedure:**

1. Run against the frozen baseline; commit the JSON output.
2. Any later run losing `> 0.02 nDCG@10` is a regression and fails CI.
3. `abstain_threshold` is chosen by sweeping values on the negative slice: the **smallest** threshold reaching abstention precision ≥ 0.95 without dropping topic `Recall@5` below 0.90. Publish the sweep table — it turns a magic number into evidence.

The evaluator calls `answer.service.answer()` directly. Telegram is never in the measurement path, which is exactly why the bot must stay thin.

---

## 14. Execution order

```powershell
# 1. Infrastructure
docker compose up -d qdrant

# 2. Ingest
python -m ingestion.run --projects data/projects.csv --seniors data/seniors.csv --rebuild

# 3. Verify the index
python -m ingestion.verify

# 4. Evaluate + tune the abstention threshold
python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold

# 5. Run the bot (long polling; no public URL needed)
$env:TELEGRAM_BOT_TOKEN="123456:ABC..."
$env:KB_SALT="random-string"
python -m bot.main

# 6. Optional Gemma 4 mode
ollama pull gemma4:12b-it-qat
$env:ENABLE_LLM="true"
```

Offline fallback for the demo, if Telegram is unreachable:

```powershell
python -m cli --query "khmer ocr"
```

---

## 15. Pre-demo checklist

| # | Check | Why |
| :-- | :--- | :--- |
| 1 | Point count == expected chunk count | Catches partial and duplicate upserts |
| 2 | Every point has `project_id`, `senior_ids`, `page_number`, `text` | Cards and citations depend on them |
| 3 | Same query twice → identical ranking | Non-determinism invalidates the eval |
| 4 | `សុខ ដារា` resolves to the same senior as `Sok Dara` | The headline feature |
| 5 | Ambiguous name → keyboard, not a guess | Wrong portfolio = wrong claim about a person |
| 6 | Non-consenting senior → no contact block, no relay button | Privacy is testable, so test it |
| 7 | Portfolio > 4096 chars paginates cleanly | Telegram's hard limit |
| 8 | Negative query abstains and calls no model | Proves the gate is real |
| 9 | Re-ingest unchanged corpus → 0 new points | Proves idempotency before the corpus grows |
| 10 | Bot survives 50 rapid messages | Rate-limit safety under exam conditions |
| 11 | Loaded model tag == pinned tag | Otherwise the numbers in the report describe a different system |
| 12 | Telegram bot reachable on mobile data | Campus Wi-Fi may block it |
