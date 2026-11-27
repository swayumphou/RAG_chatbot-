# ADR-005: The bot reaches IDs, forced intents, and facet chips through `answer()` keyword arguments

- **Status:** Accepted (chosen by the project owner on 2026-11-27)
- **Date:** 2026-11-27
- **Owners:** Group 1
- **Related:** T4.1, T5.2, T5.4, T5.5, T5.6, T5.9; INTERFACES.md §5.3; CLAUDE.md rule 9

## Context

`answer(query, client, embedder, db, abstain_threshold, enable_llm)` only accepts typed text.
Several Telegram actions point at an exact record instead of text:

- `/senior <name>` must do a name lookup even when the router would pick `TOPIC_SEARCH`;
- a candidate `pick`, the `Portfolio` button, and `?start=senior_<id>` need a portfolio by `senior_id`;
- the `Abstract` button and `?start=proj_<id>` need one project by `project_id`;
- facet chips and `Clear all` need a search with a facet dict.

CLAUDE.md rule 9 and T5.2 require exactly one `answer()` call per interaction, and T5.9 forbids
handlers from importing retrieval modules.

## Decision

Add optional keyword-only arguments to `answer()`; at most one is used per call:

| Argument | Used by | Result |
|---|---|---|
| `intent="SENIOR_LOOKUP"` (any of the five intents) | `/senior`, `/year` | Router skipped; the text is used as given |
| `senior_id="s-..."` | `pick`, `Portfolio`, `senior_<id>` | `mode="portfolio"`, or `not_found` for an unknown/withdrawn senior |
| `project_id="..."` | `Abstract`, `proj_<id>` | `mode="project"`, one `Citation` from SQLite; `not_found` with `PROJECT_NOT_FOUND` |
| `facets={...}` | facet chips; `{}` = Clear all | `FACET_BROWSE` with the given filters |

## Options considered

| Option | Pros | Cons |
|---|---|---|
| A. Keyword arguments on `answer()` (chosen) | One call per interaction; handlers stay thin; explicit | Wider `answer()` signature |
| B. Encode IDs as text (`"senior_s-2024-014"`) | No signature change | Mixes commands into the router; fragile |
| C. Separate service functions | Clear names | Breaks "one `answer()` call per interaction" |

## Consequences

- Easier: every bot action is one `answer()` call; handlers never import `retrieval`.
- Harder: `answer()` has more arguments to test; the defaults keep the original behaviour.
- Tests: `tests/unit/test_answer.py::test_bot_entry_points`.

## Approval

- [x] Project owner chose option A (2026-11-27)
- [x] Linked from `implementation/README.md` §9
- [x] Recorded in `implementation/spec/INTERFACES.md` §5.3
