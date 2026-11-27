"""Tests for Qdrant facet filters, SQLite facet counts, and facet links (T3.4)."""

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from qdrant_client import models  # noqa: E402

from ingestion.validate_manifest import validate_manifest  # noqa: E402
from retrieval.filters import (  # noqa: E402
    build_facet_filter,
    decode_facets,
    encode_facets,
    facet_counts,
)
from store.db import DB  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"


@pytest.fixture
def db(tmp_path):
    """A database with the fixture manifest loaded."""
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


def conditions_by_key(flt):
    return {condition.key: condition.match for condition in flt.must}


def test_year_filter_appears_in_the_qdrant_filter():
    flt = build_facet_filter({"year": 2024})
    match = conditions_by_key(flt)["year"]
    assert isinstance(match, models.MatchValue)
    assert match.value == 2024


def test_tech_stack_uses_match_any():
    flt = build_facet_filter({"tech_stack": ["Python", "Tesseract"]})
    match = conditions_by_key(flt)["tech_stack"]
    assert isinstance(match, models.MatchAny)
    assert match.any == ["Python", "Tesseract"]


def test_several_facets_are_all_must_conditions():
    flt = build_facet_filter({"year": "2024", "program": "DSE", "supervisor_id": "sup-003"})
    matches = conditions_by_key(flt)
    assert set(matches) == {"year", "program", "supervisor_id"}
    assert matches["year"].value == 2024  # text "2024" becomes the integer 2024
    assert matches["program"].value == "DSE"


def test_no_facets_means_no_filter():
    assert build_facet_filter({}) is None
    assert build_facet_filter({"year": [], "program": None}) is None


def test_unknown_facet_is_rejected():
    with pytest.raises(ValueError, match="colour"):
        build_facet_filter({"colour": "red"})


def test_counts_describe_the_corpus_not_the_page(db):
    counts = facet_counts(db, {"year": 2024})
    # Six granted 2024 projects are in SQLite (the pending one is not counted),
    # no matter how many results one page of search shows.
    assert counts["total"] == 6
    assert counts["program=DSE"] + counts["program=ITE"] == counts["total"]

    dse = facet_counts(db, {"year": 2024, "program": "DSE"})
    assert dse["total"] == counts["program=DSE"]


def test_counts_are_computed_with_group_by(db):
    statements = []
    db.conn.set_trace_callback(statements.append)
    facet_counts(db, {"year": 2024})
    db.conn.set_trace_callback(None)
    assert any("GROUP BY" in sql for sql in statements)


def test_deep_link_string_round_trips():
    facets = {"year": 2024, "program": "DSE"}
    text = encode_facets(facets)
    assert text == "year=2024&program=DSE"
    assert decode_facets(text) == facets

    tricky = {"tech_stack": ["scikit-learn", "C&C++"], "domain": "Computer Vision"}
    text = encode_facets(tricky)
    assert " " not in text and "C&C" not in text  # values are URL-safe
    assert decode_facets(text) == tricky


def test_clear_all_is_an_empty_dict():
    assert encode_facets({}) == ""
    assert decode_facets("") == {}
    assert build_facet_filter(decode_facets("")) is None
