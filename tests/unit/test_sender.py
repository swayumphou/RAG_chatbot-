"""Tests for the send throttle, 429 handling, and the duplicate cache (T5.7)."""

import asyncio
import logging
import time

import pytest

pytest.importorskip("telegram")

from telegram.error import NetworkError, RetryAfter  # noqa: E402

from bot.sender import ChatThrottle, RecentReplies  # noqa: E402


class FakeBot:
    """Records when each message was sent. `failures` are raised first, in order."""

    def __init__(self, failures=()):
        self.failures = list(failures)
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append((chat_id, text, time.perf_counter()))
        return text


async def test_two_sends_to_one_chat_are_spaced():
    bot = FakeBot()
    throttle = ChatThrottle(min_gap=0.2)
    await asyncio.gather(throttle.send(bot, 1, text="a"), throttle.send(bot, 1, text="b"))
    (_, _, first), (_, _, second) = bot.sent
    assert second - first >= 0.2


async def test_other_chats_are_not_blocked():
    bot = FakeBot()
    throttle = ChatThrottle(min_gap=0.5)
    start = time.monotonic()
    await asyncio.gather(*(throttle.send(bot, chat, text="hi") for chat in range(5)))
    assert time.monotonic() - start < 0.4  # five chats in parallel, no 0.5 s waits
    assert len(bot.sent) == 5


async def test_a_429_sleeps_and_retries_once():
    bot = FakeBot(failures=[RetryAfter(0)])
    assert await ChatThrottle(min_gap=0).send(bot, 1, text="hello") == "hello"
    assert [text for _, text, _ in bot.sent] == ["hello"]


async def test_a_second_429_drops_and_logs(caplog):
    bot = FakeBot(failures=[RetryAfter(0), RetryAfter(0)])
    with caplog.at_level(logging.WARNING, logger="bot.sender"):
        assert await ChatThrottle(min_gap=0).send(bot, 1, text="hello") is None
    assert bot.sent == []
    assert "message dropped" in caplog.text  # never silent


async def test_other_send_errors_are_dropped_and_logged(caplog):
    bot = FakeBot(failures=[NetworkError("offline")])
    with caplog.at_level(logging.WARNING, logger="bot.sender"):
        assert await ChatThrottle(min_gap=0).send(bot, 1, text="hello") is None
    assert "message dropped" in caplog.text


def test_duplicate_query_within_30_seconds_is_not_answered_again():
    now = {"t": 0.0}
    recent = RecentReplies(seconds=30)
    recent.clock = lambda: now["t"]
    recent.put(7, "text:Khmer OCR", "saved result")
    now["t"] = 29
    assert recent.get(7, "text:khmer ocr ") == "saved result"  # same query, same chat
    assert recent.get(8, "text:khmer ocr") is None  # another chat
    now["t"] = 31
    assert recent.get(7, "text:khmer ocr") is None  # too old
