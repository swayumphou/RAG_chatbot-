"""Tests for senior name resolution (T3.5)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("rapidfuzz")

from ingestion.validate_manifest import validate_manifest  # noqa: E402
from retrieval.entity import resolve_senior  # noqa: E402
from store.db import DB  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"
SOK_DARA = "s-2024-014"


@pytest.fixture
def db(tmp_path):
    """A database with the fixture seniors and projects."""
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


class TextIndexClient:
    """Fake Qdrant: scroll() returns the points of project dse2024-001 (two authors)."""

    def __init__(self):
        self.scrolled = []

    def scroll(self, collection_name, scroll_filter, limit, with_payload):
        self.scrolled.append(scroll_filter.must[0].match.text)
        payload = {
            "senior_ids": ["s-2024-014", "s-2024-021"],
            "author_names_normalized": "sok dara / chan sreyneang",
        }
        return [SimpleNamespace(payload=payload)], None


class DownClient:
    """Fake Qdrant that is not running."""

    def scroll(self, *args, **kwargs):
        raise ConnectionError("Qdrant is down")


def test_latin_name_resolves_by_alias(db):
    result = resolve_senior("Sok Dara", db)
    assert (result.status, result.senior_id, result.via) == ("resolved", SOK_DARA, "alias")
    assert result.confidence == 1.0


def test_khmer_name_resolves_by_alias(db):
    result = resolve_senior("សុខ ដារា", db)
    assert (result.status, result.senior_id, result.via) == ("resolved", SOK_DARA, "alias")


def test_three_spec_spellings_reach_the_same_senior(db):
    ids = {resolve_senior(name, db).senior_id for name in ("Sok Dara", "សុខ ដារា", "sokdaraa")}
    assert ids == {SOK_DARA}


def test_text_index_picks_the_author_whose_name_was_typed(db):
    # "dara" is not an alias. The text index finds a project with two authors;
    # only Sok Dara's own name contains the typed word.
    client = TextIndexClient()
    result = resolve_senior("dara", db, client)
    assert client.scrolled == ["dara"]
    assert (result.status, result.senior_id, result.via) == ("resolved", SOK_DARA, "text_index")
    assert result.confidence == 0.90


def test_typo_resolves_by_fuzzy_match(db):
    # "sok daraa" is not an alias and the text index does not match it.
    result = resolve_senior("sok daraa", db, DownClient())
    assert (result.status, result.senior_id, result.via) == ("resolved", SOK_DARA, "fuzzy")
    assert 0.88 <= result.confidence < 1.0


def test_two_seniors_with_the_same_name_never_merge(db):
    # Two active seniors are both called "Keo Sophea" (DSE and ITE).
    result = resolve_senior("Keo Sophea", db)
    assert result.status == "ambiguous"
    assert result.senior_id is None
    ids = {c["senior_id"] for c in result.candidates}
    assert ids == {"s-2024-031", "s-2024-044"}
    for c in result.candidates:
        assert {"full_name_latin", "program", "cohort_year", "score"} <= set(c)
        assert "contact_email" not in c and "github" not in c  # no contact data


def test_fuzzy_typo_of_a_shared_name_is_ambiguous(db):
    result = resolve_senior("keo sopheaa", db, DownClient())
    assert result.status == "ambiguous"
    assert {c["senior_id"] for c in result.candidates} == {"s-2024-031", "s-2024-044"}


def test_unknown_name_is_not_found_with_unconfirmed_candidates(db):
    result = resolve_senior("Sok Panha", db, DownClient())
    assert result.status == "not_found"
    assert result.senior_id is None
    assert 1 <= len(result.candidates) <= 3
    assert all(c["confirmed"] is False for c in result.candidates)


def test_withdrawn_senior_is_never_resolved(db):
    result = resolve_senior("Mao Rithy", db, DownClient())
    assert result.status == "not_found"
    assert "s-2022-077" not in {c["senior_id"] for c in result.candidates}
