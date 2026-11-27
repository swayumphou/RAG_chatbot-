"""Start the Telegram bot.

    python -m bot.main           # long polling
    python -m bot.main --check   # readiness check; never contacts Telegram

- The token comes from TELEGRAM_BOT_TOKEN only. When it is missing and the
  bot runs in a terminal, it is asked for (hidden input). It is never logged.
- Private chats only; edited messages are ignored (see bot/handlers.py).
- A handler error sends the user a short hint; polling keeps running.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from telegram.ext import ApplicationBuilder

from bot import handlers
from bot.sender import ChatThrottle, RecentReplies
from config import ConfigError, get_config
from logging_config import configure_logging, log_startup

logger = logging.getLogger(__name__)

ERROR_HINT = (
    "Sorry, something went wrong. Try a topic (khmer ocr), /senior &lt;name&gt;, or /year 2024."
)


def build_application(token, deps):
    """The bot application with every handler registered. `deps` go into bot_data."""
    app = ApplicationBuilder().token(token).concurrent_updates(True).build()
    app.bot_data.update(deps)
    handlers.register(app)
    app.add_error_handler(on_error)
    return app


async def on_error(update, context):
    """Any handler exception: log it, tell the user briefly, and keep polling."""
    logger.error("handler failed: %r", context.error)  # the log filter removes tokens
    chat = getattr(update, "effective_chat", None)  # some errors come without a chat
    if chat is not None:
        try:
            await context.bot.send_message(chat_id=chat.id, text=ERROR_HINT, parse_mode="HTML")
        except Exception as error:
            logger.warning("could not send the error hint: %r", error)


def get_token(config) -> str:
    """TELEGRAM_BOT_TOKEN from the environment, or typed in a local terminal."""
    if config.telegram_bot_token:
        return config.telegram_bot_token
    if sys.stdin.isatty():
        return getpass.getpass("TELEGRAM_BOT_TOKEN (hidden): ").strip()
    raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")


def load_dependencies(config) -> dict:
    """Open SQLite and Qdrant and load bge-m3 once; every handler shares them."""
    from ingestion.embedder import Embedder
    from ingestion.indexer import connect_qdrant
    from store.db import DB, DEFAULT_DB_PATH

    embedder = Embedder(config.embed_model)
    embedder.load()
    return {
        "client": connect_qdrant(config.qdrant_url),
        "embedder": embedder,
        "db": DB(DEFAULT_DB_PATH),
        "threshold": config.abstain_threshold,
        "enable_llm": config.enable_llm,
        "salt": config.kb_salt,
        "throttle": ChatThrottle(config.min_send_gap),
        "recent": RecentReplies(config.dedupe_seconds),
        "chats": {},  # hashed user -> chat id, in memory only (for the relay)
    }


def check(config) -> int:
    """Readiness check without contacting Telegram. Prints one line per item; 0 = all OK."""
    from ingestion.embedder import Embedder
    from ingestion.indexer import connect_qdrant, count_points
    from store.db import DB, DEFAULT_DB_PATH

    def token_set():
        if not config.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        return "set (not shown, Telegram not contacted)"

    def salt_set():
        if not config.kb_salt:
            raise RuntimeError("KB_SALT is not set")
        return "set (not shown)"

    def sqlite():
        if not DEFAULT_DB_PATH.exists():  # a check must not create an empty database
            raise RuntimeError(f"{DEFAULT_DB_PATH} not found; run python -m ingestion.run first")
        db = DB(DEFAULT_DB_PATH)
        return f"{len(db.all_seniors())} active seniors"

    def qdrant():
        points = count_points(connect_qdrant(config.qdrant_url))
        if points == 0:
            raise RuntimeError("the index is empty; run python -m ingestion.run first")
        return f"{points} points"

    def embedder():
        model = Embedder(config.embed_model)
        model.load()
        return model.revision

    ok = True
    for name, step in [
        ("token", token_set),
        ("salt", salt_set),
        ("sqlite", sqlite),
        ("qdrant", qdrant),
        ("embedder", embedder),
    ]:
        try:
            print(f"OK    {name}: {step()}")
        except Exception as error:
            print(f"FAIL  {name}: {error}")
            ok = False
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RUPP senior-project Telegram bot")
    parser.add_argument("--check", action="store_true", help="readiness check, no Telegram")
    args = parser.parse_args(argv)

    try:
        config = get_config()
    except ConfigError as error:
        print(f"Configuration error: {error}")
        return 1
    if args.check:
        return check(config)

    token = get_token(config)
    if not config.kb_salt:
        print("KB_SALT is not set.")
        return 1
    configure_logging(level=config.log_level, secrets=(token, config.kb_salt))
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines contain the token

    deps = load_dependencies(config)
    from ingestion.indexer import collection_name, count_points

    try:
        qdrant_version = deps["client"].info().version
    except Exception:
        qdrant_version = "unknown"
    log_startup(
        embed_model=deps["embedder"].revision,
        llm_model=config.ollama_model,
        qdrant_version=qdrant_version,
        collection=collection_name(),
        point_count=count_points(deps["client"]),
        enable_llm=config.enable_llm,
    )
    app = build_application(token, deps)
    app.run_polling(allowed_updates=["message", "callback_query"])  # no edited messages
    return 0


if __name__ == "__main__":
    sys.exit(main())
