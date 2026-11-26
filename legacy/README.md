# Legacy Code

This folder is a quarantine, not part of the V2.0 product.

## `dse-curriculum/`

The original `RAG_chatbot-` repository pulled from GitHub at commit `d1b5fac`. It is a **DSE curriculum assistant**, not the RUPP Senior Project Knowledge Base.

It is kept for reference only:

- `src/` — the old Python implementation (LangChain, Qdrant, Telegram, web app, curriculum routing).
- `data/` — the old DSE curriculum sources.
- `evaluation/` — the old 59-query curriculum golden set.
- `tests/test_core.py` — the old unit tests.
- `web/` — the old local web chat.
- `README.md` — the old product documentation.
- `requirements.txt` — the old pinned environment.
- `Start Telegram Bot.cmd` — the old Windows launcher.

## Rules

- **Do not import anything from `legacy/` in V2.0 code.**
- Do not copy the old `src/` layout into the new packages.
- Use it only to study patterns worth reusing: LangChain `Document`/splitter/`QdrantVectorStore`/`BaseRetriever`, RRF plumbing, Telegram escaping and pagination, Khmer handling, SQLite logging.
- Do not delete this folder until Phase 8 is complete and the team has agreed to drop the reference copy.
- The old `.gitignore` inside `legacy/dse-curriculum/` applies only to that subtree.

See `implementation/tasks/T8.1-remove-or-quarantine-the-web-app.md` and `T8.2-retire-the-dse-curriculum-product.md` for the quarantine verification tasks.
