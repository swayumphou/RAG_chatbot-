"""Tests for portfolio assembly from SQLite (T3.6)."""

import inspect
from pathlib import Path

import pytest

from ingestion.validate_manifest import validate_manifest
from retrieval.portfolio import portfolio
from store.db import DB

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"
SOK_DARA = "s-2024-014"  # consent_public_contact = true, two projects
CHAN_SREYNEANG = "s-2024-021"  # consent_public_contact = false


def load_db(tmp_path, with_projects=True):
    corpus = tmp_path / "corpus"
    for number in range(1, 7):
        (corpus / f"dse2024-{number:03d}").mkdir(parents=True)
    report = validate_manifest(MANIFEST / "projects.csv", MANIFEST / "seniors.csv", corpus)
    database = DB(tmp_path / "kb.sqlite3")
    with database.transaction():
        for senior in report.seniors:
            database.upsert_senior(senior)
        if with_projects:
            for project in report.projects:
                database.upsert_project(project)
                database.replace_authorship(project["project_id"], project["senior_ids"])
    return database


def add_parsed_document(db, project_id):
    db.replace_documents(
        project_id,
        [
            {
                "doc_id": f"{project_id}__report.pdf",
                "project_id": project_id,
                "doc_type": "report",
                "file_path": f"{project_id}/report.pdf",
                "sha256": "0" * 64,
                "page_count": 4,
                "parser": "pymupdf",
                "parse_status": "ok",
                "chunk_count": 5,
            }
        ],
    )


@pytest.fixture
def db(tmp_path):
    database = load_db(tmp_path)
    yield database
    database.close()


def test_two_projects_newest_first(db):
    result = portfolio(db, SOK_DARA)
    assert result.senior["full_name_latin"] == "Sok Dara"
    assert result.senior["full_name_khmer"] == "សុខ ដារា"
    assert result.senior["program"] == "DSE"
    assert result.senior["cohort_year"] == 2024
    assert result.senior["supervisor_id"] == "sup-003"

    ids = [p["project_id"] for p in result.projects]
    assert ids == ["dse2024-004", "dse2024-001"]  # same year -> project_id DESC
    order = [(p["year"], p["project_id"]) for p in result.projects]
    assert order == sorted(order, reverse=True)


def test_zero_projects_is_still_a_valid_portfolio(tmp_path):
    database = load_db(tmp_path, with_projects=False)
    try:
        result = portfolio(database, SOK_DARA)
        assert result.projects == []
        assert result.metadata_only is True
        assert result.senior["full_name_latin"] == "Sok Dara"
    finally:
        database.close()


def test_metadata_only_is_true_only_without_documents(db):
    assert portfolio(db, SOK_DARA).metadata_only is True
    add_parsed_document(db, "dse2024-001")
    assert portfolio(db, SOK_DARA).metadata_only is False


def test_contact_fields_only_with_consent(db):
    with_consent = portfolio(db, SOK_DARA).senior
    assert set(with_consent["contact"]) == {"github", "linkedin"}

    without_consent = portfolio(db, CHAN_SREYNEANG).senior
    assert "contact" not in without_consent
    for field in ("contact_email", "github", "linkedin", "website"):
        assert field not in without_consent


def test_portfolio_never_uses_qdrant(db, monkeypatch):
    # portfolio() takes no Qdrant client, so it works when Qdrant is down.
    assert list(inspect.signature(portfolio).parameters) == ["db", "senior_id"]

    # And it never creates one: any Qdrant client would raise here.
    qdrant_client = pytest.importorskip("qdrant_client")

    def explode(*args, **kwargs):
        raise AssertionError("portfolio() must not use Qdrant")

    monkeypatch.setattr(qdrant_client.QdrantClient, "__init__", explode)
    assert portfolio(db, SOK_DARA).projects


def test_withdrawn_or_unknown_senior_has_no_portfolio(db):
    with pytest.raises(LookupError):
        portfolio(db, "s-2022-077")
    with pytest.raises(LookupError):
        portfolio(db, "s-0000-000")
