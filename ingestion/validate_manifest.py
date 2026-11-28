"""Step 00: check projects.csv, seniors.csv, and the corpus folders.

Nothing is written here: no Qdrant, no SQLite, no network. If the report has
errors, ingestion stops before it touches the index.

Errors (stop the run):
- a required field is empty
- duplicate project_id or senior_id
- a project lists a senior_id that is not in seniors.csv
- a granted project has no corpus folder
- a bad year, program, status, or true/false value

Warnings (the run continues):
- permission is not "granted"            -> project skipped
- a project has a withdrawn author       -> project skipped
- a granted project folder is empty      -> metadata-only project (no chunks)
- a corpus folder or file has no manifest row (orphan)

Run it:
    python -m ingestion.validate_manifest --projects data/projects.csv \
        --seniors data/seniors.csv --corpus data/corpus [--json]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from retrieval.intent import PROGRAMS, normalize
from store.db import parse_bool, split_list

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PROJECT = [
    "project_id",
    "title_en",
    "year",
    "program",
    "supervisor_id",
    "senior_ids",
    "abstract",
    "permission",
]
REQUIRED_SENIOR = ["senior_id", "full_name_latin", "program", "cohort_year"]

DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".txt"}
CONTACT_FIELDS = {"contact_email", "github", "linkedin", "website"}
YEAR_RE = re.compile(r"^20\d\d$")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    corpus_hash: str = ""
    projects: list[dict] = field(default_factory=list)  # only projects we may ingest
    seniors: list[dict] = field(default_factory=list)  # all valid seniors (incl. withdrawn)
    diff: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        """JSON view of the report. Contact fields are removed (they never go to logs)."""
        data = asdict(self)
        data["seniors"] = [
            {k: v for k, v in s.items() if k not in CONTACT_FIELDS} for s in self.seniors
        ]
        data["ok"] = self.ok
        return data


# ---------------------------------------------------------------- corpus


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def corpus_hash(corpus_dir: Path) -> str:
    """One SHA-256 for the whole corpus: relative path + file hash, in sorted order.

    Sorting makes it independent of file-system order, so an unchanged corpus
    always gives the same hash. Hidden files such as .gitkeep are ignored.
    """
    corpus_dir = Path(corpus_dir)
    digest = hashlib.sha256()
    if not corpus_dir.is_dir():
        return digest.hexdigest()
    files = [p for p in corpus_dir.rglob("*") if p.is_file()]
    for path in sorted(files, key=lambda p: p.relative_to(corpus_dir).as_posix()):
        relative = path.relative_to(corpus_dir)
        if is_hidden(relative):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def project_files(corpus_dir: Path, project_id: str) -> list[Path]:
    """All visible files in data/corpus/<project_id>/."""
    folder = Path(corpus_dir) / project_id
    if not folder.is_dir():
        return []
    files = [p for p in folder.rglob("*") if p.is_file() and not is_hidden(p.relative_to(folder))]
    return sorted(files)


def project_documents(corpus_dir: Path, project_id: str) -> list[Path]:
    """Only the files we can parse (pdf, docx, pptx, md, txt)."""
    return [
        p for p in project_files(corpus_dir, project_id) if p.suffix.lower() in DOCUMENT_EXTENSIONS
    ]


# ---------------------------------------------------------------- rows


def clean_project(row: dict) -> dict:
    """A project row with real types: lists for ;-fields and an int year."""
    return {
        "project_id": row["project_id"],
        "title_en": row["title_en"],
        "title_khmer": row.get("title_khmer") or None,
        "year": int(row["year"]),
        "term": row.get("term") or None,
        "program": row["program"].upper(),
        "domain": row.get("domain") or None,
        "tech_stack": split_list(row.get("tech_stack")),
        "keywords": split_list(row.get("keywords")),
        "supervisor_id": row.get("supervisor_id") or None,
        "senior_ids": split_list(row.get("senior_ids")),
        "abstract": row["abstract"],
        "repo_url": row.get("repo_url") or None,
        "demo_url": row.get("demo_url") or None,
        "permission": row["permission"].lower(),
    }


def clean_senior(row: dict) -> dict:
    return {
        "senior_id": row["senior_id"],
        "full_name_latin": row["full_name_latin"],
        "full_name_khmer": row.get("full_name_khmer") or None,
        "aliases": split_list(row.get("aliases")),
        "program": row["program"].upper(),
        "cohort_year": int(row["cohort_year"]),
        "supervisor_id": row.get("supervisor_id") or None,
        "contact_email": row.get("contact_email") or None,
        "github": row.get("github") or None,
        "linkedin": row.get("linkedin") or None,
        "website": row.get("website") or None,
        "consent_public_contact": parse_bool(row.get("consent_public_contact")),
        "consent_relay": parse_bool(row.get("consent_relay")),
        "status": (row.get("status") or "active").lower(),
    }


def read_csv(path: Path, required: list[str], report: ValidationReport) -> list[tuple[int, dict]]:
    """Read a CSV file. Returns (line_number, row) pairs with stripped values."""
    if not path.is_file():
        report.errors.append(f"{path.name}: file not found ({path})")
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = [name.strip() for name in reader.fieldnames or []]
        reader.fieldnames = header
        missing = [name for name in required if name not in header]
        if missing:
            report.errors.append(f"{path.name}: missing column(s): {', '.join(missing)}")
            return []

        result = []
        line = 1  # the header is line 1
        for row in reader:
            start_line = line + 1
            line = reader.line_num  # an abstract may span several lines
            values = {k: (v or "").strip() for k, v in row.items() if k}
            if any(values.values()):  # skip blank lines
                result.append((start_line, values))
        return result


# ---------------------------------------------------------------- checks


def check_seniors(path: Path, report: ValidationReport) -> dict[str, dict]:
    """Return {senior_id: clean senior} for every valid senior row."""
    seniors = {}
    for line, row in read_csv(path, REQUIRED_SENIOR, report):
        where = f"{path.name} line {line}"
        problems = [f"{where}: missing required field '{c}'" for c in REQUIRED_SENIOR if not row[c]]
        senior_id = row["senior_id"]
        if senior_id in seniors:
            problems.append(f"{where}: duplicate senior_id '{senior_id}'")
        if row["program"] and row["program"].upper() not in PROGRAMS:
            problems.append(f"{where}: program '{row['program']}' must be one of {PROGRAMS}")
        if row["cohort_year"] and not YEAR_RE.match(row["cohort_year"]):
            problems.append(f"{where}: cohort_year '{row['cohort_year']}' must be 20xx")
        if (row.get("status") or "active").lower() not in ("active", "withdrawn"):
            problems.append(f"{where}: status must be active or withdrawn")
        for flag in ("consent_public_contact", "consent_relay"):
            try:
                parse_bool(row.get(flag))
            except ValueError:
                problems.append(f"{where}: {flag} must be true or false")

        if problems:
            report.errors.extend(problems)
            continue
        senior = clean_senior(row)
        seniors[senior_id] = senior
        if senior["status"] == "withdrawn":
            report.warnings.append(f"{where}: senior '{senior_id}' is withdrawn -> SKIPPED")
    return seniors


def check_projects(path: Path, seniors: dict, corpus_dir: Path, report: ValidationReport):
    """Return (eligible projects, skipped project ids, every project id seen)."""
    eligible, skipped, seen = [], [], set()
    for line, row in read_csv(path, REQUIRED_PROJECT, report):
        where = f"{path.name} line {line}"
        problems = [
            f"{where}: missing required field '{c}'" for c in REQUIRED_PROJECT if not row[c]
        ]
        project_id = row["project_id"]
        if project_id and project_id in seen:
            problems.append(f"{where}: duplicate project_id '{project_id}'")
        seen.add(project_id)
        if row["year"] and not YEAR_RE.match(row["year"]):
            problems.append(f"{where}: year '{row['year']}' must be a 20xx cohort year")
        if row["program"] and row["program"].upper() not in PROGRAMS:
            problems.append(f"{where}: program '{row['program']}' must be one of {PROGRAMS}")

        withdrawn = []
        for senior_id in split_list(row["senior_ids"]):
            if senior_id not in seniors:
                problems.append(f"{where}: unknown senior_id '{senior_id}'")
            elif seniors[senior_id]["status"] == "withdrawn":
                withdrawn.append(senior_id)

        if problems:
            report.errors.extend(problems)
            continue
        if row["permission"].lower() != "granted":
            report.warnings.append(f"{where}: '{project_id}' permission is not granted -> SKIPPED")
            skipped.append(project_id)
            continue
        if withdrawn:
            names = ", ".join(withdrawn)
            report.warnings.append(
                f"{where}: '{project_id}' has withdrawn author {names} -> SKIPPED"
            )
            skipped.append(project_id)
            continue
        if not (corpus_dir / project_id).is_dir():
            report.errors.append(f"{where}: corpus folder missing for '{project_id}'")
            continue

        others = [p.name for p in project_files(corpus_dir, project_id)]
        others = [name for name in others if Path(name).suffix.lower() not in DOCUMENT_EXTENSIONS]
        if others:
            report.warnings.append(f"{where}: '{project_id}' ignores unsupported files: {others}")
        if not project_documents(corpus_dir, project_id):
            report.warnings.append(
                f"{where}: '{project_id}' has no documents -> metadata-only project"
            )
        eligible.append(clean_project(row))
    return eligible, skipped, seen


def check_orphans(corpus_dir: Path, project_ids: set[str], report: ValidationReport):
    """Warn about corpus folders/files that no manifest row owns."""
    if not corpus_dir.is_dir():
        return
    for entry in sorted(corpus_dir.iterdir()):
        if entry.name.startswith(".") or entry.name.lower().startswith("readme"):
            continue
        if entry.is_dir() and entry.name not in project_ids:
            report.warnings.append(f"corpus: orphan folder '{entry.name}' has no manifest row")
        elif entry.is_file():
            report.warnings.append(f"corpus: orphan file '{entry.name}' is not in a project folder")


def validate_manifest(projects_csv, seniors_csv, corpus_dir, previous=None) -> ValidationReport:
    """Check the manifest and corpus. `previous` = (projects, seniors) from the last run."""
    projects_csv, seniors_csv, corpus_dir = Path(projects_csv), Path(seniors_csv), Path(corpus_dir)
    report = ValidationReport(corpus_hash=corpus_hash(corpus_dir))
    if not corpus_dir.is_dir():
        report.errors.append(f"corpus: folder not found ({corpus_dir})")

    seniors = check_seniors(seniors_csv, report)
    eligible, skipped, seen = check_projects(projects_csv, seniors, corpus_dir, report)
    check_orphans(corpus_dir, seen, report)

    report.seniors = list(seniors.values())
    report.projects = eligible if report.ok else []
    old_projects, old_seniors = previous or ([], [])
    report.diff = diff_manifests(old_projects, old_seniors, eligible, report.seniors)
    report.diff["projects"]["skipped"] = sorted(skipped)
    return report


# ---------------------------------------------------------------- diff

# The fields compared to decide whether a row was "updated" since the last run.
PROJECT_COMPARE = [
    "title_en",
    "title_khmer",
    "year",
    "term",
    "program",
    "domain",
    "tech_stack",
    "keywords",
    "supervisor_id",
    "senior_ids",
    "abstract",
    "repo_url",
    "demo_url",
    "permission",
]
SENIOR_COMPARE = [
    "full_name_latin",
    "full_name_khmer",
    "program",
    "cohort_year",
    "supervisor_id",
    "contact_email",
    "github",
    "linkedin",
    "consent_public_contact",
    "consent_relay",
    "status",
]


LIST_FIELDS = {"tech_stack", "keywords", "senior_ids"}
BOOL_FIELDS = {"consent_public_contact", "consent_relay"}


def comparable(field_name, value):
    """Turn a value into the same shape whether it came from the CSV or SQLite.

    "2024" == 2024, "a;b" == ["a", "b"], "" == None, 1 == "true".
    """
    if field_name in LIST_FIELDS:
        return tuple(sorted(split_list(value)))
    if field_name in BOOL_FIELDS:
        return parse_bool(value)
    text = str(value).strip() if value is not None else ""
    return text or None


def fingerprint(row: dict, fields: list[str]) -> tuple:
    return tuple(comparable(name, row.get(name)) for name in fields)


def senior_fingerprint(row: dict) -> tuple:
    # Aliases are compared in normalized form, the way SQLite stores them.
    names = [row.get("full_name_latin"), row.get("full_name_khmer")]
    names += split_list(row.get("aliases"))
    aliases = tuple(sorted({normalize(str(n)) for n in names if n}))
    return fingerprint(row, SENIOR_COMPARE) + (aliases,)


def changes(old_rows, new_rows, key, make_fingerprint) -> dict:
    old = {row[key]: make_fingerprint(row) for row in old_rows}
    new = {row[key]: make_fingerprint(row) for row in new_rows}
    return {
        "added": sorted(set(new) - set(old)),
        "updated": sorted(k for k in set(new) & set(old) if new[k] != old[k]),
        "removed": sorted(set(old) - set(new)),
    }


def diff_manifests(old_projects, old_seniors, new_projects, new_seniors) -> dict:
    """What was added, updated (any field changed), or removed since last time."""
    return {
        "projects": changes(
            old_projects, new_projects, "project_id", lambda r: fingerprint(r, PROJECT_COMPARE)
        ),
        "seniors": changes(old_seniors, new_seniors, "senior_id", senior_fingerprint),
    }


# ---------------------------------------------------------------- CLI


def print_report(report: ValidationReport):
    for message in report.errors:
        print("ERROR  ", message)
    for message in report.warnings:
        print("WARNING", message)
    skipped = report.diff.get("projects", {}).get("skipped", [])
    print(
        f"projects: {len(report.projects)} eligible, {len(skipped)} skipped | "
        f"seniors: {len(report.seniors)} | errors: {len(report.errors)} | "
        f"warnings: {len(report.warnings)}"
    )
    print("corpus_hash:", report.corpus_hash)
    print("OK" if report.ok else "FAILED")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate the manifest (no writes).")
    parser.add_argument("--projects", type=Path, default=ROOT / "data" / "projects.csv")
    parser.add_argument("--seniors", type=Path, default=ROOT / "data" / "seniors.csv")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    report = validate_manifest(args.projects, args.seniors, args.corpus)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
