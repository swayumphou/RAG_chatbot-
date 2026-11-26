---
name: rag-retrieval-and-answer
description: Use when touching intent routing, hybrid RRF retrieval, project grouping, senior entity resolution, portfolios, the relevance gate, Gemma synthesis, citations, or consent.
---

# Retrieval and Answer

## Five intents, five paths

| Intent | Signal | Path |
|---|---|---|
| `TOPIC_SEARCH` | fallback | dense + sparse RRF → group by project → top 5 |
| `SENIOR_LOOKUP` | exact name/surname, 2–4 tokens | alias → full-text → fuzzy |
| `PORTFOLIO` | "portfolio", "projects by", "works of" | resolve senior → SQLite portfolio |
| `FACET_BROWSE` | year/program/supervisor and no topic | Qdrant payload filter + SQLite counts |
| `ANSWER` | who/what/which/has anyone | retrieve → gate → extractive/Gemma |

When in doubt, route to `TOPIC_SEARCH`. A wrong senior lookup shows a confident portfolio for the wrong person — worse than a retrieval miss.

## Hybrid retrieval contract

- Dense prefetch 40 + sparse prefetch 40.
- RRF `k=60`, limit 10 before grouping.
- Group by `project_id`, keep the max score per project, return top 5 distinct projects.
- Group **after** fusion, **before** truncation.
- `matched_by` is `dense`, `lexical`, or `both` based on membership in the two prefetch lists.
- Gate uses the best project's **dense cosine**, not the RRF score.

## Entity resolution contract

1. Exact `senior_alias.alias_normalized` → confidence 1.00, `via="alias"`.
2. Qdrant full-text on `author_names_normalized` → exactly one senior → 0.90, `via="text_index"`.
3. `rapidfuzz.token_set_ratio ≥ 88` over the directory → exactly one → `score/100`, `via="fuzzy"`.

Several matches → `ambiguous` with candidates. None → `not_found` with up to 3 closest, marked not confirmed. **Never auto-merge.**

`Sok Dara`, `សុខ ដារា`, and `sokdaraa` must resolve to the same `senior_id`.

## Portfolio contract

- Served from SQLite only; works with Qdrant down.
- Newest project first.
- Contact fields only if `consent_public_contact=1`.
- Zero documents → `metadata_only=True`.
- A portfolio > 4096 chars paginates; it never truncates.

## Gate and answer contract

- Default is **extractive**: verbatim passage + project/page citation. `ENABLE_LLM=false`.
- Below `abstain_threshold` (placeholder 0.45, measured in Phase 6): `mode="abstain"`; **never call the LLM**; show up to 3 weak matches marked low confidence.
- LLM mode: Gemma 4 via Ollama, default `gemma4:12b-it-qat`, `temperature=0.0`, `num_ctx=8192`, `num_predict=600`.
- Prompt receives only the top 5 grouped projects. Contact fields never enter the prompt.
- Every factual sentence cites `[Project: <title>, p.<n>]`.
- Validator: allowed set is `{(title, page)}` from the retrieved citations. No citation or any invented title/page → `mode="extractive_fallback"`, log, serve the extractive answer.

## Common RAG-specific bugs

- Grouping by `(source_id, page)` instead of `project_id`.
- Numeric citations `[1]` instead of `[Project: title, p.N]`.
- Calling Gemma below the gate.
- Passing chunks from the same project/page as five "projects".
- Auto-picking the highest-scoring ambiguous name.
- Putting contact fields in the prompt.
- Refusing ITE because of the legacy DSE-only rule.
- Treating a BM25 score as if it were a bge-m3 sparse score.
- Using the RRF score for the gate.

## Verification

Tests: `tests/unit/test_intent.py`, `test_hybrid.py`, `test_entity.py`, `test_filters.py`, `test_portfolio.py`, `test_answer.py`, `test_validate.py`, `test_consent.py`; integration: `tests/integration/test_retrieval_pipeline.py`, `test_answer_pipeline.py`.

Run:
```powershell
python -m eval.run_eval --golden eval/golden_set.csv
```
Do not use Telegram in the measurement path.
