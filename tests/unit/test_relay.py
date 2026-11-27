"""Tests for the consent-gated relay (T5.8)."""

import logging
from pathlib import Path

import pytest

pytest.importorskip("telegram")

from bot.relay import (  # noqa: E402
    RELAY_LIMIT_PER_HOUR,
    deliver_queued,
    deliver_relay,
    forward_reply,
    queue_relay,
    user_hash,
)
from ingestion.validate_manifest import validate_manifest  # noqa: E402
from store.db import DB  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"
SALT = "test-salt"
STUDENT_ID, STUDENT_CHAT = 42, 4200  # fake Telegram ids
SENIOR_ID, SENIOR_CHAT = 99, 9900
CHAN = "s-2024-021"  # consent_relay = true in the fixture
SOK = "s-2024-014"  # consent_relay = false


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
    yield database
    database.close()


class Outbox:
    """Stands in for the bot's send function and records every message."""

    def __init__(self):
        self.messages = []

    async def __call__(self, chat_id, text, markup):
        self.messages.append({"chat_id": chat_id, "text": text, "markup": markup})


student = user_hash(STUDENT_ID, SALT)
senior = user_hash(SENIOR_ID, SALT)


def test_no_consent_means_no_relay(db):
    assert queue_relay(db, SOK, student, "Hello") is None
    assert db.rows("SELECT * FROM relay_message") == []


async def test_senior_who_has_not_started_the_bot_gets_nothing(db):
    # Chan agreed to the relay but never started the bot (no telegram_user_hash).
    assert queue_relay(db, CHAN, student, "Hello") is None
    assert db.rows("SELECT * FROM relay_message") == []


async def test_message_and_reply_hide_both_identities(db, caplog):
    db.set_telegram_user_hash(CHAN, senior)  # the senior started the bot
    chats = {student: STUDENT_CHAT, senior: SENIOR_CHAT}  # who is online (memory only)
    outbox = Outbox()

    with caplog.at_level(logging.INFO, logger="bot.relay"):
        relay_id = queue_relay(db, CHAN, student, "  How did you train the OCR model?  ")
        assert await deliver_relay(db, relay_id, chats, outbox) is True
        assert await forward_reply(db, relay_id, "We fine-tuned it.", chats, outbox) is True

    to_senior, to_student = outbox.messages
    assert to_senior["chat_id"] == SENIOR_CHAT
    assert "How did you train the OCR model?" in to_senior["text"]
    assert to_senior["markup"].inline_keyboard[0][0].callback_data == f"rr:{relay_id}"
    assert to_student["chat_id"] == STUDENT_CHAT
    assert to_student["text"] == "Reply from Chan Sreyneang:\n\nWe fine-tuned it."

    # Neither message contains the other side's Telegram id or hash.
    for message in outbox.messages:
        for secret in (str(STUDENT_ID), str(SENIOR_ID), student, senior):
            assert secret not in message["text"]

    # Only the hash is stored, and the relay is logged.
    row = db.get_relay(relay_id)
    assert (row["from_hash"], row["status"]) == (student, "replied")
    assert "relay queued" in caplog.text and "relay delivered" in caplog.text


async def test_an_offline_senior_gets_the_message_when_they_open_the_bot(db):
    db.set_telegram_user_hash(CHAN, senior)
    outbox = Outbox()
    relay_id = queue_relay(db, CHAN, student, "Hello")
    assert await deliver_relay(db, relay_id, {student: STUDENT_CHAT}, outbox) is False
    assert db.get_relay(relay_id)["status"] == "queued"  # kept, not lost

    # Later the senior opens the bot.
    chats = {student: STUDENT_CHAT, senior: SENIOR_CHAT}
    assert await deliver_queued(db, db.get_senior(CHAN), chats, outbox) == 1
    assert outbox.messages[0]["chat_id"] == SENIOR_CHAT
    assert db.get_relay(relay_id)["status"] == "delivered"


def test_relay_is_rate_limited_per_sender(db, caplog):
    db.set_telegram_user_hash(CHAN, senior)
    for number in range(RELAY_LIMIT_PER_HOUR):
        assert queue_relay(db, CHAN, student, f"message {number}") is not None
    with caplog.at_level(logging.WARNING, logger="bot.relay"):
        assert queue_relay(db, CHAN, student, "one too many") is None
    assert "relay dropped" in caplog.text
    # Another student is not affected.
    assert queue_relay(db, CHAN, user_hash(7, SALT), "hi") is not None
