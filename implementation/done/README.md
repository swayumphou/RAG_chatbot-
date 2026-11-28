# Done Task Cards

Completed task cards live here. A card is moved here only after the manager has reviewed the spec compliance and the primary test.

## Move procedure

1. The implementer sets the card's **Status** to `REVIEW`.
2. The manager runs the card's primary test and reviews the diff.
3. If approved:
   - change the card's **Status** to `DONE`;
   - add `**Completed:** YYYY-MM-DD`, `**Commit:** <hash>`, and `**Reviewed by:** <name>` under the status line;
   - move the file from `../tasks/` to this folder;
   - run `python implementation/tools/rebuild_index.py`.
4. If changes are requested, keep it in `../tasks/` and write the findings in the card.

## Completed tasks

| ID | Task | Completed | Commit | Reviewed by |
|---|---|---|---|---|
| T0.3 | [Environment and tooling baseline](T0.3-environment-and-tooling-baseline.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T1.2 | [Projects manifest fixture](T1.2-projects-manifest-fixture.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T1.4 | [SQLite schema and store API](T1.4-sqlite-schema-and-store-api.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T1.5 | [Manifest validator](T1.5-manifest-validator.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T1.6 | [Manifest diff and validation CLI](T1.6-manifest-diff-and-validation-cli.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T2.1 | [Parser contract and registry](T2.1-parser-contract-and-registry.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T2.3 | [DOCX parser](T2.3-docx-parser.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T2.5 | [Markdown parser](T2.5-markdown-parser.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T2.6 | [Structure-aware chunker](T2.6-structure-aware-chunker.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T2.7 | [bge-m3 embedder](T2.7-bge-m3-embedder.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T2.9 | [Ingestion orchestrator and report](T2.9-ingestion-orchestrator-and-report.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.1 | [Normalization and Khmer segmentation](T3.1-normalization-and-khmer-segmentation.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.2 | [Five-intent router](T3.2-five-intent-router.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.3 | [Hybrid retrieval and project grouping](T3.3-hybrid-retrieval-and-project-grouping.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.4 | [Qdrant filters and facet counts](T3.4-qdrant-filters-and-facet-counts.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.5 | [Senior entity resolution](T3.5-senior-entity-resolution.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.6 | [Portfolio assembly](T3.6-portfolio-assembly.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.7 | [Multi-intent intersection](T3.7-multi-intent-intersection.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T3.8 | [Retrieval integration tests](T3.8-retrieval-integration-tests.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T4.1 | [Answer service contract and dispatch](T4.1-answer-service-contract-and-dispatch.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T4.2 | [Relevance gate and abstention](T4.2-relevance-gate-and-abstention.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T4.4 | [Gemma 4 synthesis](T4.4-gemma-4-synthesis.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T4.5 | [Citation validator and fallback](T4.5-citation-validator-and-fallback.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T4.6 | [Consent-gated contact card](T4.6-consent-gated-contact-card.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T4.7 | [CLI fallback](T4.7-cli-fallback.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.2 | [Commands, plain text, and feedback](T5.2-commands-plain-text-and-feedback.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.3 | [Formatter and project cards](T5.3-formatter-and-project-cards.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.4 | [Portfolio rendering and pagination](T5.4-portfolio-rendering-and-pagination.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.5 | [Callback state and ambiguity keyboard](T5.5-callback-state-and-ambiguity-keyboard.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.6 | [Deep links and facet chips](T5.6-deep-links-and-facet-chips.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.7 | [Sender throttle, 429, and duplicate cache](T5.7-sender-throttle-429-and-duplicate-cache.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.8 | [Consent-gated relay](T5.8-consent-gated-relay.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T5.9 | [Bot integration tests](T5.9-bot-integration-tests.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T6.1 | [Golden set schema and slices](T6.1-golden-set-schema-and-slices.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T6.2 | [Metrics library](T6.2-metrics-library.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T6.4 | [Query logging and click tracking](T6.4-query-logging-and-click-tracking.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T6.5 | [Training-pair exporter](T6.5-training-pair-exporter.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T6.6 | [CI regression gate](T6.6-ci-regression-gate.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T7.2 | [Configuration and secrets](T7.2-configuration-and-secrets.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T7.3 | [Observability](T7.3-observability.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T7.4 | [Pre-demo health check](T7.4-pre-demo-health-check.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T8.1 | [Verify the web app quarantine](T8.1-remove-or-quarantine-the-web-app.md) | 2026-11-28 | 2a63e3f | swayumphou |
| T8.2 | [Verify the DSE curriculum quarantine](T8.2-retire-the-dse-curriculum-product.md) | 2026-11-28 | 2a63e3f | swayumphou |
