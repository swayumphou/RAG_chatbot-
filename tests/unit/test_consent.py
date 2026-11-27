"""Tests for consent-gated contact details (T4.6)."""

from pathlib import Path

import pytest

from answer.consent import CONSENT_DENIED, can_relay, contact_card
from ingestion.validate_manifest import validate_manifest
from store.db import DB

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"

# Synthetic contact data for one fixture senior (.invalid is a reserved test domain).
EMAIL = "sok.dara@example.invalid"
GITHUB = "https://example.invalid/fixture/sok-dara-github"


def senior(**fields):
    row = {
        "senior_id": "s-2024-014",
        "full_name_latin": "Sok Dara",
        "contact_email": EMAIL,
        "github": GITHUB,
        "linkedin": "https://example.invalid/fixture/sok-dara-linkedin",
        "website": "https://example.invalid/fixture/sok-dara-site",
        "phone": "not-a-real-number",  # must never be returned
        "telegram_user_hash": None,
        "consent_public_contact": 0,
        "consent_relay": 0,
    }
    row.update(fields)
    return row


def test_consent_false_gives_no_contact_block():
    for value in (0, "0", "false", None, ""):
        assert contact_card(senior(consent_public_contact=value)) is None
    assert contact_card(None) is None
    # The fixed message the bot shows instead (INTERFACES.md §7).
    assert CONSENT_DENIED == (
        "This senior has not opted in to public contact. "
        "Ask your department secretariat to forward a message."
    )


def test_consent_true_gives_only_professional_fields():
    card = contact_card(senior(consent_public_contact=1))
    assert card == {
        "contact_email": EMAIL,
        "github": GITHUB,
        "linkedin": "https://example.invalid/fixture/sok-dara-linkedin",
        "website": "https://example.invalid/fixture/sok-dara-site",
    }
    assert "phone" not in card  # phone numbers are never returned
    # Empty fields are left out.
    assert contact_card(senior(consent_public_contact=1, website=None, linkedin="")) == {
        "contact_email": EMAIL,
        "github": GITHUB,
    }


def test_relay_needs_consent_and_a_started_bot():
    assert can_relay(senior(consent_relay=1, telegram_user_hash="h1")) is True
    assert can_relay(senior(consent_relay=1, telegram_user_hash=None)) is False  # bot not started
    assert can_relay(senior(consent_relay=0, telegram_user_hash="h1")) is False  # no consent
    assert can_relay(None) is False


# ---------------------------------------------------------------- service


@pytest.fixture
def db(tmp_path):
    corpus = tmp_path / "corpus"
    for number in range(1, 7):
        (corpus / f"dse2024-{number:03d}").mkdir(parents=True)
    report = validate_manifest(MANIFEST / "projects.csv", MANIFEST / "seniors.csv", corpus)
    database = DB(tmp_path / "kb.sqlite3")
    with database.transaction():
        for row in report.seniors:
            if row["senior_id"] == "s-2024-014":
                row = row | {"contact_email": EMAIL}  # give the consenting senior an email
            database.upsert_senior(row)
        for project in report.projects:
            database.upsert_project(project)
            database.replace_authorship(project["project_id"], project["senior_ids"])
    yield database
    database.close()


def test_service_attaches_contact_only_to_consenting_portfolios(db):
    pytest.importorskip("qdrant_client")
    pytest.importorskip("rapidfuzz")
    from answer.service import answer

    consenting = answer("Sok Dara", object(), None, db)  # consent_public_contact = true
    assert consenting.mode == "portfolio"
    assert consenting.portfolio.senior["contact"]["contact_email"] == EMAIL
    assert consenting.citations == []  # contact data is never a citation

    private = answer("Chan Sreyneang", object(), None, db)  # consent_public_contact = false
    assert "contact" not in private.portfolio.senior


def test_llm_prompt_never_contains_contact_data(db, monkeypatch):
    pytest.importorskip("qdrant_client")
    pytest.importorskip("rapidfuzz")
    import answer.service as service
    from answer.prompts import build_messages
    from retrieval.hybrid import RetrievedProject

    retrieved = RetrievedProject(
        project_id="dse2024-001",
        title="Khmer Handwritten Text Recognition with Tesseract",
        senior_ids=("s-2024-014", "s-2024-021"),
        year=2024,
        program="DSE",
        supervisor_id="sup-003",
        page_number=1,
        snippet="We segment Khmer handwriting into lines.",
        score=0.03,
        dense_score=0.8,
        matched_by="both",
    )
    monkeypatch.setattr(service, "hybrid_retrieve", lambda *args, **kwargs: [retrieved])
    prompts = []

    def spy(query, citations):
        prompts.append(build_messages(query, citations))
        return "No indexed project covers this question."

    monkeypatch.setattr(service, "synthesize", spy)
    service.answer("who built the Sok Dara OCR system", object(), None, db, enable_llm=True)

    text = str(prompts)
    assert prompts, "the LLM path was not reached"
    assert EMAIL not in text and "@" not in text
    assert "github" not in text.lower() and "linkedin" not in text.lower()
