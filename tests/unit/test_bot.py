"""Tests for the Telegram bot: start-up, commands, ambiguity keyboard, deep links, facet chips.

Telegram is never contacted: a FakeBot records what the handlers send.
"""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
pytest.importorskip("qdrant_client")
pytest.importorskip("rapidfuzz")

import answer.service as service  # noqa: E402
from bot import handlers, main  # noqa: E402
from bot.sender import ChatThrottle, RecentReplies  # noqa: E402
from ingestion.validate_manifest import validate_manifest  # noqa: E402
from retrieval.hybrid import RetrievedProject  # noqa: E402
from store.db import DB  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "fixtures" / "manifest"
TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijklmnop"


# ---------------------------------------------------------------- fakes


class FakeBot:
    """Records every message and chat action instead of calling Telegram."""

    def __init__(self):
        self.sent = []
        self.actions = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return SimpleNamespace(message_id=len(self.sent))

    async def send_chat_action(self, chat_id, action):
        self.actions.append(action)


class FakeQuery:
    """A pressed button."""

    def __init__(self, data):
        self.data = data
        self.edited = None

    async def answer(self):
        pass

    async def edit_message_text(self, text, **kwargs):
        self.edited = text


def update_for(text=None, button=None, chat_type="private"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=4200, type=chat_type),
        effective_user=SimpleNamespace(id=42),
        effective_message=SimpleNamespace(text=text),
        callback_query=FakeQuery(button) if button is not None else None,
    )


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


@pytest.fixture
def context(db):
    """One chat's context: the shared bot_data plus this chat's chat_data."""
    bot_data = {
        "client": object(),  # never used: the search is faked below
        "embedder": None,
        "db": db,
        "threshold": 0.45,
        "enable_llm": False,
        "salt": "test-salt",
        "throttle": ChatThrottle(min_gap=0),
        "recent": RecentReplies(),
        "chats": {},
    }
    return SimpleNamespace(bot=FakeBot(), bot_data=bot_data, chat_data={}, args=[])


def found(project_id, title, senior_ids, score):
    return RetrievedProject(
        project_id=project_id,
        title=title,
        senior_ids=senior_ids,
        year=2024,
        program="DSE",
        supervisor_id="sup-003",
        page_number=1,
        snippet=f"Passage of {project_id}.",
        score=0.03,
        dense_score=score,
        matched_by="both",
    )


@pytest.fixture
def calls(monkeypatch):
    """Spy on the ONE answer() call per interaction; the search inside answer() is faked."""
    results = [
        found(
            "dse2024-001",
            "Khmer Handwritten Text Recognition with Tesseract",
            ("s-2024-014", "s-2024-021"),
            0.80,
        ),
        found("dse2024-004", "Khmer News Topic Classification", ("s-2024-014",), 0.60),
    ]
    monkeypatch.setattr(service, "hybrid_retrieve", lambda *args, **kwargs: list(results))
    recorded = []

    def spy(query, client, embedder, db, threshold, enable_llm, **options):
        recorded.append((query, options))
        return service.answer(query, client, embedder, db, threshold, enable_llm, **options)

    monkeypatch.setattr(handlers, "answer", spy)
    return recorded


def texts(context):
    return [message["text"] for message in context.bot.sent]


def buttons(message):
    markup = message.get("reply_markup")
    if markup is None:
        return []
    return [button for row in markup.inline_keyboard for button in row]


# ---------------------------------------------------------------- T5.1


def message_json(chat_type):
    return {
        "message_id": 1,
        "date": 0,
        "chat": {"id": 5, "type": chat_type},
        "from": {"id": 42, "is_bot": False, "first_name": "Test"},
        "text": "hello",
    }


async def test_bootstrap_private_only_and_no_token_logging(tmp_path, capsys):
    from telegram import Update
    from telegram.ext import CommandHandler

    import logging_config

    root_level = logging.getLogger().level
    files = logging_config.configure_logging(log_dir=tmp_path, secrets=[TOKEN], console=False)
    try:
        app = main.build_application(TOKEN, {"threshold": 0.45, "enable_llm": False})
        assert app.bot_data["threshold"] == 0.45  # the shared service settings
        commands = set()
        for handler in app.handlers[0]:
            if isinstance(handler, CommandHandler):
                commands |= set(handler.commands)
        assert commands == {"start", "help", "about", "search", "senior", "year"}
        assert main.on_error in app.error_handlers

        # Private, new messages only: groups and edited messages are ignored.
        new_private = {"update_id": 1, "message": message_json("private")}
        new_group = {"update_id": 2, "message": message_json("group")}
        edited_private = {"update_id": 3, "edited_message": message_json("private")}
        private = Update.de_json(new_private, app.bot)
        group = Update.de_json(new_group, app.bot)
        edited = Update.de_json(edited_private, app.bot)
        assert handlers.PRIVATE_NEW.check_update(private)
        assert not handlers.PRIVATE_NEW.check_update(group)
        assert not handlers.PRIVATE_NEW.check_update(edited)

        # A handler exception: the user gets a short hint; the token never reaches the logs.
        bot = FakeBot()
        failing = SimpleNamespace(bot=bot, error=RuntimeError(f"boom with {TOKEN}"))
        await main.on_error(private, failing)
        assert bot.sent[0]["text"] == main.ERROR_HINT
        log_text = files.bot.read_text(encoding="utf-8")
        assert "handler failed" in log_text
        assert TOKEN not in log_text
        output = capsys.readouterr()
        assert TOKEN not in output.out + output.err
    finally:
        logging_config._clear_owned_handlers()
        logging.getLogger().setLevel(root_level)


def test_token_comes_from_the_environment(monkeypatch):
    assert main.get_token(SimpleNamespace(telegram_bot_token=TOKEN)) == TOKEN
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN is not set"):
        main.get_token(SimpleNamespace(telegram_bot_token=None))


# ---------------------------------------------------------------- T5.2


async def test_commands_and_plain_text_call_one_service(context, calls, db):
    # Plain text -> one topic search, "typing...", a project card, and the rating buttons.
    await handlers.on_text(update_for("khmer ocr"), context)
    assert calls == [("khmer ocr", {})]
    assert context.bot.actions == ["typing"]
    assert "<b>Khmer Handwritten Text Recognition with Tesseract</b>" in texts(context)[0]

    # /search is the same path as plain text.
    context.args = ["khmer", "handwriting"]
    await handlers.on_search(update_for(), context)
    assert calls[-1] == ("khmer handwriting", {})

    # /senior is always a name lookup; /year is a facet filter.
    context.args = ["Sok", "Dara"]
    await handlers.on_senior(update_for(), context)
    assert calls[-1] == ("Sok Dara", {"intent": "SENIOR_LOOKUP"})
    assert "<b>Sok Dara · សុខ ដារា</b>" in texts(context)[-1]
    context.args = ["2024"]
    await handlers.on_year(update_for(), context)
    assert calls[-1] == ("", {"facets": {"year": 2024}})
    assert len(calls) == 4  # exactly one service call per interaction

    # No service call for help, usage errors, or an unknown command.
    for command, args in [
        (handlers.on_start, []),
        (handlers.on_help, []),
        (handlers.on_about, []),
        (handlers.on_unknown_command, []),
        (handlers.on_senior, []),
        (handlers.on_year, ["24"]),
        (handlers.on_search, []),
    ]:
        context.args = args
        await command(update_for(), context)
    assert len(calls) == 4
    assert texts(context).count(handlers.HELP) == 3  # /start, /help, unknown command
    assert handlers.SENIOR_USAGE in texts(context) and handlers.YEAR_USAGE in texts(context)

    # The same text again within 30 s: answered from memory, no second search.
    await handlers.on_text(update_for("khmer ocr"), context)
    assert len(calls) == 4


async def test_rating_updates_the_query_log_and_removes_the_buttons(context, calls, db):
    await handlers.on_text(update_for("khmer ocr"), context)
    rating = [m for m in context.bot.sent if m["text"] == "Was this helpful?"][0]
    thumbs_up = [b for b in buttons(rating) if b.text == "👍"][0]

    press = update_for(button=thumbs_up.callback_data)
    await handlers.on_callback(press, context)
    query_id = thumbs_up.callback_data.split(":")[1]
    assert db.one("SELECT rating FROM query_log WHERE query_id = ?", (query_id,))["rating"] == 1
    assert press.callback_query.edited == "Thanks for the feedback."  # buttons gone


def test_handlers_contain_no_retrieval_logic():
    tree = ast.parse((ROOT / "bot" / "handlers.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    for module in imported:
        assert not module.startswith(("retrieval", "ingestion", "qdrant_client", "store"))


# ---------------------------------------------------------------- T5.5


async def test_ambiguous_name_keyboard(context, calls):
    context.args = ["Keo", "Sophea"]
    await handlers.on_senior(update_for(), context)
    keyboard = context.bot.sent[-1]
    assert keyboard["text"] == "I found 2 seniors named Keo Sophea. Which one?"
    choices = buttons(keyboard)
    assert [b.text for b in choices] == ["Keo Sophea — DSE 2024", "Keo Sophea — ITE 2024"]
    assert all(len(b.callback_data.encode()) <= 64 for b in choices)
    assert not any("<b>Keo Sophea" in t for t in texts(context))  # nobody auto-selected

    # Choosing the second one opens that senior's portfolio.
    await handlers.on_callback(update_for(button=choices[1].callback_data), context)
    assert calls[-1] == ("", {"senior_id": "s-2024-044"})
    assert "ITE · Cohort 2024" in texts(context)[-1]

    # An expired or unknown key asks the user to search again (no service call).
    before = len(calls)
    await handlers.on_callback(update_for(button="pick:deadbeef:0"), context)
    assert texts(context)[-1] == handlers.EXPIRED
    assert len(calls) == before


# ---------------------------------------------------------------- T5.6


async def test_deep_links_and_facet_chips(context, calls, db):
    # senior_<id> opens the portfolio; proj_<id> opens the project.
    context.args = ["senior_s-2024-014"]
    await handlers.on_start(update_for(), context)
    assert calls[-1] == ("", {"senior_id": "s-2024-014"})
    assert "<b>Sok Dara · សុខ ដារា</b>" in texts(context)[-1]

    context.args = ["proj_dse2024-004"]
    await handlers.on_start(update_for(), context)
    assert "<b>Abstract</b>" in texts(context)[-1]
    assert "This fixture project classifies Khmer news" in texts(context)[-1]

    # Unknown ids get an explicit not-found reply.
    context.args = ["senior_s-0000-000"]
    await handlers.on_start(update_for(), context)
    assert texts(context)[-1] == service.NOT_FOUND
    context.args = ["proj_nothing"]
    await handlers.on_start(update_for(), context)
    assert texts(context)[-1] == service.PROJECT_NOT_FOUND

    # /year 2024: the count is the SQLite GROUP BY count, with one chip per program.
    context.args = ["2024"]
    await handlers.on_year(update_for(), context)
    header = [m for m in context.bot.sent if "projects" in m["text"]][-1]
    total = db.facet_counts({"year": 2024})["total"]
    assert header["text"] == f"<b>2024</b> · {total} projects"
    chips = {b.text: b.callback_data for b in buttons(header)}
    assert chips == {
        "DSE 4": "fc:year=2024&program=DSE",
        "ITE 2": "fc:year=2024&program=ITE",
        "Clear all": "fc:",
    }

    # Pressing a chip filters; the active filter can be removed; Clear all resets.
    await handlers.on_callback(update_for(button=chips["DSE 4"]), context)
    assert calls[-1] == ("", {"facets": {"year": 2024, "program": "DSE"}})
    header = [m for m in context.bot.sent if "projects" in m["text"]][-1]
    assert header["text"] == "<b>2024 · DSE</b> · 4 projects"
    chips = {b.text: b.callback_data for b in buttons(header)}
    assert chips == {"✕ DSE": "fc:year=2024", "Clear all": "fc:"}
    await handlers.on_callback(update_for(button="fc:"), context)
    assert calls[-1] == ("", {"facets": {}})
    assert all(len(data.encode()) <= 64 for data in chips.values())
