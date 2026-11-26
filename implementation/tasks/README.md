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
| T0.1 | [Product-scope decision ADR](T0.1-product-scope-decision-adr.md) | 0 - Scope and Foundations | none | TODO |
| T0.2 | [Verify the initialized skeleton and branch](T0.2-v2-0-branch-and-package-skeleton.md) | 0 - Scope and Foundations | T0.1 | TODO |
| T0.3 | [Environment and tooling baseline](T0.3-environment-and-tooling-baseline.md) | 0 - Scope and Foundations | T0.2 | TODO |
| T1.1 | [Seniors manifest fixture](T1.1-seniors-manifest-fixture.md) | 1 - Data Model and Manifest | T0.3 | TODO |
| T1.2 | [Projects manifest fixture](T1.2-projects-manifest-fixture.md) | 1 - Data Model and Manifest | T1.1 | TODO |
| T1.3 | [Corpus fixture set](T1.3-corpus-fixture-set.md) | 1 - Data Model and Manifest | T1.2 | TODO |
| T1.4 | [SQLite schema and store API](T1.4-sqlite-schema-and-store-api.md) | 1 - Data Model and Manifest | T1.1, T1.2 | TODO |
| T1.5 | [Manifest validator](T1.5-manifest-validator.md) | 1 - Data Model and Manifest | T1.4 | TODO |
| T1.6 | [Manifest diff and validation CLI](T1.6-manifest-diff-and-validation-cli.md) | 1 - Data Model and Manifest | T1.5 | TODO |
| T2.1 | [Parser contract and registry](T2.1-parser-contract-and-registry.md) | 2 - Project-Aware Ingestion and Indexing | T1.4 | TODO |
| T2.2 | [PDF parser and OCR fallback](T2.2-pdf-parser-and-ocr-fallback.md) | 2 - Project-Aware Ingestion and Indexing | T2.1 | TODO |
| T2.3 | [DOCX parser](T2.3-docx-parser.md) | 2 - Project-Aware Ingestion and Indexing | T2.1 | TODO |
| T2.4 | [PPTX parser](T2.4-pptx-parser.md) | 2 - Project-Aware Ingestion and Indexing | T2.1 | TODO |
| T2.5 | [Markdown parser](T2.5-markdown-parser.md) | 2 - Project-Aware Ingestion and Indexing | T2.1 | TODO |
| T2.6 | [Structure-aware chunker](T2.6-structure-aware-chunker.md) | 2 - Project-Aware Ingestion and Indexing | T2.2–T2.5 | TODO |
| T2.7 | [bge-m3 embedder](T2.7-bge-m3-embedder.md) | 2 - Project-Aware Ingestion and Indexing | T2.6 | TODO |
| T2.8 | [Qdrant indexer and integrity verifier](T2.8-qdrant-indexer-and-integrity-verifier.md) | 2 - Project-Aware Ingestion and Indexing | T2.7 | TODO |
| T2.9 | [Ingestion orchestrator and report](T2.9-ingestion-orchestrator-and-report.md) | 2 - Project-Aware Ingestion and Indexing | T1.5, T2.8 | TODO |
| T3.1 | [Normalization and Khmer segmentation](T3.1-normalization-and-khmer-segmentation.md) | 3 - Five-Intent Retrieval | T1.4 | TODO |
| T3.2 | [Five-intent router](T3.2-five-intent-router.md) | 3 - Five-Intent Retrieval | T3.1, T1.4 | TODO |
| T3.3 | [Hybrid retrieval and project grouping](T3.3-hybrid-retrieval-and-project-grouping.md) | 3 - Five-Intent Retrieval | T2.8, T3.1 | TODO |
| T3.4 | [Qdrant filters and facet counts](T3.4-qdrant-filters-and-facet-counts.md) | 3 - Five-Intent Retrieval | T3.3, T1.4 | TODO |
| T3.5 | [Senior entity resolution](T3.5-senior-entity-resolution.md) | 3 - Five-Intent Retrieval | T3.1, T1.4, T2.8 | TODO |
| T3.6 | [Portfolio assembly](T3.6-portfolio-assembly.md) | 3 - Five-Intent Retrieval | T1.4 | TODO |
| T3.7 | [Multi-intent intersection](T3.7-multi-intent-intersection.md) | 3 - Five-Intent Retrieval | T3.2, T3.3, T3.5 | TODO |
| T3.8 | [Retrieval integration tests](T3.8-retrieval-integration-tests.md) | 3 - Five-Intent Retrieval | T3.3–T3.7 | TODO |
| T4.1 | [Answer service contract and dispatch](T4.1-answer-service-contract-and-dispatch.md) | 4 - Extractive-First Answer Layer | T3.8 | TODO |
| T4.2 | [Relevance gate and abstention](T4.2-relevance-gate-and-abstention.md) | 4 - Extractive-First Answer Layer | T4.1, T3.3 | TODO |
| T4.3 | [Extractive answer and citations](T4.3-extractive-answer-and-citations.md) | 4 - Extractive-First Answer Layer | T4.2 | TODO |
| T4.4 | [Gemma 4 synthesis](T4.4-gemma-4-synthesis.md) | 4 - Extractive-First Answer Layer | T4.1 | TODO |
| T4.5 | [Citation validator and fallback](T4.5-citation-validator-and-fallback.md) | 4 - Extractive-First Answer Layer | T4.4 | TODO |
| T4.6 | [Consent-gated contact card](T4.6-consent-gated-contact-card.md) | 4 - Extractive-First Answer Layer | T1.4, T4.1 | TODO |
| T4.7 | [CLI fallback](T4.7-cli-fallback.md) | 4 - Extractive-First Answer Layer | T4.1 | TODO |
| T5.1 | [Bot bootstrap and private-chat envelope](T5.1-bot-bootstrap-and-private-chat-envelope.md) | 5 - Telegram Interface | T4.7 | TODO |
| T5.2 | [Commands, plain text, and feedback](T5.2-commands-plain-text-and-feedback.md) | 5 - Telegram Interface | T5.1 | TODO |
| T5.3 | [Formatter and project cards](T5.3-formatter-and-project-cards.md) | 5 - Telegram Interface | T5.2 | TODO |
| T5.4 | [Portfolio rendering and pagination](T5.4-portfolio-rendering-and-pagination.md) | 5 - Telegram Interface | T3.6, T5.3 | TODO |
| T5.5 | [Callback state and ambiguity keyboard](T5.5-callback-state-and-ambiguity-keyboard.md) | 5 - Telegram Interface | T5.3, T3.5 | TODO |
| T5.6 | [Deep links and facet chips](T5.6-deep-links-and-facet-chips.md) | 5 - Telegram Interface | T3.4, T5.2 | TODO |
| T5.7 | [Sender throttle, 429, and duplicate cache](T5.7-sender-throttle-429-and-duplicate-cache.md) | 5 - Telegram Interface | T5.3 | TODO |
| T5.8 | [Consent-gated relay](T5.8-consent-gated-relay.md) | 5 - Telegram Interface | T4.6, T5.1 | TODO |
| T5.9 | [Bot integration tests](T5.9-bot-integration-tests.md) | 5 - Telegram Interface | T5.1–T5.8 | TODO |
| T6.1 | [Golden set schema and slices](T6.1-golden-set-schema-and-slices.md) | 6 - Evaluation and Training-Pair Flywheel | T5.9 | TODO |
| T6.2 | [Metrics library](T6.2-metrics-library.md) | 6 - Evaluation and Training-Pair Flywheel | T6.1 | TODO |
| T6.3 | [Evaluation runner, threshold sweep, and baseline](T6.3-evaluation-runner-threshold-sweep-and-baseline.md) | 6 - Evaluation and Training-Pair Flywheel | T6.2, T4.2 | TODO |
| T6.4 | [Query logging and click tracking](T6.4-query-logging-and-click-tracking.md) | 6 - Evaluation and Training-Pair Flywheel | T5.2, T1.4 | TODO |
| T6.5 | [Training-pair exporter](T6.5-training-pair-exporter.md) | 6 - Evaluation and Training-Pair Flywheel | T6.4 | TODO |
| T6.6 | [CI regression gate](T6.6-ci-regression-gate.md) | 6 - Evaluation and Training-Pair Flywheel | T6.3, T6.5 | TODO |
| T7.1 | [Dockerfile and Compose stack](T7.1-dockerfile-and-compose-stack.md) | 7 - Deployment, Configuration, and Observability | T2.9, T6.3 | TODO |
| T7.2 | [Configuration and secrets](T7.2-configuration-and-secrets.md) | 7 - Deployment, Configuration, and Observability | T0.3 | TODO |
| T7.3 | [Observability](T7.3-observability.md) | 7 - Deployment, Configuration, and Observability | T4.4, T5.7, T6.4 | TODO |
| T7.4 | [Pre-demo health check](T7.4-pre-demo-health-check.md) | 7 - Deployment, Configuration, and Observability | T7.1–T7.3, T6.3 | TODO |
| T7.5 | [CI pipeline](T7.5-ci-pipeline.md) | 7 - Deployment, Configuration, and Observability | T6.6, T7.3 | TODO |
| T8.1 | [Verify the web app quarantine](T8.1-remove-or-quarantine-the-web-app.md) | 8 - Retire the Reference Product | T5.9 | TODO |
| T8.2 | [Verify the DSE curriculum quarantine](T8.2-retire-the-dse-curriculum-product.md) | 8 - Retire the Reference Product | T3.8, T4.7 | TODO |
| T8.3 | [Final layout, dead code, and README](T8.3-final-layout-dead-code-and-readme.md) | 8 - Retire the Reference Product | T8.1, T8.2 | TODO |

---

## Phase exit gates

See `../spec/PHASES.md`. Do not start a phase until the previous phase's exit gate is green.
