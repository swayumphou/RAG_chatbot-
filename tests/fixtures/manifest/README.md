# Manifest fixtures

Everything here is **invented test data**. The names are fixtures, not real students. No email
addresses, phone numbers, or student IDs are used, and the URLs use the reserved `.invalid`
domain, so they can never resolve to a real person.

The real manifest (`data/projects.csv`, `data/seniors.csv`) stays header-only until
permission-cleared data arrives (CLAUDE.md data policy). Tests and smoke runs use these files.

## `seniors.csv`

| senior_id | Why it exists |
|---|---|
| `s-2024-014` Sok Dara | Latin + Khmer + typo aliases (`Sok Dara`, `សុខ ដារា`, `sokdaraa`, `DARA Sok`). `consent_public_contact=true`. Author of two projects. |
| `s-2024-021` Chan Sreyneang | `consent_relay=true` (relay still needs the senior to start the bot). Co-author of `dse2024-001`. |
| `s-2024-031` Keo Sophea (DSE) | Same display name as `s-2024-044` — the ambiguity case. |
| `s-2024-044` Keo Sophea (ITE) | Same display name as `s-2024-031`. Must never be merged with it. |
| `s-2024-052` Lim Vuthy | Only project has no documents — metadata-only portfolio. |
| `s-2022-077` Mao Rithy | `status=withdrawn`. Present, but must be skipped and never indexed. |

## `projects.csv`

| project_id | Why it exists |
|---|---|
| `dse2024-001` | DSE, two authors, full report (PDF) + slides (PPTX). |
| `dse2024-002` | Scanned (image-only) PDF — OCR path. |
| `dse2024-003` | ITE project, DOCX report. |
| `dse2024-004` | Khmer title, `repo_url`, Markdown report. |
| `dse2024-005` | Granted, but its corpus folder is empty — metadata-only project. |
| `dse2024-006` | Blank PDF — `parse_status="failed"`, zero chunks. |
| `dse2024-007` | `permission=pending` — must be skipped and never indexed. |
| `dse2022-008` | Only author is withdrawn — must be skipped and never indexed. |

Project ids are opaque labels; `dse2024-003` is an ITE project even though its id starts with `dse`.

## `invalid/`

One rejection case per file. Each is a `projects.csv` variant checked against the valid
`seniors.csv` and the fixture corpus.

| File | Expected result |
|---|---|
| `missing_project_id.csv` | error: missing required field |
| `duplicate_project_id.csv` | error: duplicate `project_id` |
| `unknown_senior.csv` | error: unknown `senior_id` |
| `missing_folder.csv` | error: corpus folder missing |
| `pending_permission.csv` | warning + skip |
| `withdrawn_senior.csv` | warning + skip |
