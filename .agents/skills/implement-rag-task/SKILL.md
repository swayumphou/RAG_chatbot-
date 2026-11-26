---
name: implement-rag-task
description: Use when picking up a task card in implementation/tasks/, branching, implementing only its files, writing its tests, and reporting without scope creep.
---

# Implement a RUPP-SPKB Task Card

## Goal

Close exactly one task card with a reviewable diff, the named tests, and an honest report.

## Workflow

1. Read `implementation/tasks/README.md` and pick the lowest-numbered unblocked task.
2. Read the task card, its dependencies, and every file in its **Spec** and **Interfaces** fields.
3. Read `implementation/spec/INTERFACES.md` for the exact names/types and `implementation/spec/TESTING.md` for test mechanics.
4. Read the matching skill:
   - data/indexing → `rag-data-and-indexing`
   - retrieval/answer → `rag-retrieval-and-answer`
   - evaluation → `rag-evaluation`
   - Telegram/privacy → `rag-telegram-and-privacy`
5. Branch from `refactored`: `git switch -c task/T1.5-manifest-validator`
6. Implement **only** the files listed in the card.
7. Write the tests named in the card. Tests are part of the deliverable.
8. Run `python -m py_compile` on every changed Python file.
9. If the human asks, run the card's primary test. Do not run the full suite unprompted.
10. Update the card checkboxes and `**Status:** REVIEW`.
11. Run `python implementation/tools/rebuild_index.py`.
12. Report: status, files changed, tests written, what was verified, what was not, concerns.
13. Do not commit, stage, or push unless the human explicitly asks.

## Task-card rules

- The card's **Spec** is binding; the rest of the repo is context.
- The card's **Files** list is the diff boundary. If another file must change, update the card first or ask.
- The card's **Interfaces** must match `INTERFACES.md`; do not invent a new return shape.
- The card's **Acceptance** is the review gate.
- The card's **Tests** are the minimum proof.
- The card's **Out of scope** is not a suggestion; do not expand into it.

## Definition of done

- [ ] Files match the card; no unrelated changes.
- [ ] Interfaces match `INTERFACES.md`.
- [ ] Tests exist and cover acceptance.
- [ ] `python -m py_compile` passes.
- [ ] `ruff`/`black` pass if available.
- [ ] No secrets, real student data, or generated indexes committed.
- [ ] Card updated; index regenerated.
- [ ] Report states what was not verified.

## Common scope-creep traps

- Filling every empty module because the skeleton is empty.
- Implementing a later task's helper "while here."
- Adding a dependency without updating `requirements.txt` and the card.
- Refactoring `legacy/`.
- Running the full test suite or starting Docker/Ollama without being asked.
- Committing or pushing to `refactored`.

## Reporting format

```text
Status: REVIEW / BLOCKED
Files: <list>
Tests written: <list>
Verified: py_compile / primary test / not run
Not verified: <list>
Concerns: <spec conflicts, missing data, assumptions>
```

If blocked, name the missing decision, the task ID, and the two options. Do not guess silently.
