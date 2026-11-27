"""Tests for Telegram formatting: project cards and pagination (T5.3)."""

import pytest

pytest.importorskip("telegram")
pytest.importorskip("qdrant_client")

from answer.service import Citation  # noqa: E402
from bot.format import (  # noqa: E402
    CONTINUED,
    MAX_CALLBACK,
    MAX_MESSAGE,
    SEND_OPTIONS,
    esc,
    paginate,
    project_card,
)


def citation(**fields):
    values = {
        "project_id": "dse2024-001",
        "title": "Khmer Handwritten Text Recognition with Tesseract",
        "senior_ids": ("s-2024-014", "s-2024-021"),
        "page_number": 3,
        "snippet": "We segment Khmer handwriting into lines and characters.",
        "matched_by": "both",
        "score": 0.03,
        "authors": ("Sok Dara", "Chan Sreyneang"),
        "year": 2024,
        "program": "DSE",
        "supervisor_id": "sup-003",
        "repo_url": "https://example.invalid/fixture/repo",
    }
    values.update(fields)
    return Citation(**values)


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def labels(markup):
    return [button.text for button in buttons(markup)]


# ---------------------------------------------------------------- cards


def test_card_layout():
    text, _ = project_card(citation())
    lines = text.split("\n")
    assert lines[0] == "<b>Khmer Handwritten Text Recognition with Tesseract</b>"
    assert lines[1] == "Sok Dara, Chan Sreyneang · 2024 · DSE · Supervisor sup-003"
    assert "We segment Khmer handwriting into lines and characters." in text
    assert "— Khmer Handwritten Text Recognition with Tesseract, p.3" in text
    assert text.endswith("<i>matched: semantic + keyword</i>")
    # The other matched paths are named too.
    assert "matched: semantic<" in project_card(citation(matched_by="dense"))[0]
    assert "matched: keyword<" in project_card(citation(matched_by="lexical"))[0]


def test_html_injection_in_a_title_is_escaped():
    evil = citation(
        title="<script>alert(1)</script> & <b>bold</b>",
        snippet="Uses <img src=x> & raw tags",
        authors=("A <i>B</i>",),
    )
    text, _ = project_card(evil)
    assert "<script>" not in text and "<img" not in text and "<i>B" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in text
    assert esc("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_snippet_is_capped_at_400_characters():
    text, _ = project_card(citation(snippet="x" * 1000))
    passage = [line for line in text.split("\n") if line.startswith("x")][0]
    assert len(passage) == 400 and passage.endswith("…")


def test_buttons_and_callback_payload_size():
    one_author = citation(senior_ids=("s-2024-014",), authors=("Sok Dara",))
    _, markup = project_card(one_author, page=0, total=3)
    data = {b.text: b.callback_data for b in buttons(markup)}
    assert data["Abstract"] == "ab:dse2024-001"
    assert data["Portfolio"] == "pf:s-2024-014"
    assert data["Next ▶"] == "pg:dse2024-001:1"

    # Two authors: one portfolio button per author, named after them (T5.4).
    _, markup = project_card(citation())
    data = {b.text: b.callback_data for b in buttons(markup)}
    assert data["Sok Dara"] == "pf:s-2024-014"
    assert data["Chan Sreyneang"] == "pf:s-2024-021"
    for button in buttons(markup):
        if button.callback_data:
            assert len(button.callback_data.encode("utf-8")) <= MAX_CALLBACK

    # A project_id so long that the payload would pass 64 bytes: those buttons are left out.
    _, markup = project_card(citation(project_id="p" * 70), page=0, total=2)
    assert "Abstract" not in labels(markup) and "Next ▶" not in labels(markup)
    assert "Sok Dara" in labels(markup)


def test_next_button_only_when_there_is_a_next_page():
    assert "Next ▶" in labels(project_card(citation(), page=1, total=3)[1])
    assert "Next ▶" not in labels(project_card(citation(), page=2, total=3)[1])
    assert "Next ▶" not in labels(project_card(citation(), page=0, total=1)[1])


def test_missing_repo_url_omits_the_button():
    with_repo = project_card(citation())[1]
    repo = [b for b in buttons(with_repo) if b.text == "Repo"][0]
    assert repo.url == "https://example.invalid/fixture/repo"
    assert "Repo" not in labels(project_card(citation(repo_url=None))[1])
    no_authors = labels(project_card(citation(senior_ids=(), authors=()))[1])
    assert no_authors == ["Abstract", "Repo"]  # no portfolio button without an author


def test_send_options_use_html_and_no_link_preview():
    assert SEND_OPTIONS["parse_mode"] == "HTML"
    assert SEND_OPTIONS["link_preview_options"].is_disabled is True


# ---------------------------------------------------------------- pagination


def test_short_text_is_one_message():
    assert paginate("hello\n\nworld") == ["hello\n\nworld"]
    assert paginate("") == []


def test_every_message_is_within_the_4096_limit_and_splits_on_blank_lines():
    paragraphs = [f"Paragraph {i}. " + "word " * 150 for i in range(40)]  # ~30 000 chars
    pages = paginate("\n\n".join(paragraphs))
    assert len(pages) > 1
    assert all(len(page) <= MAX_MESSAGE for page in pages)
    # Splits happen between paragraphs: every paragraph is whole in exactly one page.
    for paragraph in paragraphs:
        assert sum(paragraph in page for page in pages) == 1


def test_one_huge_paragraph_is_split_with_a_marker_and_nothing_is_lost():
    words = [f"w{i}" for i in range(3000)]  # one paragraph of ~17 000 characters
    pages = paginate(" ".join(words))
    assert len(pages) > 1
    assert all(len(page) <= MAX_MESSAGE for page in pages)
    assert all(page.endswith(CONTINUED) for page in pages[:-1])
    rebuilt = " ".join(page.removesuffix(CONTINUED) for page in pages).split()
    assert rebuilt == words  # every word survives, in order


def test_a_cut_never_lands_inside_an_html_tag_or_entity():
    text = ("a" * 4085) + ' <a href="https://example.invalid/x">link</a> &amp; end'
    pages = paginate(text)
    assert all(len(page) <= MAX_MESSAGE for page in pages)
    for page in pages:
        assert page.count("<") == page.count(">")  # no half tag
        assert "&am" not in page.replace("&amp;", "")  # no half entity


# ---------------------------------------------------------------- portfolio (T5.4)


def make_portfolio(projects=2, metadata_only=False, contact=None):
    from retrieval.portfolio import Portfolio

    senior = {
        "senior_id": "s-2024-014",
        "full_name_latin": "Sok Dara",
        "full_name_khmer": "សុខ ដារា",
        "program": "DSE",
        "cohort_year": 2024,
        "supervisor_id": "sup-003",
    }
    if contact:
        senior["contact"] = contact
    rows = [
        {"project_id": f"p{i:03d}", "title_en": f"Fixture project number {i}", "year": 2024 - i % 3}
        for i in range(projects)
    ]
    return Portfolio(senior=senior, projects=rows, metadata_only=metadata_only)


def test_portfolio_paginates_and_labels_metadata_only():
    from answer.consent import CONSENT_DENIED
    from bot.format import portfolio_card, portfolio_page

    # Header, newest-first projects, and the consent line.
    text = portfolio_card(make_portfolio())
    assert text.startswith("<b>Sok Dara · សុខ ដារា</b>\nDSE · Cohort 2024 · Supervisor sup-003")
    assert "<b>Projects (2)</b>\n1. Fixture project number 0 (2024)" in text
    assert esc(CONSENT_DENIED) in text  # no contact consent
    assert "Metadata only" not in text
    assert "Metadata only" in portfolio_card(make_portfolio(metadata_only=True))

    # A portfolio longer than 4096 characters is split, never cut short.
    long = make_portfolio(projects=300)
    assert len(portfolio_card(long)) > MAX_MESSAGE
    pages = []
    page = 0
    while True:
        text, markup = portfolio_page(long, page)
        assert len(text) <= MAX_MESSAGE
        pages.append(text)
        next_data = [b.callback_data for b in buttons(markup) if b.text == "Next ▶"]
        if not next_data:
            break
        assert next_data == [f"pf:s-2024-014:{page + 1}"]
        page += 1
    assert len(pages) > 1
    joined = "\n".join(pages)
    for i in range(300):
        assert joined.count(f"Fixture project number {i} (") == 1  # every project, once


def test_portfolio_contact_respects_consent():
    from bot.format import portfolio_card

    contact = {
        "github": "https://example.invalid/fixture/sok-dara-github",
        "contact_email": "sok.dara@example.invalid",
    }
    text = portfolio_card(make_portfolio(contact=contact))
    assert '<a href="https://example.invalid/fixture/sok-dara-github">GitHub</a>' in text
    assert "Email: sok.dara@example.invalid" in text
    no_consent = portfolio_card(make_portfolio())
    assert "github" not in no_consent.lower() and "@" not in no_consent


def test_relay_button_only_when_the_senior_can_be_messaged():
    from bot.format import portfolio_page

    _, markup = portfolio_page(make_portfolio(), can_relay=True)
    assert [b.callback_data for b in buttons(markup)] == ["relay:s-2024-014"]
    _, markup = portfolio_page(make_portfolio(), can_relay=False)
    assert buttons(markup) == []


# ---------------------------------------------------------------- ambiguity keyboard (T5.5)


def test_candidate_keyboard_has_one_button_per_senior():
    from bot.format import candidate_keyboard

    dse = {"senior_id": "s-2024-031", "full_name_latin": "Keo Sophea", "program": "DSE"}
    ite = {"senior_id": "s-2024-044", "full_name_latin": "Keo Sophea", "program": "ITE"}
    candidates = [dse | {"cohort_year": 2024}, ite | {"cohort_year": 2024}]
    text, markup = candidate_keyboard("Keo Sophea", candidates, "3f9a1c2e")
    assert text == "I found 2 seniors named Keo Sophea. Which one?"
    assert [(b.text, b.callback_data) for b in buttons(markup)] == [
        ("Keo Sophea — DSE 2024", "pick:3f9a1c2e:0"),
        ("Keo Sophea — ITE 2024", "pick:3f9a1c2e:1"),
    ]
    assert all(len(b.callback_data.encode()) <= MAX_CALLBACK for b in buttons(markup))
