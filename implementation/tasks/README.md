# Task Cards

Active task cards. One task per file. A task is small enough to implement, test, and review in one sitting.

## Workflow

1. Pick the lowest-numbered unblocked task.
2. Read the card, its **Spec**, and the shared contracts in `../spec/`.
3. Branch from `refactored`: `git switch -c task/T0.1-...`
4. Implement only the files in the card.
5. Write the tests named in the card.
6. Commit: `feat(T<id>): <summary>`.
7. Set the card's **Status** to `REVIEW` and run this script.
8. When the manager marks it done, move the card to `../done/` and run this script again.

## Status values

`TODO` -> `IN PROGRESS` -> `REVIEW` -> `DONE`. `BLOCKED` is allowed with a written reason.

## Index

| ID | Task | Phase | Depends on | Status |
|---|---|---|---|---|
| T0.1 | [Product-scope decision ADR](T0.1-product-scope-decision-adr.md) | 0 - Scope and Foundations | none | REVIEW |
| T0.2 | [Verify the initialized skeleton and branch](T0.2-v2-0-branch-and-package-skeleton.md) | 0 - Scope and Foundations | T0.1 | REVIEW |
| T1.1 | [Seniors manifest fixture](T1.1-seniors-manifest-fixture.md) | 1 - Data Model and Manifest | T0.3 | REVIEW |
| T1.3 | [Corpus fixture set](T1.3-corpus-fixture-set.md) | 1 - Data Model and Manifest | T1.2 | REVIEW |
| T2.2 | [PDF parser and OCR fallback](T2.2-pdf-parser-and-ocr-fallback.md) | 2 - Project-Aware Ingestion and Indexing | T2.1 | REVIEW |
| T2.4 | [PPTX parser](T2.4-pptx-parser.md) | 2 - Project-Aware Ingestion and Indexing | T2.1 | REVIEW |
| T2.8 | [Qdrant indexer and integrity verifier](T2.8-qdrant-indexer-and-integrity-verifier.md) | 2 - Project-Aware Ingestion and Indexing | T2.7 | REVIEW |
| T4.3 | [Extractive answer and citations](T4.3-extractive-answer-and-citations.md) | 4 - Extractive-First Answer Layer | T4.2 | REVIEW |
| T5.1 | [Bot bootstrap and private-chat envelope](T5.1-bot-bootstrap-and-private-chat-envelope.md) | 5 - Telegram Interface | T4.7 | REVIEW |
| T6.3 | [Evaluation runner, threshold sweep, and baseline](T6.3-evaluation-runner-threshold-sweep-and-baseline.md) | 6 - Evaluation and Training-Pair Flywheel | T6.2, T4.2 | REVIEW |
| T7.1 | [Dockerfile and Compose stack](T7.1-dockerfile-and-compose-stack.md) | 7 - Deployment, Configuration, and Observability | T2.9, T6.3 | REVIEW |
| T7.5 | [CI pipeline](T7.5-ci-pipeline.md) | 7 - Deployment, Configuration, and Observability | T6.6, T7.3 | REVIEW |
| T8.3 | [Final layout, dead code, and README](T8.3-final-layout-dead-code-and-readme.md) | 8 - Retire the Reference Product | T8.1, T8.2 | REVIEW |

---

## Phase exit gates

See `../spec/PHASES.md`. Do not start a phase until the previous phase's exit gate is green.
