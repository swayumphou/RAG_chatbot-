"""The whole Telegram interface with a fake transport (T5.9).

Telegram is never contacted. FakeBot records every send, chat action, and
button answer; deliver() plays the role of python-telegram-bot's update loop
(pick the handler; an exception goes to the error handler). The service is
the real answer() with the Qdrant search replaced by fixed results.
"""

import ast
import asyncio
import re
import time
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

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "fixtures" / "manifest"
CHAT = 4200
MIN_GAP = 0.01  # a short gap keeps the 50-message test fast

COMMANDS = {
    "start": handlers.on_start,
    "help": handlers.on_help,
    "about": handlers.on_about,
    "search": handlers.on_search,
    "senior": handlers.on_senior,
    "year": handlers.on_year,
}


class FakeBot:
    def __init__(self):
        self.sent = []
        self.actions = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "time": time.perf_counter(), **kwargs})
        return SimpleNamespace(message_id=len(self.sent))

    async def send_chat_action(self, chat_id, action):
        self.actions.append(action)


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answered = False
        self.edited = None

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edited = text


async def deliver(context, text=None, button=None):
    """Send one update through the bot, like the polling loop would."""
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=CHAT, type="private"),
        effective_user=SimpleNamespace(id=42),
        effective_message=SimpleNamespace(text=text),
        callback_query=FakeQuery(button) if button is not None else None,
    )
    if button is not None:
        handler = handlers.on_callback
    elif text.startswith("/"):
        name, *args = text[1:].split()
        context.args = args
        handler = COMMANDS.get(name, handlers.on_unknown_command)
    else:
        handler = handlers.on_text
    try:
        await handler(update, context)
    except Exception as error:  # the polling loop hands it to the error handler and goes on
        await main.on_error(update, SimpleNamespace(bot=context.bot, error=error))
    return update


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
    bot_data = {
        "client": object(),
        "embedder": None,
        "db": db,
        "threshold": 0.45,
        "enable_llm": False,
        "salt": "test-salt",
        "throttle": ChatThrottle(min_gap=MIN_GAP),
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
        snippet=f"Passage of {project_id} & <friends>.",  # must arrive escaped
        score=0.03,
        dense_score=score,
        matched_by="both",
    )


@pytest.fixture
def calls(monkeypatch):
    """Records every answer() call; the query 'explode' makes the service fail."""
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
        if query == "explode":
            raise RuntimeError("the service failed")
        return service.answer(query, client, embedder, db, threshold, enable_llm, **options)

    monkeypatch.setattr(handlers, "answer", spy)
    return recorded


def buttons(message):
    markup = message.get("reply_markup")
    return [] if markup is None else [b for row in markup.inline_keyboard for b in row]


def button_data(context, label):
    """The callback data of the newest button with this label."""
    for message in reversed(context.bot.sent):
        for button in buttons(message):
            if button.text == label:
                return button.callback_data
    raise AssertionError(f"no button {label!r}")


async def test_the_whole_bot_contract(context, calls, db):
    sent = context.bot.sent

    async def step(expected_calls, text=None, button=None):
        """One interaction; returns the messages it sent. Checks the number of answer() calls."""
        before_calls, before_sent = len(calls), len(sent)
        update = await deliver(context, text=text, button=button)
        assert len(calls) - before_calls == expected_calls, (text, button)
        return update, sent[before_sent:]

    # 1. /start -> help
    _, out = await step(0, "/start")
    assert out[0]["text"] == handlers.HELP

    # 2. a plain topic query -> a project card (+ rating buttons)
    _, out = await step(1, "khmer ocr")
    assert out[0]["text"].startswith("<b>Khmer Handwritten Text Recognition with Tesseract</b>")
    assert "Passage of dse2024-001 &amp; &lt;friends&gt;." in out[0]["text"]
    assert context.bot.actions[-1] == "typing"

    # 3. /senior Sok Dara -> portfolio
    _, out = await step(1, "/senior Sok Dara")
    assert out[0]["text"].startswith("<b>Sok Dara · សុខ ដារា</b>")

    # 4. an ambiguous name -> a keyboard, nobody picked
    _, out = await step(1, "/senior Keo Sophea")
    assert out[0]["text"] == "I found 2 seniors named Keo Sophea. Which one?"
    assert len(buttons(out[0])) == 2

    # 5. a portfolio button on the project card (two authors, one button each) -> that portfolio
    _, out = await step(1, button=button_data(context, "Chan Sreyneang"))
    assert out[0]["text"].startswith("<b>Chan Sreyneang")

    # 6. Next -> the next result card, from memory (no new search)
    update, out = await step(0, button=button_data(context, "Next ▶"))
    assert update.callback_query.answered
    assert out[0]["text"].startswith("<b>Khmer News Topic Classification</b>")

    # 7. /year 2024 -> facet header with chips
    _, out = await step(1, "/year 2024")
    assert out[0]["text"] == f"<b>2024</b> · {db.facet_counts({'year': 2024})['total']} projects"
    assert {b.text for b in buttons(out[0])} == {"DSE 4", "ITE 2", "Clear all"}

    # 8. a senior_<id> deep link -> portfolio
    _, out = await step(1, "/start senior_s-2024-014")
    assert out[0]["text"].startswith("<b>Sok Dara")

    # 9. 👍 -> the rating is saved in query_log
    thumbs_up = button_data(context, "👍")
    update, _ = await step(0, button=thumbs_up)
    query_id = thumbs_up.split(":")[1]
    assert db.one("SELECT rating FROM query_log WHERE query_id = ?", (query_id,))["rating"] == 1
    assert update.callback_query.edited == "Thanks for the feedback."

    # A failing handler: the user gets the hint and the bot keeps working.
    _, out = await step(1, "explode")
    assert out[-1]["text"] == main.ERROR_HINT
    _, out = await step(0, "khmer ocr")  # still answered (from the 30 s memory)
    assert out[0]["text"].startswith("<b>Khmer Handwritten")

    # 10. 50 rapid messages: no unhandled error, spacing respected, duplicates not re-searched.
    start = len(sent)
    before = len(calls)
    await asyncio.gather(*(deliver(context, text=f"topic {i % 5}") for i in range(50)))
    burst = sent[start:]
    assert main.ERROR_HINT not in [m["text"] for m in burst]
    assert len(calls) - before == 5  # five different texts, each searched once
    times = [m["time"] for m in burst]
    pairs = zip(times, times[1:], strict=False)
    assert all(later - earlier >= MIN_GAP for earlier, later in pairs)

    # Every message: HTML mode, at most 4096 characters, no half-open tags.
    for message in sent:
        assert len(message["text"]) <= 4096
        assert message.get("parse_mode") == "HTML"
        for tag in ("b", "i", "a"):
            opened = len(re.findall(rf"<{tag}[ >]", message["text"]))
            assert opened == message["text"].count(f"</{tag}>"), message["text"]


def test_no_handler_imports_a_retrieval_module():
    for path in (ROOT / "bot").glob("*.py"):
        if path.name == "format.py":
            continue  # format.py uses only the facet text helpers (encode/decode)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(("retrieval", "qdrant_client")), path.name
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(("retrieval", "qdrant_client")), path.name


async def test_tapping_abstract_records_the_click(context, calls, db):
    """T6.4: a search writes one query_log row; tapping Abstract sets clicked_project."""
    await deliver(context, text="khmer ocr")
    [logged] = db.query_log_rows()
    assert logged["raw_query"] == "khmer ocr" and logged["surface"] == "telegram"
    assert logged["tg_user_hash"] not in (None, "42")  # a salted hash, never the raw id

    await deliver(context, button=button_data(context, "Abstract"))
    assert db.query_log_rows()[0]["clicked_project"] == "dse2024-001"
