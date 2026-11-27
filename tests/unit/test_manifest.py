"""Tests for the manifest fixtures, validator, diff, and CLI (T1.1, T1.2, T1.5, T1.6)."""

import csv
import json
from pathlib import Path

from ingestion.validate_manifest import (
    corpus_hash,
    diff_manifests,
    main,
    validate_manifest,
)

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"
PROJECTS = MANIFEST / "projects.csv"
SENIORS = MANIFEST / "seniors.csv"
INVALID = MANIFEST / "invalid"

PROJECT_HEADER = (
    "project_id,title_en,title_khmer,year,term,program,domain,tech_stack,keywords,"
    "supervisor_id,senior_ids,abstract,repo_url,demo_url,permission"
)
SENIOR_HEADER = (
    "senior_id,full_name_latin,full_name_khmer,aliases,program,cohort_year,supervisor_id,"
    "contact_email,github,linkedin,consent_public_contact,consent_relay,status"
)


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def make_corpus(root: Path) -> Path:
    """Corpus folders for the fixture projects. 005 stays empty (metadata-only)."""
    corpus = root / "corpus"
    for number in range(1, 7):
        folder = corpus / f"dse2024-{number:03d}"
        folder.mkdir(parents=True)
        if number != 5:
            (folder / "report.md").write_text("# Title\n\nFixture text.\n", encoding="utf-8")
    return corpus


def test_senior_fixture_columns():
    assert SENIORS.read_text(encoding="utf-8").splitlines()[0] == SENIOR_HEADER
    rows = read_rows(SENIORS)
    assert len(rows) >= 6

    sok_dara = next(r for r in rows if r["senior_id"] == "s-2024-014")
    aliases = sok_dara["aliases"].split(";")
    assert {"Sok Dara", "សុខ ដារា", "sokdaraa"} <= set(aliases)
    assert all(alias.strip() for alias in aliases)

    names = [r["full_name_latin"] for r in rows]
    assert any(names.count(name) == 2 for name in names), "need two seniors with the same name"
    assert any(r["consent_public_contact"] == "true" for r in rows)
    assert any(r["consent_relay"] == "true" for r in rows)
    assert any(r["status"] == "withdrawn" for r in rows)
    for row in rows:
        assert row["program"] in {"DSE", "ITE", "TEE", "BE", "FTCE"}
        assert row["cohort_year"].startswith("20") and len(row["cohort_year"]) == 4
        assert not row["contact_email"], "fixtures must not contain email addresses"


def test_project_fixture_columns():
    assert PROJECTS.read_text(encoding="utf-8").splitlines()[0] == PROJECT_HEADER
    projects = read_rows(PROJECTS)
    senior_ids = {r["senior_id"] for r in read_rows(SENIORS)}
    assert len(projects) >= 5
    for project in projects:
        for senior_id in project["senior_ids"].split(";"):
            assert senior_id in senior_ids
    assert any(p["permission"] == "pending" for p in projects)
    assert any(p["program"] == "ITE" for p in projects)
    assert any(p["title_khmer"] and p["repo_url"] for p in projects)
    assert any(p["project_id"] == "dse2024-005" for p in projects)  # no documents


def test_valid_manifest_and_rejections(tmp_path):
    corpus = make_corpus(tmp_path)

    report = validate_manifest(PROJECTS, SENIORS, corpus)
    assert report.ok, report.errors
    eligible = {p["project_id"] for p in report.projects}
    assert "dse2024-007" not in eligible  # pending
    assert "dse2022-008" not in eligible  # withdrawn author
    assert "dse2024-005" in eligible  # metadata-only is allowed
    assert report.diff["projects"]["skipped"] == ["dse2022-008", "dse2024-007"]
    assert len(report.corpus_hash) == 64

    expected_errors = {
        "missing_project_id.csv": "missing required field 'project_id'",
        "duplicate_project_id.csv": "duplicate project_id",
        "unknown_senior.csv": "unknown senior_id",
        "missing_folder.csv": "corpus folder missing",
    }
    for name, text in expected_errors.items():
        bad = validate_manifest(INVALID / name, SENIORS, corpus)
        assert not bad.ok, name
        assert any(text in e and "line " in e for e in bad.errors), (name, bad.errors)
        assert bad.projects == []  # nothing is ingested when there is an error

    for name in ("pending_permission.csv", "withdrawn_senior.csv"):
        warned = validate_manifest(INVALID / name, SENIORS, corpus)
        assert warned.ok, warned.errors
        assert any("SKIPPED" in w for w in warned.warnings)
        assert [p["project_id"] for p in warned.projects] == ["dse2024-003"]


def test_metadata_only_project_is_a_warning(tmp_path):
    report = validate_manifest(PROJECTS, SENIORS, make_corpus(tmp_path))
    assert any("dse2024-005" in w and "metadata-only" in w for w in report.warnings)


def test_corpus_hash_is_stable_and_order_independent(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    for root, order in ((first, ["x", "y"]), (second, ["y", "x"])):
        for name in order:
            (root / name).mkdir(parents=True)
            (root / name / "report.md").write_text(f"text {name}", encoding="utf-8")
    assert corpus_hash(first) == corpus_hash(second)
    (first / "x" / "report.md").write_text("changed", encoding="utf-8")
    assert corpus_hash(first) != corpus_hash(second)


def test_diff_added_updated_removed():
    old = [
        {"project_id": "p1", "title_en": "A", "year": 2024, "senior_ids": ["s1"]},
        {"project_id": "p2", "title_en": "B", "year": 2024, "senior_ids": ["s1"]},
    ]
    new = [
        {"project_id": "p1", "title_en": "A changed", "year": "2024", "senior_ids": "s1"},
        {"project_id": "p3", "title_en": "C", "year": 2024, "senior_ids": ["s1"]},
    ]
    diff = diff_manifests(old, [], new, [])
    assert diff["projects"] == {"added": ["p3"], "updated": ["p1"], "removed": ["p2"]}

    same = diff_manifests(old, [], [dict(old[0], year="2024", senior_ids="s1")], [])
    assert same["projects"]["updated"] == []  # "2024" and 2024 are the same value


def test_report_json_has_no_contact_fields(tmp_path):
    report = validate_manifest(PROJECTS, SENIORS, make_corpus(tmp_path))
    text = json.dumps(report.to_dict(), ensure_ascii=False)
    # The fixture senior's GitHub/LinkedIn links must not appear. (A project's
    # repo_url is public project metadata, so it may appear.)
    assert "sok-dara-github" not in text
    assert "sok-dara-linkedin" not in text
    for senior in report.to_dict()["seniors"]:
        assert "github" not in senior and "contact_email" not in senior


def test_cli_exit_codes(tmp_path, capsys):
    corpus = make_corpus(tmp_path)
    args = ["--seniors", str(SENIORS), "--corpus", str(corpus)]
    assert main(["--projects", str(PROJECTS), *args]) == 0
    assert "OK" in capsys.readouterr().out

    assert main(["--projects", str(INVALID / "duplicate_project_id.csv"), *args]) == 1
    assert "FAILED" in capsys.readouterr().out

    assert main(["--projects", str(PROJECTS), *args, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["corpus_hash"]
