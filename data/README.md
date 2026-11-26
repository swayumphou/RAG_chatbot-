# Data

## V2.0 manifests

| File | Purpose | Status |
|---|---|---|
| `projects.csv` | Curated project manifest (one row per project) | Header only until T1.2 supplies rows |
| `seniors.csv` | Curated senior manifest (one row per senior) | Header only until T1.1 supplies rows |
| `corpus/<project_id>/` | Permission-cleared reports, slides, posters, notes | Empty until T1.3/T2.9 |

These are the only inputs the V2.0 ingestion pipeline may index.

## Reference data

`reference/dse-curriculum/` is a migrated copy of the old DSE curriculum
material found in the pulled `RAG_chatbot-` repository. It contains 51 course
records, the old source registry, documents, and the old curriculum golden set.

It is **not** senior-project data and must not be indexed by the V2.0 pipeline.
See `reference/dse-curriculum/README.md` for the migration details.

## Rules

- Only `permission=granted` projects enter `projects.csv`.
- Only documents inside `corpus/<project_id>/` enter Qdrant.
- Never add contact details, student IDs, grades, or phone numbers.
- Do not copy reference data into `corpus/`.
- The manifest is the source of truth; a PDF never decides identity.
