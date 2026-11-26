---
name: rag-data-and-indexing
description: Use when touching manifests, corpus files, parsers, chunking, embeddings, Qdrant payloads/indexes, or the ingestion pipeline.
---

# RAG Data and Indexing

## The source-of-truth rule

`data/projects.csv` + `data/seniors.csv` are the identity layer. A PDF may only supply evidence. Never infer a senior, supervisor, year, permission, or title from parsed text when the manifest has a value.

## Manifest rules

| Rule | Meaning |
|---|---|
| `permission=granted` | Only granted projects may enter Qdrant. Pending rows are reported and skipped. |
| `status=withdrawn` | Skip the senior and remove their chunks on the next run. |
| Referential integrity | Every `senior_ids` value must exist in `seniors.csv`. |
| No duplicates | `project_id` and `senior_id` must be unique. |
| Corpus folder | Each granted project owns `data/corpus/<project_id>/`. |
| Corpus hash | Order-independent SHA-256 of relative path + file bytes; re-run must add zero points. |
| Manifest diff | Report projects added, updated, removed. |
| No invented rows | Never generate senior/project data to make a test pass. |

`data/reference/dse-curriculum/` is migrated legacy curriculum data. It is **not** a project manifest and must never be indexed.

## Parsing rules

Every parser returns `ParsedDocument` with:
`doc_id`, `project_id`, `doc_type`, `file_path`, `sha256`, `pages`, `parse_status`, `parser`, `page_count`.

- `doc_id = f"{project_id}__{filename}"`.
- PDF: PyMuPDF; OCR (`eng+khm`) when page text < 20 chars; set `parse_status="ocr"`.
- DOCX: headings and tables; `page_number=0`, anchor `section N`.
- PPTX: one page per slide; notes and tables included; `doc_type="slides"`.
- Markdown: heading sections; `page_number=0`.
- Zero usable text → `parse_status="failed"`, reported, excluded.
- Never emit an empty `Document`.

## Chunking rules

| Setting | Value |
|---|---|
| Body | 700 chars / 100 overlap |
| Slides | 300 chars / 50 overlap |
| Minimum | 80 chars (measure the body before breadcrumb) |
| Separators | `["\n## ", "\n### ", "\n\n", "\n", ". ", " "]` |
| Abstract | one atomic chunk, `chunk_id = f"{doc_id}::abstract"` |
| Dropped sections | `references`, `bibliography`, `appendix` |

Breadcrumb:

```text
[Project: <title> | Author(s): <names> | Year: <20xx> | Program: <DSE|ITE|...> | Section: <section> | p.<n>]
```

Payload keys: `project_id`, `title_en`, `senior_ids`, `author_names_normalized`, `doc_id`, `doc_type`, `year`, `program`, `domain`, `tech_stack`, `supervisor_id`, `section`, `page_number`, `chunk_index`, `chunk_id`, `breadcrumb`, `text`, `is_primary_doc`.

## Embedding rules

- One `Embedder` for chunks and queries.
- bge-m3 dense 1024-d + bge-m3 sparse lexical weights.
- `max_length=512`, batch size 16.
- Log the model revision in the ingestion report and every eval.
- No silent fallback to keyword-only.
- If FlagEmbedding is blocked, an ADR must approve the fallback; the interface stays the same.

## Qdrant rules

Collection `rupp_senior_projects_v1`:
- `dense`: 1024, COSINE, HNSW `m=16`, `ef_construct=200`.
- `lexical`: sparse, IDF.
- Payload indexes: keyword on `project_id`, `doc_id`, `doc_type`, `program`, `supervisor_id`, `senior_ids`, `domain`, `tech_stack`; integer on `year`; full-text on `author_names_normalized`.
- Point ID: `uuid5(NAMESPACE, chunk_id)`.
- Upsert idempotently; re-run unchanged corpus adds zero points.
- Post-run: `count == len(chunks)`; payload keys complete; every `senior_ids` value active.

## Failure modes to check before claiming done

- [ ] Grouping/deduping by `(source_id, page)` instead of `project_id`.
- [ ] Sparse BM25 substituted for bge-m3 lexical weights.
- [ ] `references`/`bibliography`/`appendix` still chunked.
- [ ] Abstract split across chunks.
- [ ] OCR pages not flagged `ocr`.
- [ ] Zero-text document reported `ok`.
- [ ] Missing payload indexes.
- [ ] Destructive delete-and-rebuild with no recovery.
- [ ] Qdrant write succeeds and SQLite rebuild fails (two-store inconsistency).
- [ ] `data/reference/` accidentally ingested.
- [ ] Re-run duplicates points.

## Verification

```powershell
python -m ingestion.validate_manifest --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus
python -m ingestion.run --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus --rebuild
python -m ingestion.verify
```

Tests: `tests/unit/test_manifest.py`, `test_parsers.py`, `test_chunker.py`, `test_embedder.py`, `test_indexer.py`, `tests/integration/test_ingestion_pipeline.py`.
