---
name: rag-evaluation
description: Use when touching the golden set, retrieval/answer metrics, threshold sweeps, committed baselines, CI regression gates, or training-pair export.
---

# Evaluation and Flywheel

## Golden set

`eval/golden_set.csv` is frozen before any model or threshold change.

Header:
`query_id,query,slice,expected_project_ids,expected_senior_id,notes`

Slices (Architecture §13.1, 90 queries):

| Slice | Count |
|---|---|
| `topic_en` | 25 |
| `topic_km` | 10 |
| `name_latin` | 10 |
| `name_typo` | 8 |
| `name_khmer` | 7 |
| `facet` | 10 |
| `negative` | 20 |

Rules:
- `expected_project_ids` uses `|` when any one is acceptable.
- Every name query has `expected_senior_id`.
- Every negative has no expected project and must abstain.
- The 60-query PRD figure is a minimum; the 90-query Architecture composition is the ruling.
- A change to the frozen set needs a new version and an ADR.

## Metrics

| Metric | Definition | Target |
|---|---|---|
| `Recall@5` | ≥ 1 expected project in top 5 | ≥ 0.90 |
| `MRR@10` | reciprocal rank of first expected project | ≥ 0.75 |
| `nDCG@10` | graded ranking quality | ≥ 0.80 |
| `Name top-1` | first resolved senior equals expected | ≥ 0.95 |
| `Abstention precision` | fraction of negatives that abstain | ≥ 0.95 |
| `Payload integrity` | required payload keys present | 100% |

Report **per slice**, never only an average. An English-only average hides a broken Khmer path.

## Runner

`eval/run_eval.py` calls `answer.service.answer()` directly. Telegram is never in the measurement path.

Output `eval/last_report.json`:
- golden-set SHA-256;
- `embed_model`, `llm_model`, `qdrant_version`;
- chosen threshold;
- per-slice metrics;
- per-query route, mode, top score, rank.

`eval/baseline.json` is committed after the first approved run.

## Threshold sweep

Choose the **smallest** threshold that reaches abstention precision ≥ 0.95 without dropping topic `Recall@5` below 0.90. Publish the sweep table. A magic number without the table is a finding.

## CI gate

`eval/compare_baseline.py` fails when:
- any slice's `nDCG@10` drops > 0.02;
- a required slice is missing;
- the golden-set hash differs;
- the loaded model tag differs from the baseline.

## Training pairs

`datasets/build_training_pairs.py`:
- positive = `clicked_project`;
- hard negatives = top-retrieved but not clicked, excluding the positive;
- drop `rating=-1`;
- require ≥ 1 positive;
- output JSONL: `{"query": ..., "positive_id": ..., "hard_negatives": [...], "schema_version": 1}`;
- reproducible from `query_log` alone;
- never include contact data.

## Common evaluation bugs

- Mutating the golden set after seeing results.
- Reporting only the average.
- Counting a negative as a positive.
- Calling the evaluator through Telegram.
- Not recording the model tag.
- A threshold copied from the legacy 0.72/0.49 value.
- Training pairs with no positive or with the positive in the negatives.
- Hard negatives that were never retrieved.

## Verification

```powershell
python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold
python -m eval.compare_baseline eval/baseline.json eval/last_report.json
pytest tests/unit/test_metrics.py tests/unit/test_training_pairs.py -q
pytest tests/integration/test_eval_pipeline.py -q
```
