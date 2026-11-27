"""Tests for the citation validator and the extractive fallback (T4.5)."""

from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("rapidfuzz")

import answer.service as service  # noqa: E402
from answer.service import Citation, answer  # noqa: E402
from answer.validate import validate_citations  # noqa: E402
from ingestion.validate_manifest import validate_manifest  # noqa: E402
from retrieval.hybrid import RetrievedProject  # noqa: E402
from store.db import DB  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"
TITLE = "Khmer Handwritten Text Recognition with Tesseract"


def citation(title=TITLE, page=4, project_id="dse2024-001"):
    return Citation(project_id, title, ("s-2024-014",), page, "passage", "both", 0.03)


RETRIEVED = [citation(), citation("Khmer News Topic Classification", 0, "dse2024-004")]


def test_valid_citation_passes():
    assert validate_citations(f"They used Tesseract [Project: {TITLE}, p.4].", RETRIEVED)
    # Several citations, all retrieved, also pass.
    text = f"A [Project: {TITLE}, p.4]. B [Project: Khmer News Topic Classification, p.0]."
    assert validate_citations(text, RETRIEVED)


def test_no_citation_is_invalid():
    assert not validate_citations("They used Tesseract.", RETRIEVED)
    assert not validate_citations("", RETRIEVED)


def test_wrong_page_fails():
    assert not validate_citations(f"[Project: {TITLE}, p.5]", RETRIEVED)


def test_wrong_title_fails():
    assert not validate_citations("[Project: Khmer Handwriting OCR, p.4]", RETRIEVED)


def test_citation_to_a_project_that_was_not_retrieved_fails():
    text = f"[Project: {TITLE}, p.4] and [Project: Campus Library Booking System, p.1]"
    assert not validate_citations(text, RETRIEVED)  # one invented citation spoils the answer


# ---------------------------------------------------------------- service fallback


@pytest.fixture
def db(tmp_path):
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


def test_bad_citation_returns_extractive_fallback_and_logs(db, monkeypatch):
    retrieved = RetrievedProject(
        project_id="dse2024-001",
        title=TITLE,
        senior_ids=("s-2024-014",),
        year=2024,
        program="DSE",
        supervisor_id="sup-003",
        page_number=4,
        snippet="passage",
        score=0.03,
        dense_score=0.8,
        matched_by="both",
    )
    monkeypatch.setattr(service, "hybrid_retrieve", lambda *args, **kwargs: [retrieved])
    # Gemma "answers" with a page that was never retrieved.
    monkeypatch.setattr(service, "synthesize", lambda q, c: f"Done [Project: {TITLE}, p.9]")
    events = []
    monkeypatch.setattr(service, "log_answer_validation", lambda event, **kw: events.append(event))

    result = answer("who built a Khmer OCR system", object(), None, db, enable_llm=True)

    assert result.mode == "extractive_fallback"
    assert result.answer is None  # the invented text is never shown
    assert [(c.title, c.page_number) for c in result.citations] == [(TITLE, 4)]
    assert events == ["citation_rejected"]
