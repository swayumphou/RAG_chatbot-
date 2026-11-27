"""Tests for normalization, Khmer segmentation, routing, and multi-intent (T3.1, T3.2, T3.7)."""

from pathlib import Path

import pytest

from ingestion.validate_manifest import validate_manifest
from retrieval.intent import (
    contains_khmer,
    decompose_multi_intent,
    normalize,
    route,
    segment_for_lexical,
)
from store.db import DB

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"


@pytest.fixture
def db(tmp_path):
    """A database with the fixture seniors and projects (no documents needed for routing)."""
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


# ---------------------------------------------------------------- T3.1


def test_normalize_keeps_khmer_and_strips_latin():
    # Accented Latin: accents removed, case folded, spaces collapsed.
    assert normalize("  Sók  Dara ") == "sok dara"
    # Khmer is kept exactly (its vowel signs are not "accents").
    assert normalize("សុខ ដារា") == "សុខ ដារា"
    # Mixed Khmer and Latin.
    assert normalize("សុខ ដារា (Sok DARA)") == "សុខ ដារា sok dara"
    # Khmer digits become ASCII digits.
    assert normalize("ឆ្នាំ ២០២៤") == "ឆ្នាំ 2024"
    # Punctuation becomes a space, also Khmer punctuation.
    assert normalize("Khmer OCR, again!") == "khmer ocr again"
    assert normalize("អត្ថបទ។") == "អត្ថបទ"
    # Possessive 's is removed.
    assert normalize("Sok Dara's") == "sok dara"
    assert normalize("") == ""


def test_contains_khmer():
    assert contains_khmer("សុខ")
    assert contains_khmer("Sok សុខ")
    assert not contains_khmer("Sok Dara")


def test_segment_for_lexical_leaves_english_unchanged():
    assert segment_for_lexical("khmer ocr") == "khmer ocr"


def test_khmer_segmentation_is_space_separated():
    pytest.importorskip("khmernltk")
    text = "ការចាត់ថ្នាក់ប្រធានបទព័ត៌មាន"  # written without spaces
    segmented = segment_for_lexical(text)
    assert " " in segmented
    assert segmented.replace(" ", "") == text  # no letter lost or added


# ---------------------------------------------------------------- T3.2


def test_one_query_per_intent(db):
    assert route("Sok Dara", db).intent == "SENIOR_LOOKUP"
    assert route("projects by Sok Dara", db).intent == "PORTFOLIO"
    assert route("who built a Khmer OCR system", db).intent == "ANSWER"
    assert route("DSE 2024", db).intent == "FACET_BROWSE"
    assert route("khmer ocr", db).intent == "TOPIC_SEARCH"


def test_portfolio_extracts_the_name(db):
    routed = route("projects by Sok Dara", db)
    assert routed.name_span == "sok dara"


def test_unknown_two_word_query_stays_topic_search(db):
    # Two words, but neither is a known surname: never guess a person.
    assert route("face recognition", db).intent == "TOPIC_SEARCH"
    assert route("library booking", db).intent == "TOPIC_SEARCH"


def test_surname_with_topic_word_stays_topic_search(db):
    # "Sok" is a surname, but "system" marks a topic.
    assert route("sok booking system", db).intent == "TOPIC_SEARCH"


def test_year_facet_query(db):
    routed = route("2024", db)
    assert routed.intent == "FACET_BROWSE"
    assert routed.years == (2024,)

    routed = route("DSE 2024", db)
    assert routed.years == (2024,)
    assert routed.programs == ("DSE",)


def test_all_programs_are_accepted(db):
    """T8.2: every RUPP program is a facet; the old DSE-only refusal is gone."""
    for program in ("DSE", "ITE", "TEE", "BE", "FTCE"):
        routed = route(f"{program} 2024", db)
        assert routed.intent == "FACET_BROWSE", program
        assert routed.programs == (program,)


def test_known_khmer_name_is_senior_lookup(db):
    routed = route("សុខ ដារា", db)
    assert routed.intent == "SENIOR_LOOKUP"
    assert routed.name_span == "សុខ ដារា"


def test_routed_query_keeps_raw_and_normalized(db):
    routed = route("  Khmer  OCR ", db)
    assert routed.raw == "  Khmer  OCR "
    assert routed.normalized == "khmer ocr"
    assert routed.lexical == "Khmer  OCR"


# ---------------------------------------------------------------- T3.7


def test_multi_intent_decomposition(db):
    name_query, topic_query = decompose_multi_intent("Sok Dara's OCR project", db)
    assert name_query.intent == "SENIOR_LOOKUP"
    assert name_query.name_span == "sok dara"
    assert topic_query.intent == "TOPIC_SEARCH"
    assert topic_query.topic_span == "ocr"

    # "<name> <topic> project" works too.
    name_query, topic_query = decompose_multi_intent("Sok Dara OCR project", db)
    assert (name_query.name_span, topic_query.topic_span) == ("sok dara", "ocr")


def test_multi_intent_needs_both_halves(db):
    assert decompose_multi_intent("khmer ocr", db) is None  # no name
    assert decompose_multi_intent("Sok Dara's projects", db) is None  # no topic
    assert decompose_multi_intent("Sok Dara", db) is None
