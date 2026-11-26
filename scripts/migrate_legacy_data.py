"""Copy the pulled DSE curriculum material into the V2.0 reference area.

The legacy data is curriculum reference, not senior-project data. This script:

1. copies the old official/practice documents to data/reference/dse-curriculum/;
2. flattens the 51 course records into courses.csv;
3. copies the old curriculum golden set for reference;
4. writes the V2.0 manifest headers if the files are still empty.

Run from the repository root:

    python scripts/migrate_legacy_data.py

It never modifies legacy/. It never invents senior or project rows.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "legacy" / "dse-curriculum"
SOURCE = LEGACY / "data"
DEST = ROOT / "data" / "reference" / "dse-curriculum"

PROJECTS_HEADER = [
    "project_id", "title_en", "title_khmer", "year", "term", "program",
    "domain", "tech_stack", "keywords", "supervisor_id", "senior_ids",
    "abstract", "repo_url", "demo_url", "permission",
]
SENIORS_HEADER = [
    "senior_id", "full_name_latin", "full_name_khmer", "aliases", "program",
    "cohort_year", "supervisor_id", "contact_email", "github", "linkedin",
    "consent_public_contact", "consent_relay", "status",
]
GOLDEN_HEADER = [
    "query_id", "query", "slice", "expected_project_ids",
    "expected_senior_id", "notes",
]


def write_header(path: Path, header: list[str]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(header)


def copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def flatten_courses() -> int:
    data = json.loads((SOURCE / "official" / "rupp-dse-degree.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    for program in data.get("programs", []):
        for course in program.get("courses", []):
            rows.append({
                "program_id": course.get("program_id", ""),
                "program_name": course.get("program_name", ""),
                "academic_year": course.get("academic_year", ""),
                "semester": course.get("semester", ""),
                "course_name": course.get("course_name", ""),
                "credits": course.get("credits", ""),
                "source_hours": course.get("source_hours") or "",
                "option": course.get("option") or "",
                "source_url": course.get("source_url") or "",
                "retrieved_at": course.get("retrieved_at") or "",
                "verification_status": course.get("verification_status") or "",
                "review_notes": "; ".join(course.get("review_notes") or []),
            })
    out = DEST / "courses.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    return len(rows)


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"legacy data not found: {SOURCE}")

    DEST.mkdir(parents=True, exist_ok=True)
    docs = DEST / "documents"
    docs.mkdir(parents=True, exist_ok=True)

    # JSON + registry
    copy_file(SOURCE / "official" / "rupp-dse-degree.json", DEST / "rupp-dse-degree.json")
    copy_file(SOURCE / "official" / "sources.csv", DEST / "sources.csv")

    # Official documents (PDF/MD) and the practice slide deck.
    for src in sorted((SOURCE / "official").glob("*")):
        if src.suffix.lower() in {".pdf", ".md", ".txt", ".docx", ".pptx"}:
            copy_file(src, docs / src.name)
    for src in sorted((SOURCE / "practice").glob("*")):
        if src.suffix.lower() in {".pdf", ".md", ".txt", ".docx", ".pptx"}:
            copy_file(src, docs / src.name)

    # Old curriculum golden set, for reference only.
    copy_file(LEGACY / "evaluation" / "golden_set.csv",
              DEST / "evaluation" / "golden_set_curriculum.csv")

    course_count = flatten_courses()

    # V2.0 manifest headers; rows are added by T1.1/T1.2.
    write_header(ROOT / "data" / "projects.csv", PROJECTS_HEADER)
    write_header(ROOT / "data" / "seniors.csv", SENIORS_HEADER)
    write_header(ROOT / "tests" / "fixtures" / "manifest" / "projects.csv", PROJECTS_HEADER)
    write_header(ROOT / "tests" / "fixtures" / "manifest" / "seniors.csv", SENIORS_HEADER)
    write_header(ROOT / "eval" / "golden_set.csv", GOLDEN_HEADER)
    write_header(ROOT / "tests" / "fixtures" / "golden" / "sample.csv", GOLDEN_HEADER)

    readme = f"""# DSE Curriculum Reference Data

This folder is a **reference copy** of the data the junior found in the pulled
`RAG_chatbot-` repository. It is **not** senior-project data and is **not**
indexed by the V2.0 pipeline.

## What is here

| Path | Meaning |
|---|---|
| `rupp-dse-degree.json` | The old DSE curriculum snapshot (1 program, {course_count} course records). |
| `courses.csv` | The same {course_count} course records flattened for inspection. |
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
"""
    (DEST / "README.md").write_text(readme, encoding="utf-8", newline="\n")

    print(f"copied legacy data -> {DEST}")
    print(f"course records: {course_count}")
    print("V2.0 manifest headers written where empty")


if __name__ == "__main__":
    main()
