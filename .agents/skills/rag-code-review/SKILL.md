---
name: rag-code-review
description: Use when reviewing a RUPP-SPKB task branch or the refactored branch; checks spec compliance, RAG-specific failure modes, privacy, security, and tests.
---

# RAG Code Review

## Review order

1. Read the task card and its acceptance criteria.
2. Read the diff against the card's **Files** list.
3. Check `implementation/spec/INTERFACES.md` for every changed public name/type.
4. Run the card's primary test if the human asks.
5. Check the RAG-specific failure modes below.
6. Report findings by severity; do not rubber-stamp.

## Severity

| Prefix | Meaning |
|---|---|
| **Critical** | Data loss, wrong person, privacy leak, broken ingestion, fabricated citation |
| **Required** | Spec violation, missing test, interface drift, wrong payload |
| **Optional** | Improvement that does not block |
| **Nit** | Style only |

## Correctness checklist

- [ ] The card's acceptance criteria are met.
- [ ] The diff stays inside the card's files.
- [ ] Public names/types match `INTERFACES.md`.
- [ ] No behavior implemented outside the card.
- [ ] Error paths handled: empty corpus, missing model, Qdrant down, timeout, malformed CSV.
- [ ] No silent fallback that changes ranking semantics.

## RAG-specific failure modes

### Ingestion
- [ ] `data/reference/` or `legacy/` is not indexed.
- [ ] Permission `granted` is required; withdrawn seniors skipped.
- [ ] `project_id`/`senior_id`/folder referential integrity checked before Qdrant writes.
- [ ] Abstract atomic; references/bibliography/appendix dropped; min 80.
- [ ] OCR flag set; zero-text document `failed`, not `ok`.
- [ ] Breadcrumb has project/author/year/program/section/page.
- [ ] Payload keys complete; payload indexes exist.
- [ ] Point IDs uuid5; re-run adds zero points.
- [ ] Qdrant and SQLite do not disagree after a failure.

### Retrieval
- [ ] Dense + bge-m3 sparse, RRF, prefetch 40/40.
- [ ] Group by `project_id` before truncation; top 5 distinct projects.
- [ ] Gate uses best dense cosine, not RRF score.
- [ ] Entity resolution alias → full-text → rapidfuzz 88; ambiguity never merged.
- [ ] Facets are Qdrant filters, not Python post-filters.
- [ ] Portfolio works with Qdrant down; newest first.

### Answer
- [ ] Extractive default; `ENABLE_LLM=false` useful.
- [ ] No LLM call below the gate.
- [ ] Prompt contains only top 5 grouped projects; no contact data.
- [ ] Citations `[Project: title, p.N]`; validator checks the allowed set.
- [ ] Bad citation → extractive fallback and log.
- [ ] `num_ctx=8192`, `num_predict=600`, temp 0.

### Telegram/privacy
- [ ] Thin handler; one `answer()` call.
- [ ] HTML escaped; ≤ 4096; callback ≤ 64 B.
- [ ] Per-chat lock; 429 retry; duplicate cache.
- [ ] Ambiguous name keyboard; no auto-pick.
- [ ] Consent gates contact; relay hides identities.
- [ ] No raw Telegram ID, token, email, phone, or student ID in logs.

## Test review

- [ ] Named tests exist and assert behavior, not implementation details.
- [ ] Negative and edge cases covered.
- [ ] No test depends on a live model, Telegram, or real data.
- [ ] The test would fail if the bug were reintroduced.
- [ ] The golden set was not mutated to make a test pass.

## Output format

```text
Verdict: APPROVE / REQUEST CHANGES
Critical: ...
Required: ...
Optional: ...
Tests: <what was run or not run>
```

If a real issue exists, state it directly. If the author has full context and disagrees, defer to their judgment, but record the decision.
