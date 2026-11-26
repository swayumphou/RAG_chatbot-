# Corpus Layout

One folder per project, named exactly by `project_id`:

```text
data/corpus/
└── dse2024-001/
    ├── report.pdf
    ├── slides.pptx
    ├── poster.pdf
    └── notes.md
```

Rules:

- Only projects with `permission=granted` in `data/projects.csv` are indexed.
- The folder name must match the manifest `project_id`.
- A project may have one to three documents; the report is the primary document.
- Do not commit documents that are not permission-cleared.
- Do not put contact details, student IDs, grades, or phone numbers in a document or filename.
- The parser records `doc_id` as `<project_id>__<filename>`.

The fixture corpus used by tests lives in `tests/fixtures/corpus/`, not here.
