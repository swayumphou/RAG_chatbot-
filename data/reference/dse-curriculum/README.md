# DSE Curriculum Reference Data

This folder is a **reference copy** of the data the junior found in the pulled
`RAG_chatbot-` repository. It is **not** senior-project data and is **not**
indexed by the V2.0 pipeline.

## What is here

| Path | Meaning |
|---|---|
| `rupp-dse-degree.json` | The old DSE curriculum snapshot (1 program, 51 course records). |
| `courses.csv` | The same 51 course records flattened for inspection. |
| `sources.csv` | The old source registry. |
| `documents/` | The old PDFs, Markdown guide, and practice slide deck. |
| `evaluation/golden_set_curriculum.csv` | The old 59-query curriculum golden set. |

## Why it is not in `data/projects.csv` or `data/seniors.csv`

The old data contains **course records**, not senior projects. It has no
`project_id`, senior author, project abstract, supervisor per project, or
permission row. Copying it into the project manifest would create fake
projects and would violate the V2.0 identity rule.

The V2.0 manifests stay header-only until the team supplies permission-cleared
senior-project rows.

## Rules

- Do not import this folder into Qdrant.
- Do not cite it as a senior project.
- Do not treat the old curriculum golden set as a V2.0 evaluation baseline.
- Keep the original copy under `legacy/dse-curriculum/` untouched.

## Reproduce

```powershell
python scripts/migrate_legacy_data.py
```
