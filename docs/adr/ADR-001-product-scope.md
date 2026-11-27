# ADR-001: Build RUPP-SPKB v2.0 instead of continuing the DSE curriculum assistant

- **Status:** Proposed (waiting for supervisor / product-owner acceptance)
- **Date:** 2026-11-27
- **Owners:** Group 1
- **Related:** T0.1, T8.1, T8.2, T8.3; [`docs/REPO_VS_SPEC_COMPARISON.md`](../REPO_VS_SPEC_COMPARISON.md); [`implementation/tasks/README.md`](../../implementation/tasks/README.md)

## Context

The repository pulled at commit `d1b5fac` ("add new project structure") is a **DSE curriculum
assistant**: it answers questions about one bachelor program from a 51-course JSON file, a few
official PDFs and Markdown pages, and a practice slide deck. It has a web page, a DSE-only refusal
for other programs, and a 59-query curriculum golden set.

The three V2.0 specifications (PRD, Architecture Spec, End-to-End Pipeline) describe a different
product: a **person-anchored archive of past senior projects**, with seniors, aliases, authorship,
projects, portfolios, name lookup, consent-gated contact, and hybrid project search, delivered
through Telegram only. The gap analysis found 0 of the V2.0 functional requirements fully met.

Continuing the old code would mean bending a curriculum data model (courses) into a people and
projects data model, which the analysis judged slower and riskier than a clean V2.0 build.

## Decision

Build **RUPP-SPKB v2.0** as specified. Keep the pulled code only as a read-only reference in
`legacy/dse-curriculum/`; no V2.0 module may import it.

**Retired scope** (not part of V2.0):

- the web app (`legacy/dse-curriculum/web/`, `src/web_app.py`); V2.0 is Telegram-only;
- the DSE course records as product data (kept only as reference in `data/reference/dse-curriculum/`, never indexed);
- the DSE-only refusal; V2.0 accepts every program (`DSE`, `ITE`, `TEE`, `BE`, `FTCE`);
- the 59-query curriculum golden set as a V2.0 acceptance artifact (replaced by the 90-query senior-project golden set).

**Reused ideas** (re-implemented in the V2.0 packages, not imported):

- LangChain plumbing (`Document`, recursive text splitting, a `BaseRetriever` adapter);
- the hybrid dense + sparse RRF skeleton;
- Telegram hygiene (HTML escaping, message limits, rate limiting);
- Khmer text handling (normalization, word segmentation);
- SQLite query logging;
- the test style (fixtures, fakes, no network in unit tests).

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Build V2.0, quarantine the old code (chosen) | Data model fits the product; clean acceptance criteria | More code to write |
| B. Extend the curriculum assistant | Reuses working code | Wrong data model (courses, not people); DSE-only assumptions everywhere |
| C. Run both products side by side | Nothing is lost | Two products to maintain; confusing for users and graders |

## Consequences

- New data model: `seniors.csv` + `projects.csv` manifest, SQLite store, project-level chunks.
- New corpus: permission-cleared senior-project documents in `data/corpus/<project_id>/`.
- New retrieval: five intents (topic, senior, portfolio, facet, answer) with project grouping.
- New evaluation: the 90-query golden set and the metrics in Architecture §13.
- Harder: nothing from the old product can be demoed as V2.0; the old evaluation numbers do not carry over.
- Tests that guard this decision: `tests/unit/test_config.py::test_no_web_imports`, `test_no_legacy_imports`, `tests/unit/test_intent.py::test_all_programs_are_accepted`.

## Approval

- [ ] Supervisor / product owner approval (mark the status `Accepted` with the date)
- [x] Linked from `implementation/README.md` §12
- [x] Linked from `docs/REPO_VS_SPEC_COMPARISON.md`
