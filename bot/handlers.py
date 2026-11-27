"""Telegram handlers: each update becomes ONE answer() call, then a formatted reply.

The bot is a thin adapter (CLAUDE.md rule 9). Handlers never search, rank,
apply thresholds, or build prompts; answer.service.answer() does all of that.

Every handler follows the same steps:
  1. private chats only (edited messages are filtered out at registration);
  2. remember the (hashed) user, for the relay;
  3. "typing..." and ONE answer() call, unless the same query was just answered;
  4. format the result (bot/format.py) and send it (bot/sender.py).

Shared objects live in context.bot_data (see bot/main.py):
  client, embedder, db, threshold, enable_llm  -> the answer() dependencies
  salt     -> for hashing Telegram user IDs
  throttle -> ChatThrottle,  recent -> RecentReplies,  chats -> {user hash: chat id}
"""

from __future__ import annotations

import asyncio
import logging
import re

from telegram.constants import ChatAction, ChatType
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

from answer.consent import CONSENT_DENIED, can_relay
from answer.service import answer, log_result
from bot import relay, state
from bot.format import (
    RATE_PROMPT,
    SEND_OPTIONS,
    abstain_text,
    abstract_card,
    candidate_keyboard,
    esc,
    facet_header,
    not_found_text,
    paginate,
    portfolio_page,
    project_card,
    rating_keyboard,
    read_chip,
)

logger = logging.getLogger(__name__)

HELP = (
    "<b>RUPP Senior Project Knowledge Base</b>\n"
    "Search past senior projects and the seniors who built them.\n\n"
    "/search &lt;topic&gt; — find projects (or just type a topic)\n"
    "/senior &lt;name&gt; — open a senior's portfolio\n"
    "/year &lt;yyyy&gt; — browse projects of one year\n"
    "/about — what this bot is\n"
    "/help — this message"
)
ABOUT = (
    "This bot searches permission-cleared RUPP senior projects. Answers quote the "
    "project documents with page citations. Contact details appear only when a "
    "senior agreed to share them."
)
SEARCH_USAGE = "Usage: /search &lt;topic&gt;, for example /search khmer ocr"
SENIOR_USAGE = "Usage: /senior &lt;name&gt;, for example /senior Sok Dara"
YEAR_USAGE = "Usage: /year &lt;yyyy&gt;, for example /year 2024"
EXPIRED = "This button has expired. Please send your search again."
RELAY_ASK = "Type your message for {name}. It is sent without your Telegram name or ID."
RELAY_SENT = "Your message was delivered."
RELAY_QUEUED = "Your message is saved. The senior will see it the next time they open the bot."
RELAY_REFUSED = "Your message could not be sent (no consent, or too many messages this hour)."
REPLY_ASK = "Type your reply. The student will not see your Telegram name or ID."
REPLY_SENT = "Your reply was sent."
REPLY_FAILED = "The student is not reachable right now. Please try again later."

# New messages in private chats only: groups and edited messages are ignored.
PRIVATE_NEW = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE


def register(app):
    """Attach every handler to the application (called by bot/main.py)."""
    commands = {
        "start": on_start,
        "help": on_help,
        "about": on_about,
        "search": on_search,
        "senior": on_senior,
        "year": on_year,
    }
    for name, callback in commands.items():
        app.add_handler(CommandHandler(name, callback, filters=PRIVATE_NEW))
    app.add_handler(MessageHandler(PRIVATE_NEW & filters.COMMAND, on_unknown_command))
    app.add_handler(MessageHandler(PRIVATE_NEW & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))


# ---------------------------------------------------------------- helpers


async def say(context, chat_id, text, markup=None):
    """Send text (split into ≤ 4096-character messages); buttons go on the last message."""
    pages = paginate(text)
    for number, page in enumerate(pages):
        buttons = markup if number == len(pages) - 1 else None
        await context.bot_data["throttle"].send(
            context.bot, chat_id, text=page, reply_markup=buttons, **SEND_OPTIONS
        )


def sender(context):
    """A send(chat_id, text, markup) function for bot/relay.py."""

    async def send(chat_id, text, markup):
        await say(context, chat_id, text, markup)

    return send


async def remember_user(update, context) -> str:
    """Note where this (hashed) user can be reached; give a senior any waiting relay messages."""
    data = context.bot_data
    me = relay.user_hash(update.effective_user.id, data["salt"])
    data["chats"][me] = update.effective_chat.id
    senior = data["db"].senior_by_telegram_hash(me)
    if senior is not None:
        await relay.deliver_queued(data["db"], senior, data["chats"], sender(context))
    return me


async def ask(update, context, query="", repeat_key=None, **options):
    """THE one answer() call of an interaction.

    `repeat_key` names what the user sent ("text:khmer ocr"); the same thing
    within 30 seconds gets the saved result instead of a second search.
    `options` are answer()'s keyword arguments (intent, senior_id, project_id, facets).
    """
    chat_id = update.effective_chat.id
    recent = context.bot_data["recent"]
    if repeat_key:
        running_or_done = recent.get(chat_id, repeat_key)
        if running_or_done is not None:
            return await running_or_done  # the same query again: no second search

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    data = context.bot_data
    # answer() is slow (embedding, maybe Gemma), so it runs in a worker thread
    # and the bot keeps serving other chats meanwhile.
    search = asyncio.ensure_future(
        asyncio.to_thread(
            answer,
            query,
            data["client"],
            data["embedder"],
            data["db"],
            data["threshold"],
            data["enable_llm"],
            **options,
        )
    )
    if repeat_key:
        recent.put(chat_id, repeat_key, search)
    try:
        result = await search
    except Exception:
        if repeat_key:
            recent.forget(chat_id, repeat_key)
        raise
    if query:
        log_query(update, context, result)
    return result


def log_query(update, context, result):
    """Save the search in query_log (T6.4). Only the salted hash of the user id is stored."""
    data = context.bot_data
    user = relay.user_hash(update.effective_user.id, data["salt"])
    message = getattr(update.effective_message, "message_id", None)
    log_result(data["db"], result, surface="telegram", tg_user_hash=user, tg_message_id=message)


def record_click(context, project_id=None, senior_id=None):
    """Abstract or Portfolio tapped on a result card: set clicked_project on that search (T6.4)."""
    pager = state.get_pager(context.chat_data.get("pager"))
    if pager is None:
        return
    for card in pager.citations:
        if card.project_id == project_id or (senior_id and senior_id in card.senior_ids):
            try:
                context.bot_data["db"].clicked_project(pager.query_id, card.project_id)
            except Exception as error:  # logging must never break the reply
                logger.warning("click log write failed: %s", error)
            return


# ---------------------------------------------------------------- showing results


async def show(update, context, result):
    """Format one AnswerResult and send it."""
    chat_id = update.effective_chat.id

    if result.mode == "portfolio":
        await show_portfolio(context, chat_id, result.portfolio)
        if result.answer:  # "Sok Dara's OCR project" with no matching project
            await say(context, chat_id, esc(result.answer))
    elif result.mode == "ambiguous":
        candidates = list(result.resolution.candidates)
        key = state.put_candidates([c["senior_id"] for c in candidates])
        names = {c.get("full_name_latin") for c in candidates}
        name = names.pop() if len(names) == 1 else result.query
        text, markup = candidate_keyboard(name, candidates, key)
        await say(context, chat_id, text, markup)
    elif result.mode == "not_found":
        await say(context, chat_id, not_found_text(result))
    elif result.mode == "abstain":
        await say(context, chat_id, abstain_text(result))
        await ask_for_rating(context, chat_id, result)
    elif result.mode == "project":
        text, markup = abstract_card(result.citations[0])
        await say(context, chat_id, text, markup)
    else:  # extractive, llm, extractive_fallback (topic search, question, facet browse)
        if result.route == "FACET_BROWSE":
            text, markup = facet_header(result.facets, result.facet_counts)
            await say(context, chat_id, text, markup)
        if result.mode == "llm":
            await say(context, chat_id, esc(result.answer))
        if result.citations:
            key = state.put_pager(result.query_id, 0, result.citations)
            context.chat_data["pager"] = key
            text, markup = project_card(result.citations[0], 0, len(result.citations))
            await say(context, chat_id, text, markup)
        await ask_for_rating(context, chat_id, result)


async def show_portfolio(context, chat_id, portfolio, page=0):
    senior_row = context.bot_data["db"].get_senior(portfolio.senior["senior_id"])
    text, markup = portfolio_page(portfolio, page, can_relay(senior_row))
    await say(context, chat_id, text, markup)


async def ask_for_rating(context, chat_id, result):
    if result.query:  # only typed searches are logged, so only they can be rated
        await say(context, chat_id, RATE_PROMPT, rating_keyboard(result.query_id))


# ---------------------------------------------------------------- commands


async def on_start(update, context):
    """/start, or a deep link: /start senior_<id> or /start proj_<id> (T5.6)."""
    await remember_user(update, context)
    payload = context.args[0] if context.args else ""
    if payload.startswith("senior_"):
        result = await ask(update, context, senior_id=payload.removeprefix("senior_"))
        await show(update, context, result)
    elif payload.startswith("proj_"):
        result = await ask(update, context, project_id=payload.removeprefix("proj_"))
        await show(update, context, result)
    else:
        await say(context, update.effective_chat.id, HELP)


async def on_help(update, context):
    await say(context, update.effective_chat.id, HELP)


async def on_about(update, context):
    await say(context, update.effective_chat.id, ABOUT)


async def on_unknown_command(update, context):
    await say(context, update.effective_chat.id, HELP)


async def on_search(update, context):
    await remember_user(update, context)
    query = " ".join(context.args or [])
    if not query:
        await say(context, update.effective_chat.id, SEARCH_USAGE)
        return
    result = await ask(update, context, query, repeat_key=f"text:{query}")
    await show(update, context, result)


async def on_senior(update, context):
    """/senior <name>: always a name lookup, even for a single unknown word."""
    await remember_user(update, context)
    name = " ".join(context.args or [])
    if not name:
        await say(context, update.effective_chat.id, SENIOR_USAGE)
        return
    result = await ask(update, context, name, repeat_key=f"senior:{name}", intent="SENIOR_LOOKUP")
    await show(update, context, result)


async def on_year(update, context):
    """/year 2024: facet browse by cohort year (filters, not a text search)."""
    await remember_user(update, context)
    year = context.args[0] if context.args else ""
    if not re.fullmatch(r"20\d\d", year):
        await say(context, update.effective_chat.id, YEAR_USAGE)
        return
    result = await ask(update, context, repeat_key=f"year:{year}", facets={"year": int(year)})
    await show(update, context, result)


async def on_text(update, context):
    """Plain text: a search (the same path as /search), unless a relay message is expected."""
    me = await remember_user(update, context)
    text = update.effective_message.text or ""
    if "relay_to" in context.chat_data:
        await send_relay_message(update, context, me, text)
        return
    if "reply_to" in context.chat_data:
        await send_relay_reply(update, context, text)
        return
    result = await ask(update, context, text, repeat_key=f"text:{text}")
    await show(update, context, result)


# ---------------------------------------------------------------- buttons


async def on_callback(update, context):
    """Button presses. The data is short: 'kind:value'."""
    query = update.callback_query
    await query.answer()  # stops the button's loading spinner
    chat = update.effective_chat
    if chat is None or chat.type != ChatType.PRIVATE:
        return
    await remember_user(update, context)
    kind, _, value = (query.data or "").partition(":")

    if kind == "ab":  # Abstract
        record_click(context, project_id=value)
        await show(update, context, await ask(update, context, project_id=value))
    elif kind == "pf":  # Portfolio, or the next page of a portfolio: pf:<senior_id>[:<page>]
        senior_id, _, page = value.partition(":")
        if not page:  # the Portfolio button on a result card (not a portfolio "Next")
            record_click(context, senior_id=senior_id)
        result = await ask(update, context, senior_id=senior_id)
        if result.mode == "portfolio":
            await show_portfolio(context, chat.id, result.portfolio, int(page or 0))
        else:
            await show(update, context, result)
    elif kind == "pg":  # Next result card: pg:<project_id>:<page>
        await show_next_card(update, context, value)
    elif kind == "pick":  # a senior chosen from the ambiguity keyboard: pick:<key>:<index>
        key, _, index = value.partition(":")
        senior_ids = state.get_candidates(key)
        if senior_ids is None or not index.isdigit() or int(index) >= len(senior_ids):
            await say(context, chat.id, EXPIRED)
            return
        await show(update, context, await ask(update, context, senior_id=senior_ids[int(index)]))
    elif kind == "fc":  # facet chip: fc:<encoded facets>, "fc:" = Clear all
        await show(update, context, await ask(update, context, facets=read_chip(value)))
    elif kind == "rate":  # 👍/👎: rate:<query_id>:<1|-1>
        query_id, _, rating = value.rpartition(":")
        await asyncio.to_thread(context.bot_data["db"].rate, query_id, int(rating))
        await query.edit_message_text("Thanks for the feedback.")  # the buttons disappear
    elif kind == "relay":  # "Send a message" on a portfolio: relay:<senior_id>
        await start_relay(update, context, value)
    elif kind == "rr":  # "Reply" on a relayed message: rr:<relay_id>
        context.chat_data["reply_to"] = value
        await say(context, chat.id, REPLY_ASK)


async def show_next_card(update, context, value):
    chat_id = update.effective_chat.id
    project_id, _, page = value.rpartition(":")
    pager = state.get_pager(context.chat_data.get("pager"))
    if pager is None or not page.isdigit():
        await say(context, chat_id, EXPIRED)
        return
    page = int(page)
    # The button must belong to the latest results of this chat.
    if not 0 < page < len(pager.citations) or pager.citations[page - 1].project_id != project_id:
        await say(context, chat_id, EXPIRED)
        return
    pager.page = page
    text, markup = project_card(pager.citations[page], page, len(pager.citations))
    await say(context, chat_id, text, markup)


# ---------------------------------------------------------------- relay (T5.8)


async def start_relay(update, context, senior_id):
    chat_id = update.effective_chat.id
    senior = context.bot_data["db"].get_senior(senior_id)
    if not can_relay(senior):
        await say(context, chat_id, esc(CONSENT_DENIED))
        return
    context.chat_data["relay_to"] = senior_id
    await say(context, chat_id, esc(RELAY_ASK.format(name=senior["full_name_latin"])))


async def send_relay_message(update, context, me, text):
    chat_id = update.effective_chat.id
    data = context.bot_data
    senior_id = context.chat_data.pop("relay_to")
    relay_id = relay.queue_relay(data["db"], senior_id, me, text)
    if relay_id is None:
        await say(context, chat_id, RELAY_REFUSED)
    elif await relay.deliver_relay(data["db"], relay_id, data["chats"], sender(context)):
        await say(context, chat_id, RELAY_SENT)
    else:
        await say(context, chat_id, RELAY_QUEUED)


async def send_relay_reply(update, context, text):
    chat_id = update.effective_chat.id
    data = context.bot_data
    relay_id = context.chat_data.pop("reply_to")
    if await relay.forward_reply(data["db"], relay_id, text, data["chats"], sender(context)):
        await say(context, chat_id, REPLY_SENT)
    else:
        await say(context, chat_id, REPLY_FAILED)
