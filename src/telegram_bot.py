"""Telegram bot (long polling).

    python src\\telegram_bot.py            start the bot (or double-click "Start Telegram Bot.cmd")
    python src\\telegram_bot.py --check    test the index and the models without contacting Telegram

The token comes from TELEGRAM_BOT_TOKEN (environment or .env); if it is not set, the bot asks for it.
Thin by design: it maps an update to ONE answer() call and renders the result. No retrieval, ranking
or threshold logic lives here - so the evaluation measures exactly what users get.

Language: a new user is first asked to pick English or Khmer; a question sent before that waits and
is answered right after the choice. Every answer is then in that language. The choice is saved in
SQLite (table user_language, by hashed user id), so it survives a restart; /language, the
"🌐 Language / ភាសា" button or typing "Language" / "ភាសា" changes it.

Suggestions: after every answer, a keyboard under the typing box offers 3 follow-up questions
(answer.suggest) and the Language button. Tapping one sends it as a normal question. A message can
carry only one keyboard, so the rating stays on the answer and the suggestions come in a short
message right after it.
"""
import argparse
import asyncio
import getpass
import hashlib
import time

import portalocker
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (AIORateLimiter, Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config
from answer import KB, LANGUAGES, answer, paginate, render, suggest

ASK_LANGUAGE = "Please select your preferred language / សូមជ្រើសរើសភាសាដែលអ្នកចង់បាន៖"
PICKER = InlineKeyboardMarkup([[InlineKeyboardButton("English", callback_data="lang:English"),
                                InlineKeyboardButton("ភាសាខ្មែរ (Khmer)", callback_data="lang:Khmer")]])
HELP = {
    "English": ("Hi! I answer questions about the <b>Data Science and Engineering (DSE)</b> program "
                "using official sources only.\n\n"
                "Try:\n"
                "• <code>What do I study in year 1?</code>\n"
                "• <code>/course Deep Learning and Application</code>\n"
                "• <code>/year 3</code>\n"
                "• <code>AIE courses in year 2 semester 1</code>\n"
                "• <code>Which majors does the department offer?</code>\n\n"
                "Every answer shows its source. Change the language: /language"),
    "Khmer": ("សួស្តី! ខ្ញុំឆ្លើយសំណួរអំពីកម្មវិធីសិក្សា <b>Data Science and Engineering (DSE)</b> "
              "ដោយប្រើតែប្រភពផ្លូវការប៉ុណ្ណោះ។\n\n"
              "សាកល្បង៖\n"
              "• <code>មុខវិជ្ជាឆ្នាំទី១</code>\n"
              "• <code>/course Deep Learning and Application</code>\n"
              "• <code>/year 3</code>\n"
              "• <code>តើដេប៉ាតឺម៉ង់មានជំនាញអ្វីខ្លះ?</code>\n\n"
              "ចម្លើយនីមួយៗបង្ហាញប្រភពរបស់វា។ ប្តូរភាសា៖ /language"),
}
CHOSEN = {"English": "Language: English", "Khmer": "ភាសា៖ ខ្មែរ"}
THANKS = {"English": "Thanks for the feedback!", "Khmer": "អរគុណសម្រាប់មតិកែលម្អ!"}
FAILED = "Sorry, something went wrong. Try /help. / សូមអភ័យទោស មានបញ្ហាកើតឡើង។ សូមសាកល្បង /help។"
LANGUAGE_BUTTON = "🌐 Language / ភាសា"
LANGUAGE_WORDS = {LANGUAGE_BUTTON.casefold(), "language", "ភាសា", "lang"}   # typed or tapped -> picker
MORE = {"English": "More questions 👇", "Khmer": "សំណួរបន្ថែម 👇"}


def _keyboard(suggestions: list[str]) -> ReplyKeyboardMarkup:
    """The buttons under the typing box: the follow-up questions, then the Language button."""
    return ReplyKeyboardMarkup([[s] for s in suggestions] + [[LANGUAGE_BUTTON]],
                               resize_keyboard=True, is_persistent=True)
DEDUPE_SECONDS = 30
_recent: dict[tuple[int, str, str], tuple[float, dict]] = {}


def _user_hash(user_id: int) -> str:
    return hashlib.sha256(f"{user_id}{config.KB_SALT}".encode()).hexdigest()[:32]


async def _ask_language(chat) -> None:
    await chat.send_message(ASK_LANGUAGE, reply_markup=PICKER)


async def _reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    """Answer in the user's language. A user who has not chosen one is asked first; the question waits."""
    if query.strip().casefold() in LANGUAGE_WORDS:
        return await _ask_language(update.effective_chat)
    user = _user_hash(update.effective_user.id)
    language = ctx.bot_data["kb"].db.language(user)
    if not language:
        if query.strip():
            ctx.user_data["pending"] = query
        return await _ask_language(update.effective_chat)
    if not query.strip():
        return await _help(update.effective_chat, language)
    await _answer(update.effective_chat, ctx, query, user, language)


async def _help(chat, language: str) -> None:
    await chat.send_message(HELP[language], parse_mode=ParseMode.HTML, reply_markup=_keyboard(suggest(language)))


async def _answer(chat, ctx: ContextTypes.DEFAULT_TYPE, query: str, user: str, language: str) -> None:
    await chat.send_action(ChatAction.TYPING)
    key, now = (chat.id, query.strip().casefold(), language), time.monotonic()
    cached = _recent.get(key)
    if cached and now - cached[0] < DEDUPE_SECONDS:
        result = cached[1]
    else:
        result = await asyncio.to_thread(answer, query, ctx.bot_data["kb"], "telegram", user, language=language)
        _recent[key] = (now, result)

    pages = paginate(render(result, html=True))
    rating = InlineKeyboardMarkup([[InlineKeyboardButton("👍", callback_data=f"rt:{result['query_id']}:1"),
                                    InlineKeyboardButton("👎", callback_data=f"rt:{result['query_id']}:-1")]])
    for n, page in enumerate(pages, start=1):
        await chat.send_message(page, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                                reply_markup=rating if n == len(pages) else None)
    if result.get("suggestions"):
        await chat.send_message(MORE[language], reply_markup=_keyboard(result["suggestions"]))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, ctx, update.message.text)


async def on_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, ctx, " ".join(ctx.args))


async def on_year(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, ctx, f"courses in year {' '.join(ctx.args)}" if ctx.args else "")


async def on_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, ctx, "")                     # help in the user's language, or the language picker


async def on_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _ask_language(update.effective_chat)


async def on_language_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    language = update.callback_query.data.split(":", 1)[1]
    await update.callback_query.answer()
    if language not in LANGUAGES:
        return
    user = _user_hash(update.effective_user.id)
    ctx.bot_data["kb"].db.set_language(user, language)
    await update.callback_query.edit_message_text(CHOSEN[language])      # the picker becomes the confirmation
    pending = ctx.user_data.pop("pending", None)
    if pending:                                        # the question that was waiting for the choice
        await _answer(update.effective_chat, ctx, pending, user, language)
    else:
        await _help(update.effective_chat, language)


async def on_rating(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _, query_id, value = update.callback_query.data.split(":")
    db = ctx.bot_data["kb"].db
    db.rate(query_id, int(value))
    await update.callback_query.answer(THANKS[db.language(_user_hash(update.effective_user.id)) or "English"])
    await update.callback_query.edit_message_reply_markup(None)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    print("handler error:", repr(ctx.error))           # the polling loop must never die
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(FAILED)


def check() -> None:
    """Readiness test that never contacts Telegram: index, embedding model, answer model."""
    kb = KB()
    print(f"python-telegram-bot {telegram.__version__}")
    print(f"index: {kb.searcher.client.count(kb.searcher.name, exact=True).count} chunks, "
          f"{len(kb.db.courses())} course records")
    result = answer("Which majors does the department offer?", kb, surface="eval")
    print(f"test question -> mode: {result['mode']}" + (f" ({result['note']})" if result["note"] else ""))
    print("READY" if result["mode"] == "llm" or not kb.enable_llm else
          "NOT READY: Gemma did not answer - is Ollama running and the model pulled?")


def single_session():
    """Two bots polling with the same token fight each other, so allow one local session."""
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(config.INDEX_DIR / "telegram-polling.lock", "w")
    try:
        portalocker.lock(handle, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING)
    except portalocker.exceptions.LockException:
        raise SystemExit("The Telegram bot is already running in another window.") from None
    return handle                                      # stays locked until this process ends


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Telegram bot.")
    parser.add_argument("--check", action="store_true", help="test locally without contacting Telegram")
    if parser.parse_args().check:
        return check()

    lock = single_session()                            # noqa: F841  (kept open on purpose)
    token = config.TELEGRAM_BOT_TOKEN or getpass.getpass("Paste your BotFather token (input is hidden): ").strip()
    if not token:
        raise SystemExit("No token given. Create a bot with @BotFather in Telegram to get one.")
    app = (Application.builder().token(token)
           .rate_limiter(AIORateLimiter(max_retries=1)).build())   # honours 429 retry_after
    app.bot_data["kb"] = KB()
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler(["start", "help"], on_help, filters=private))
    app.add_handler(CommandHandler("language", on_language, filters=private))
    app.add_handler(CommandHandler(["ask", "search", "course"], on_ask, filters=private))
    app.add_handler(CommandHandler("year", on_year, filters=private))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED,
                                   on_text))
    app.add_handler(CallbackQueryHandler(on_language_chosen, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(on_rating, pattern=r"^rt:"))
    app.add_error_handler(on_error)
    print(f"bot running (answers by {config.OLLAMA_MODEL if config.ENABLE_LLM else 'quoted passages'}) "
          "- press Ctrl+C to stop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
