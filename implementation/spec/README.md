# Specs

This folder holds the shared implementation contracts. The canonical product specs remain in `../../docs/`.

## Canonical product specs

- [`../../docs/Senior_Project_KB_Prd_V2_0.md`](../../docs/Senior_Project_KB_Prd_V2_0.md)
- [`../../docs/Senior_Project_KB_Architecture_Spec_V2_0.md`](../../docs/Senior_Project_KB_Architecture_Spec_V2_0.md)
- [`../../docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md`](../../docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md)
- [`../../docs/REPO_VS_SPEC_COMPARISON.md`](../../docs/REPO_VS_SPEC_COMPARISON.md) — what the pulled legacy code did vs the target.

## Shared implementation contracts

| File | Use |
|---|---|
| [`INTERFACES.md`](INTERFACES.md) | CSV schemas, SQLite DDL, Qdrant payload, Python signatures, callback data, config values |
| [`TESTING.md`](TESTING.md) | Fixtures, test commands, fakes, evaluation protocol, CI |
| [`TRACEABILITY.md`](TRACEABILITY.md) | FR / acceptance / pipeline → task mapping |
| [`PHASES.md`](PHASES.md) | Phase goals, dependencies, exit gates |
| [`TASK_TEMPLATE.md`](TASK_TEMPLATE.md) | Template for new task cards |

## Rules

- A task card may add detail but may not contradict `INTERFACES.md`.
- If a task needs a new field, signature, or callback, change `INTERFACES.md` first and note the change in the task card.
- If a spec conflict is discovered, add an ADR under `../../docs/adr/` and link it from `../../implementation/README.md` §9.
- Never copy a real student document or personal data into a fixture or a spec.

## Task specs

Each task card in `../tasks/` contains its own **Spec** section: exact requirements, values, edge cases, files, interfaces, steps, and acceptance tests. `TASK_TEMPLATE.md` shows the required shape.
