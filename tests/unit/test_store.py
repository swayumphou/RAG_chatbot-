"""Tests for the SQLite store (T1.4)."""

from pathlib import Path

import pytest

from ingestion.validate_manifest import validate_manifest
from store.db import DB

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"


@pytest.fixture
def db(tmp_path):
    """A database loaded with the fixture manifest (no documents)."""
    corpus = tmp_path / "corpus"
    for number in range(1, 7):
        (corpus / f"dse2024-{number:03d}").mkdir(parents=True)
    report = validate_manifest(MANIFEST / "projects.csv", MANIFEST / "seniors.csv", corpus)
    database = DB(tmp_path / "kb.sqlite3")
    with database.transaction():
        for senior in report.seniors:
            database.upsert_senior(senior)
        for project in report.projects:
            database.upsert_project(project)
            database.replace_authorship(project["project_id"], project["senior_ids"])
    yield database
    database.close()


def test_schema_creates_all_tables(tmp_path):
    database = DB(tmp_path / "empty.sqlite3")
    tables = {r["name"] for r in database.rows("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "senior",
        "senior_alias",
        "project",
        "authorship",
        "document",
        "query_log",
        "relay_message",
    }
    assert expected <= tables


def test_upsert_senior_twice_does_not_duplicate(db):
    senior = db.get_senior("s-2024-014")
    db.upsert_senior(senior | {"aliases": "Sok Dara;sokdaraa"})
    db.upsert_senior(senior | {"aliases": "Sok Dara;sokdaraa"})
    count = db.one("SELECT COUNT(*) AS n FROM senior WHERE senior_id = 's-2024-014'")
    assert count["n"] == 1
    assert db.get_senior("s-2024-014")["created_at"] == senior["created_at"]


def test_lookup_alias_latin_khmer_and_typo(db):
    assert db.lookup_alias("sokdaraa") == "s-2024-014"
    assert db.lookup_alias("Sok Dara") == "s-2024-014"
    assert db.lookup_alias("សុខ ដារា") == "s-2024-014"
    # Two seniors share "Keo Sophea": that is ambiguity, not a match.
    assert db.lookup_alias("keo sophea") is None
    assert db.alias_matches("keo sophea") == ["s-2024-031", "s-2024-044"]


def test_withdrawn_senior_is_kept_but_never_resolves(db):
    assert db.withdrawn_senior_ids() == {"s-2022-077"}
    assert db.senior_exists("s-2022-077")
    assert db.lookup_alias("mao rithy") is None
    assert "mao rithy" not in db.known_names()


def test_known_names_and_surnames(db):
    assert {"sok dara", "សុខ ដារា", "sokdaraa"} <= db.known_names()
    assert {"sok", "keo", "សុខ"} <= db.known_surnames()


def test_senior_card_has_no_contact_fields(db):
    card = db.senior_card("s-2024-014")
    assert card["full_name_latin"] == "Sok Dara"
    assert "github" not in card and "contact_email" not in card


def test_projects_for_senior_newest_first(db):
    db.upsert_project(
        {
            "project_id": "fixture-2023",
            "title_en": "An older fixture project",
            "year": 2023,
            "program": "DSE",
            "abstract": "Fixture abstract.",
            "permission": "granted",
        }
    )
    db.upsert_authorship("fixture-2023", "s-2024-014")
    projects = db.projects_for_senior("s-2024-014")
    years = [p["year"] for p in projects]
    assert years == sorted(years, reverse=True)
    assert projects[-1]["project_id"] == "fixture-2023"
    assert projects[0]["tech_stack"]  # decoded JSON list


def test_facet_counts_use_the_whole_corpus(db):
    counts = db.facet_counts({"year": 2024})
    assert counts["total"] == 6  # pending and withdrawn projects are not in SQLite
    assert counts["program=DSE"] == 4
    assert counts["program=ITE"] == 2
    assert counts["tech_stack=Python"] >= 1
    assert len(db.list_projects({"year": 2024})) == counts["total"]


def test_query_log_rating_and_click(db):
    query_id = db.log_query(
        raw_query="khmer ocr",
        normalized_query="khmer ocr",
        intent="TOPIC_SEARCH",
        retrieved_json=[{"project_id": "dse2024-001"}],
    )
    assert db.rate(query_id, 1)
    assert db.clicked_project(query_id, "dse2024-001")
    row = db.query_log_rows()[0]
    assert row["rating"] == 1 and row["clicked_project"] == "dse2024-001"
    with pytest.raises(ValueError):
        db.log_query(raw_query="x", tg_user_id=12345)  # raw Telegram ids are refused
    with pytest.raises(ValueError):
        db.rate(query_id, 5)
