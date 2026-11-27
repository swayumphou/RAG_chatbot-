"""Turn answer results into safe Telegram HTML messages and buttons.

Rules (Architecture §10):
- Escape every piece of text with esc() BEFORE adding HTML tags. Project
  titles often contain < & _ * which would otherwise break the message.
- A Telegram message holds at most 4096 characters; paginate() splits longer
  text on blank lines and never drops text.
- Button callback data holds at most 64 bytes, so buttons carry short
  references (ab:<project_id>), never whole records.
"""

from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from answer.consent import CONSENT_DENIED
from retrieval.filters import decode_facets, encode_facets  # plain text helpers, no search
from store.db import as_list

logger = logging.getLogger(__name__)

MAX_MESSAGE = 4096  # characters per Telegram message
MAX_CALLBACK = 64  # bytes of callback_data per button
MAX_SNIPPET = 400  # characters of passage on a project card
CONTINUED = " …"  # added where one very long paragraph had to be cut

# Options for every send_message call: HTML parse mode and no link previews.
SEND_OPTIONS = {
    "parse_mode": "HTML",
    "link_preview_options": LinkPreviewOptions(is_disabled=True),
}

MATCHED = {"dense": "semantic", "lexical": "keyword", "both": "semantic + keyword"}
LOW_CONFIDENCE = "(low confidence)"
RATE_PROMPT = "Was this helpful?"
CONTACT_LABELS = {
    "github": "GitHub",
    "linkedin": "LinkedIn",
    "website": "Website",
    "contact_email": "Email",
}


def esc(text) -> str:
    """Escape text for Telegram HTML (< > & only; quotes are fine outside attributes)."""
    return html.escape(str(text if text is not None else ""), quote=False)


def callback_button(label, data):
    """A button with callback data, or None when the data is longer than 64 bytes."""
    if len(data.encode("utf-8")) > MAX_CALLBACK:
        logger.warning("button %r dropped: callback data is over %d bytes", label, MAX_CALLBACK)
        return None
    return InlineKeyboardButton(label, callback_data=data)


def keyboard(rows) -> InlineKeyboardMarkup:
    """Buttons from rows of buttons; missing (None) buttons and empty rows are left out."""
    rows = [[button for button in row if button is not None] for row in rows]
    return InlineKeyboardMarkup([row for row in rows if row])


def details_line(citation) -> str:
    """'Sok Dara, Chan Sreyneang · 2024 · DSE · Supervisor sup-003' (escaped)."""
    details = [", ".join(citation.authors), str(citation.year or ""), citation.program]
    if citation.supervisor_id:
        details.append(f"Supervisor {citation.supervisor_id}")
    return esc(" · ".join(part for part in details if part))


def portfolio_buttons(citation) -> list:
    """One author: a 'Portfolio' button. Several authors: one button per author (T5.4)."""
    if len(citation.senior_ids) == 1:
        return [callback_button("Portfolio", f"pf:{citation.senior_ids[0]}")]
    buttons = []
    for number, senior_id in enumerate(citation.senior_ids):
        name = citation.authors[number] if number < len(citation.authors) else senior_id
        buttons.append(callback_button(name, f"pf:{senior_id}"))
    return buttons


def project_card(citation, page=0, total=1) -> tuple[str, InlineKeyboardMarkup]:
    """One search result as (HTML text, buttons). `page` is this card's place in the results."""
    snippet = citation.snippet or ""
    if len(snippet) > MAX_SNIPPET:
        snippet = snippet[: MAX_SNIPPET - 1] + "…"  # the passage stays verbatim, only shortened
    matched = MATCHED.get(citation.matched_by, citation.matched_by)

    lines = [
        f"<b>{esc(citation.title)}</b>",
        details_line(citation),
        "",
        esc(snippet),
        f"— {esc(citation.title)}, p.{citation.page_number}",
        f"<i>matched: {esc(matched)}</i>",
    ]
    text = "\n".join(lines)

    # Buttons appear only when their data exists.
    first_row = [callback_button("Abstract", f"ab:{citation.project_id}")]
    if citation.repo_url:
        first_row.append(InlineKeyboardButton("Repo", url=citation.repo_url))
    rows = [first_row, portfolio_buttons(citation)]
    if page < total - 1:
        rows.append([callback_button("Next ▶", f"pg:{citation.project_id}:{page + 1}")])
    return text, keyboard(rows)


def abstract_card(citation) -> tuple[str, InlineKeyboardMarkup]:
    """One project with its full manifest abstract (Abstract button, proj_<id> deep link)."""
    lines = [
        f"<b>{esc(citation.title)}</b>",
        details_line(citation),
        "",
        "<b>Abstract</b>",
        esc(citation.snippet),
    ]
    rows = [portfolio_buttons(citation)]
    if citation.repo_url:
        rows.append([InlineKeyboardButton("Repo", url=citation.repo_url)])
    return "\n".join(lines), keyboard(rows)


# ---------------------------------------------------------------- portfolio (T5.4)


def contact_link(field, value) -> str:
    """A contact field as HTML: web addresses become links, an email stays text."""
    label = CONTACT_LABELS.get(field, field)
    if str(value).startswith(("https://", "http://")):
        return f'<a href="{html.escape(value, quote=True)}">{esc(label)}</a>'
    return f"{esc(label)}: {esc(value)}"


def portfolio_card(portfolio) -> str:
    """Everything about one senior as HTML text. May be longer than one message."""
    senior = portfolio.senior
    name = esc(senior["full_name_latin"])
    if senior.get("full_name_khmer"):
        name += " · " + esc(senior["full_name_khmer"])
    about = f"{senior['program']} · Cohort {senior['cohort_year']}"
    if senior.get("supervisor_id"):
        about += f" · Supervisor {senior['supervisor_id']}"

    lines = [f"<b>{name}</b>", esc(about)]
    if portfolio.metadata_only:
        lines += ["", "<i>Metadata only: no documents of this senior are indexed yet.</i>"]
    lines += ["", f"<b>Projects ({len(portfolio.projects)})</b>"]
    for number, project in enumerate(portfolio.projects, start=1):  # newest first
        lines.append(f"{number}. {esc(project['title_en'])} ({project['year']})")

    # Contact details only when the senior consented (the service already removed them otherwise).
    lines.append("")
    contact = senior.get("contact")
    if contact:
        lines.append("Connect: " + " · ".join(contact_link(k, v) for k, v in contact.items()))
    else:
        lines.append(esc(CONSENT_DENIED))
    return "\n".join(lines)


def portfolio_page(portfolio, page=0, can_relay=False) -> tuple[str, InlineKeyboardMarkup]:
    """One message of a portfolio, with 'Next ▶' when there is more and the relay button."""
    pages = paginate(portfolio_card(portfolio))
    page = max(0, min(page, len(pages) - 1))
    senior_id = portfolio.senior["senior_id"]
    rows = []
    if page < len(pages) - 1:
        rows.append([callback_button("Next ▶", f"pf:{senior_id}:{page + 1}")])
    if can_relay:  # only with relay consent and a senior who has started the bot (T5.8)
        rows.append([callback_button("Send a message", f"relay:{senior_id}")])
    return pages[page], keyboard(rows)


# ---------------------------------------------------------------- other replies


def candidate_keyboard(name, candidates, key) -> tuple[str, InlineKeyboardMarkup]:
    """'I found 2 seniors named X. Which one?' with one button per senior (T5.5).

    The buttons carry only `pick:<key>:<index>`; the list itself waits in bot/state.py.
    """
    text = f"I found {len(candidates)} seniors named {esc(name)}. Which one?"
    rows = []
    for index, c in enumerate(candidates):
        label = f"{c.get('full_name_latin')} — {c.get('program')} {c.get('cohort_year')}"
        rows.append([callback_button(label, f"pick:{key}:{index}")])
    return text, keyboard(rows)


def not_found_text(result) -> str:
    lines = [esc(result.answer)]
    candidates = result.resolution.candidates if result.resolution else ()
    if candidates:
        lines += ["", "Closest names (not confirmed):"]
        for c in candidates:
            lines.append(f"• {esc(c.get('full_name_latin'))} — {esc(c.get('program'))}")
    return "\n".join(lines)


def abstain_text(result) -> str:
    lines = [esc(result.answer)]
    if result.weak_matches:
        lines.append("")
        for p in result.weak_matches:
            lines.append(f"• {esc(p.title)} ({p.year}) {LOW_CONFIDENCE}")
    return "\n".join(lines)


def rating_keyboard(query_id) -> InlineKeyboardMarkup:
    thumbs_up = callback_button("👍", f"rate:{query_id}:1")
    thumbs_down = callback_button("👎", f"rate:{query_id}:-1")
    return keyboard([[thumbs_up, thumbs_down]])


def relay_reply_keyboard(relay_id) -> InlineKeyboardMarkup:
    return keyboard([[callback_button("Reply", f"rr:{relay_id}")]])


# ---------------------------------------------------------------- facet chips (T5.6)


def facet_header(facets, counts) -> tuple[str, InlineKeyboardMarkup]:
    """'2024 · 6 projects' plus chips like 'DSE 4', 'ITE 2', and 'Clear all'.

    The numbers come from SQLite GROUP BY counts, so they describe the whole
    corpus, not only the cards shown. A chip carries `fc:<encoded facets>`.
    """
    values = [str(v) for value in facets.values() for v in as_list(value)]
    label = " · ".join(values) or "All projects"
    text = f"<b>{esc(label)}</b> · {counts.get('total', 0)} projects"

    active = set(as_list(facets.get("program")))
    chips = []
    for key, count in sorted(counts.items()):
        if not key.startswith("program="):
            continue
        program = key.split("=", 1)[1]
        if program in active:  # an active filter can be removed
            new = {k: v for k, v in facets.items() if k != "program"}
            chips.append(callback_button(f"✕ {program}", "fc:" + encode_facets(new)))
        else:
            new = {**facets, "program": program}
            chips.append(callback_button(f"{program} {count}", "fc:" + encode_facets(new)))
    rows = [chips]
    if facets:
        rows.append([callback_button("Clear all", "fc:")])
    return text, keyboard(rows)


def read_chip(data) -> dict:
    """The facets inside a chip's callback data (after 'fc:'); '' means Clear all."""
    return decode_facets(data)


def paginate(text, limit=MAX_MESSAGE) -> list[str]:
    """Split text into messages of at most `limit` characters, on blank lines.

    Paragraphs are packed together while they fit. A single paragraph longer
    than the limit is cut (at a space, never inside an HTML tag) and every
    cut piece ends with CONTINUED, so nothing is dropped silently.
    """
    if not text or not text.strip():
        return []

    pages = []
    current = ""
    for paragraph in text.split("\n\n"):
        for piece in split_long_paragraph(paragraph, limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                pages.append(current)
                current = piece
    if current:
        pages.append(current)
    return pages


def split_long_paragraph(paragraph, limit) -> list[str]:
    """Return the paragraph as it is, or cut it into pieces that fit in one message."""
    if len(paragraph) <= limit:
        return [paragraph]

    pieces = []
    rest = paragraph
    room = limit - len(CONTINUED)
    while len(rest) > limit:
        cut = safe_cut(rest, room)
        pieces.append(rest[:cut].rstrip() + CONTINUED)
        rest = rest[cut:].lstrip()
    pieces.append(rest)
    return pieces


def safe_cut(text, room) -> int:
    """The position to cut at: the last line break (or space) before `room`, never inside a tag."""
    cut = text.rfind("\n", 0, room)
    if cut < room // 2:  # no line break in the second half: cut at a space instead
        cut = text.rfind(" ", 0, room)
    if cut <= 0:
        cut = room  # one enormous word: cut it at the limit
    tag_start = text.rfind("<", 0, cut)
    if tag_start > text.rfind(">", 0, cut) and tag_start > 0:
        cut = tag_start  # the cut would land inside <...>: cut before the tag instead
    amp = text.rfind("&", 0, cut)
    if amp > text.rfind(";", 0, cut) and cut - amp < 10 and amp > 0:
        cut = amp  # never split an entity such as &amp;
    return cut
