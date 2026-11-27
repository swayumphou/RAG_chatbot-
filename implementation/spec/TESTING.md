# Testing and Verification Handbook

This file tells the junior what to test, how to test it, and what "verified" means. The specs define behavior; the task cards define the tests; this file defines the mechanics.

---

## 1. Test stack

| Tool | Use |
|---|---|
| `pytest` | Unit and integration tests |
| `pytest-asyncio` | Bot/sender async tests |
| `ruff` | Lint |
| `black` | Formatting |
| `python -m py_compile` | Fast syntax check |
| `pytest-cov` | Optional coverage report |

Test layout:

```text
tests/
├── unit/
│   ├── test_manifest.py
│   ├── test_store.py
│   ├── test_parsers.py
│   ├── test_chunker.py
│   ├── test_embedder.py
│   ├── test_indexer.py
│   ├── test_intent.py
│   ├── test_hybrid.py
│   ├── test_entity.py
│   ├── test_filters.py
│   ├── test_portfolio.py
│   ├── test_answer.py
│   ├── test_validate.py
│   ├── test_consent.py
│   ├── test_format.py
│   ├── test_sender.py
│   ├── test_state.py
│   ├── test_relay.py
│   ├── test_metrics.py
│   ├── test_training_pairs.py
│   ├── test_config.py
│   └── test_bot.py
├── integration/
│   ├── test_ingestion_pipeline.py
│   ├── test_retrieval_pipeline.py
│   ├── test_answer_pipeline.py
│   ├── test_bot_pipeline.py
│   ├── test_eval_pipeline.py
│   └── test_pre_demo.py
└── fixtures/
    ├── build_fixtures.py
    ├── manifest/
    │   ├── projects.csv
    │   ├── seniors.csv
    │   └── invalid/
    └── corpus/
        ├── dse2024-001/report.pdf
        ├── dse2024-001/slides.pptx
        ├── dse2024-002/report.pdf      # scanned/image-only
        ├── dse2024-003/report.docx
        ├── dse2024-004/report.md
        └── dse2024-005/report.pdf      # metadata-only / no chunks
```

The fixture corpus is **synthetic**. Do not commit real student documents, names, emails, phone numbers, or student IDs.

---

## 2. Fixtures

### 2.1 Manifest fixtures

The valid fixture must contain:

- 5 projects across DSE and ITE.
- 6 seniors, including:
  - one senior with Latin + Khmer + typo aliases;
  - two seniors with the same display name (ambiguity);
  - one senior with `consent_public_contact=true`;
  - one with `consent_relay=true`;
  - one with `status=withdrawn`;
  - one with no documents.
- One project with `permission=pending` (must be skipped).
- One project with no documents (must still yield a metadata-only portfolio).

Invalid fixtures, one per folder:

| Fixture | Expected error |
|---|---|
| `missing_project_id.csv` | missing required field |
| `duplicate_project_id.csv` | duplicate `project_id` |
| `unknown_senior.csv` | unknown `senior_id` |
| `missing_folder/` | corpus folder missing |
| `pending_permission.csv` | warning + skip |
| `withdrawn_senior.csv` | warning + skip |

### 2.2 Document fixtures

`tests/fixtures/build_fixtures.py` generates them with PyMuPDF, python-docx, python-pptx, and Pillow:

- **Born-digital PDF:** 4 pages, headings, an abstract section, a references section, a short header/footer.
- **Scanned PDF:** an image-only page with text rendered as an image; OCR should produce text and `parse_status="ocr"`.
- **DOCX:** heading levels + a table.
- **PPTX:** 3 slides + speaker notes + one table.
- **Markdown:** headings, an abstract, a references section, a short 20-char paragraph.
- **Empty/zero-text PDF:** one blank page; `parse_status="failed"` and excluded.

The fixture builder is a test utility, not production ingestion code.

### 2.3 Golden set fixtures

`eval/golden_set.csv` is the product-level fixture. Test-only subsets live under `tests/fixtures/golden/` and must use the same header.

Header:

```csv
query_id,query,slice,expected_project_ids,expected_senior_id,notes
```

Do not mutate `eval/golden_set.csv` after the baseline is committed; add a new version if the set changes.

---

## 3. Commands

```powershell
# Syntax only (allowed for a dispatched implementer)
python -m py_compile ingestion/run.py

# Unit tests
pytest tests/unit -q

# Integration tests (needs Qdrant + Ollama or fakes)
pytest tests/integration -q

# One test
pytest tests/unit/test_manifest.py::test_duplicate_project_id_rejected -v

# Coverage
pytest --cov=ingestion --cov=retrieval --cov=answer --cov=bot -q

# Lint / format
ruff check .
black --check .

# Ingestion
python -m ingestion.validate_manifest --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus
python -m ingestion.run --projects data/projects.csv --seniors data/seniors.csv --corpus data/corpus --rebuild
python -m ingestion.verify

# Evaluation
python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold

# Bot without Telegram
python -m bot.main --check
```

---

## 4. Verification policy

- A **dispatched subagent** writes the tests named in the task card but does not run the full suite. It may run `python -m py_compile` on each changed file. The manager runs the primary test after the implementation report.
- A **human owner** should run the card's test while developing; the final acceptance run is still the manager's.
- The manager runs the **single primary test named first in the card**, not every suite. Broader suites run when a change touches shared runtime wiring or when the human asks.
- A test that was never written cannot be run later. Writing the test is part of the deliverable, even if execution is deferred.

---

## 5. Per-task test contract

Every task card names at least one test. The minimum mapping:

| Task | Primary test |
|---|---|
| T1.5 Manifest validator | `tests/unit/test_manifest.py::test_valid_manifest_and_rejections` |
| T2.2 PDF + OCR | `tests/unit/test_parsers.py::test_pdf_pages_and_ocr_flag` |
| T2.6 Chunker | `tests/unit/test_chunker.py::test_abstract_atomic_and_references_dropped` |
| T2.8 Indexer | `tests/unit/test_indexer.py::test_payload_indexes_and_deterministic_ids` |
| T3.3 Hybrid retrieval | `tests/unit/test_hybrid.py::test_group_by_project_before_truncation` |
| T3.5 Entity resolution | `tests/unit/test_entity.py::test_three_spellings_resolve_to_one_senior` |
| T3.6 Portfolio | `tests/unit/test_portfolio.py::test_metadata_only_portfolio` |
| T4.5 Citation validator | `tests/unit/test_validate.py::test_invented_title_or_page_falls_back` |
| T5.3 Project card | `tests/unit/test_format.py::test_project_card_escapes_html_and_fits_limit` |
| T5.7 Sender | `tests/unit/test_sender.py::test_per_chat_spacing_and_retry_after` |
| T6.2 Metrics | `tests/unit/test_metrics.py::test_recall_mrr_ndcg_and_abstention` |
| T6.3 Eval runner | `tests/integration/test_eval_pipeline.py::test_per_slice_report_and_baseline` |

Each card may add more tests; the primary test is the one the manager runs first.

---

## 6. Fakes and service-dependent tests

### 6.1 Fake Qdrant

Use `qdrant_client.QdrantClient(":memory:")` for unit tests where the collection contract is the subject. For tests that must prove real RRF + prefetch behavior, mark them:

```python
@pytest.mark.integration
def test_hybrid_prefetch_and_rrf(...):
    ...
```

They run only when `QDRANT_URL` points to a server.

### 6.2 Fake embedder

Do not call Ollama or FlagEmbedding in unit tests. Use a deterministic fake:

```python
class FakeEmbedder:
    revision = "fake-bge-m3@test"
    def encode(self, texts):
        return ([[float(len(t)), 1.0, 0.0, ...] for t in texts],
                [([1, 2], [0.5, 0.5]) for _ in texts])
```

The fake must return 1024-dimensional dense vectors when the indexer validates dimensions.

### 6.3 Fake Ollama

Patch `answer.prompts.httpx.post` or inject a `chat_fn`. Tests must cover:

- successful answer with valid citations;
- timeout / connection error → extractive fallback;
- `done_reason="length"` → extractive fallback;
- LLM says `LLM_NO_ANSWER` → abstain;
- invented citation → extractive fallback.

### 6.4 Fake Telegram transport

Do not contact Telegram. Use a small async fake with `send_message`, `send_chat_action`, `edit_message_text`, and `answer_callback_query` methods. Record calls and assert:

- HTML escaping;
- ≤ 4096 chars;
- ≥ 1 s spacing per chat;
- 429 `retry_after` sleeps and retries once;
- duplicate query within 30 s is served from cache;
- callback data is ≤ 64 bytes.

---

## 7. Evaluation protocol

1. Freeze `eval/golden_set.csv` before any model or threshold change.
2. Run `python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold`.
3. Commit `eval/baseline.json` with:
   - model tags (`embed_model`, `llm_model`, `qdrant_version`);
   - per-slice Recall@5, MRR@10, nDCG@10, name top-1, abstention precision;
   - the chosen `abstain_threshold`;
   - the golden-set hash.
4. Any later run losing > 0.02 nDCG@10 fails CI.
5. Report per slice, never only an average. An English-only average hides a broken Khmer path.
6. The evaluator calls `answer.service.answer()` directly. Telegram is never in the measurement path.

### Golden-set composition

Use the Architecture §13.1 slices (90 queries total):

| Slice | Count |
|---|---|
| Topic queries (English) | 25 |
| Topic queries (Khmer) | 10 |
| Name queries (Latin, exact) | 10 |
| Name queries (typos/variants) | 8 |
| Name queries (Khmer script) | 7 |
| Facet queries | 10 |
| Negatives | 20 |

The PRD's "60-query" figure is a minimum; the ruling is in `implementation/README.md` §9.

---

## 8. CI

CI runs:

```powershell
ruff check .
black --check .
pytest tests/unit -q
pytest tests/integration -q        # only when services are available
python -m eval.compare_baseline eval/baseline.json eval/last_report.json
```

The workflow is `.github/workflows/ci.yml`. The evaluation step runs only when the repository
variable `RUN_EVAL=true` is set (it downloads bge-m3 and needs a committed `eval/baseline.json`);
it runs from outside the repository folder because the repo's `datasets/` package would hide the
Hugging Face `datasets` library.

`compare_baseline` exits non-zero when:

- a slice's nDCG@10 drops > 0.02;
- a required slice is missing;
- the golden-set hash differs from the baseline;
- the loaded model tag differs from the pinned tag.

---

## 9. What not to test

- Do not test the exact wording of Gemma's answer. Test citation validity and fact containment.
- Do not snapshot Telegram's full HTML; assert escaping, limits, and button callback data.
- Do not require a live model for unit tests.
- Do not use real student data in fixtures.
- Do not test the old `legacy/dse-curriculum/src/` tree; it is quarantined reference code.

---

## 10. Test data hygiene

- Every fixture is synthetic or permission-cleared.
- Names in fixtures are invented (`Sok Dara`, `Chan Sreyneang`) and marked as fixtures.
- No emails, phone numbers, student IDs, or grades.
- No real GitHub/LinkedIn URLs that resolve to a person.
- Fixture files are small (< 100 KB each) and generated by `tests/fixtures/build_fixtures.py`.
