# Architecture Decision Records

Every decision that changes the product scope, a shared interface, or a runtime assumption is recorded here.

## Naming

`ADR-<number>-<short-kebab-title>.md`

Examples:
- `ADR-001-product-scope.md`
- `ADR-002-embedding-runtime.md`
- `ADR-003-eval-baseline.md`

## Status values

`Proposed` -> `Accepted` -> `Superseded` (with a link to the newer ADR).

## Rules

- One decision per ADR.
- State the context and the forces, not only the answer.
- Record the consequences, including what becomes harder.
- Link the ADR from `implementation/README.md` §9 and from the relevant task card.
- Do not rewrite an accepted ADR; supersede it with a new one.

Use `ADR-TEMPLATE.md` as the starting point.
