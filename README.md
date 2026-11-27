# RUPP Senior Project Knowledge Base (RUPP-SPKB v2.0)

Searchable archive of past RUPP senior projects: topic search, senior lookup, portfolios, and consent-gated contact through a Telegram bot.

**Status:** project skeleton. The implementation plan is complete; the code files are empty placeholders waiting for their task cards. The pulled DSE curriculum assistant is quarantined in `legacy/dse-curriculum/` and is not part of the V2.0 build.

## Start here

1. [`docs/Senior_Project_KB_Prd_V2_0.md`](docs/Senior_Project_KB_Prd_V2_0.md) — product requirements.
2. [`docs/Senior_Project_KB_Architecture_Spec_V2_0.md`](docs/Senior_Project_KB_Architecture_Spec_V2_0.md) — architecture and data contracts.
3. [`docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md`](docs/Senior_Project_KB_End_To_End_Pipeline_V2_0.md) — stage-by-stage pipeline.
4. [`docs/REPO_VS_SPEC_COMPARISON.md`](docs/REPO_VS_SPEC_COMPARISON.md) — what the pulled code did vs what V2.0 needs.
5. [`implementation/README.md`](implementation/README.md) — the implementation handbook.
6. [`implementation/tasks/README.md`](implementation/tasks/README.md) — the active task board.
7. [`implementation/spec/PHASES.md`](implementation/spec/PHASES.md) — phase goals and exit gates.
8. [`CLAUDE.md`](CLAUDE.md) — instructions for Claude Code.
9. [`.agents/skills/README.md`](.agents/skills/README.md) — project-specific coding skills.

**Next action:** `implementation/tasks/T0.1-product-scope-decision-adr.md`.

## Layout

```text
.
├── data/
│   ├── README.md                 # data rules
│   ├── projects.csv              # curated project manifest (header only; rows in T1.2)
│   ├── seniors.csv               # curated senior manifest (header only; rows in T1.1)
│   ├── corpus/<project_id>/      # permission-cleared senior-project documents
│   └── reference/dse-curriculum/ # migrated legacy DSE data; never indexed
├── config.py                     # settings and environment (fill in T7.2)
├── logging_config.py             # structured logs (fill in T7.3)
├── ingestion/                    # validate -> parse -> chunk -> embed -> index
├── store/                        # SQLite metadata store
├── retrieval/                    # intent, hybrid, entity, facets, portfolio
├── answer/                       # gate, extractive/Gemma, citations, consent
├── bot/                          # Telegram adapter
├── eval/                         # golden set, metrics, runner, baseline
├── datasets/                     # query log -> training pairs
├── scripts/                      # pre-demo checks
├── tests/                        # unit, integration, fixtures
├── docs/                         # product specs, comparison, ADRs
├── implementation/               # task cards, shared specs, done cards
├── .agents/skills/               # project-specific coding-agent skills
├── CLAUDE.md                     # Claude Code project instructions
└── legacy/dse-curriculum/        # pulled DSE assistant, reference only
```

## Data status

- `data/projects.csv` and `data/seniors.csv` contain headers only. No senior-project rows exist yet.
- The pulled DSE curriculum data is migrated to `data/reference/dse-curriculum/`: 51 course records, 5 documents, the old source registry, and the old curriculum golden set.
- The reference data is **not** indexed as senior projects. It is kept for context and for the old curriculum assistant.
- `data/corpus/` is empty until permission-cleared senior-project documents arrive.

## Stack

- Python 3.11
- LangChain (loaders, splitters, `Document`, Qdrant vector store, retriever)
- bge-m3 dense (1024-d) + bge-m3 sparse lexical
- Qdrant (server, hybrid RRF)
- Gemma 4 via Ollama (`gemma4:12b-it-qat`, extractive default)
- SQLite for metadata and query logs
- `python-telegram-bot` 22.8
- $0 recurring, self-hosted

## Command-line demo (no Telegram)

`cli.py` calls the same `answer()` function as the bot, so it is the offline demo path:

```powershell
python cli.py --query "khmer ocr"                              # extractive answer (default)
python cli.py --query "who built a Khmer OCR system" --llm     # let Gemma 4 write the answer
```

It prints the matching projects with `[Project: <title>, p.<n>]` citations. `--db`,
`--qdrant-url`, and `--qdrant-path` point it at the same SQLite file and Qdrant index that
`python -m ingestion.run` wrote. A missing index, model, or Qdrant server prints a one-line
error instead of a traceback.

## Notes

- Empty files are intentional placeholders. Each one is owned by a task card in `implementation/tasks/`.
- Do not import from `legacy/` in any V2.0 module.
- Never commit real student documents, contact details, or model secrets.
