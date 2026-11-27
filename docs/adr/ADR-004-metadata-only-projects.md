# ADR-004: A granted project with an empty corpus folder is a warning, not an error

- **Status:** Proposed
- **Date:** 2026-11-27
- **Owners:** Group 1 (implementation by Claude Code, awaiting human approval)
- **Related:** T1.2, T1.3, T1.5, T2.9, T3.6; PRD FR-01, FR-07

## Context

Two task cards disagree:

- **T1.5** lists "`permission=granted` project with no files in its folder" as a validation **error**.
- **T1.2 / T1.3 / TESTING §2.1** require a granted project with **no documents** (`dse2024-005`)
  in the *valid* fixture, and **FR-07** requires "a senior with zero parsed documents still yields
  a valid, clearly-labelled metadata-only portfolio".

If an empty folder were an error, the valid fixture could never pass validation and a metadata-only
portfolio could never exist.

## Decision

- A granted project whose corpus folder is **missing** is an **error** (FR-01).
- A granted project whose folder **exists but holds no documents** is a **warning**
  ("metadata-only"). It is stored in SQLite, produces zero chunks, is reported as
  `status="failed"` with reason `no documents (metadata-only project)` in the ingestion report
  (T2.9: zero chunks is reported as failed), and its senior gets a metadata-only portfolio.
- Hidden files such as `.gitkeep` do not count as documents.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Empty folder = error (T1.5 literally) | Strict | Valid fixture fails; FR-07 impossible |
| B. Empty folder = warning (chosen) | FR-07 works; missing folder still caught | A forgotten upload is only a warning |
| C. No folder needed at all | Simplest | Typos in `project_id` would go unnoticed |

## Consequences

- Easier: metadata-only portfolios work and the fixture manifest validates.
- Harder: a project whose documents were forgotten is only flagged as a warning; check the
  ingestion report's `failed` projects before a demo.
- Tests that depend on it: `tests/unit/test_manifest.py::test_valid_manifest_and_rejections`,
  `test_metadata_only_project_is_a_warning`, `tests/integration/test_ingestion_pipeline.py`.

## Approval

- [ ] Supervisor / product owner approval
- [x] Linked from `implementation/README.md` §9
- [x] Linked from the affected task card(s)
