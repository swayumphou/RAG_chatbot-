"""Send messages without hitting Telegram's rate limits.

Telegram allows about 1 message per second per chat and about 30 per second
in total. During a live demo many students write at once, so:

- ChatThrottle sends one message at a time per chat, at least 1 s apart,
  and at most 25 per second in total. Other chats are never blocked.
- A 429 "RetryAfter" error: wait as long as Telegram says, retry once,
  then log and drop the message. A dropped message is always logged.
- RecentReplies remembers each chat's answers for 30 s, so the same query
  sent twice is answered from memory instead of searching again.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict

from telegram.error import RetryAfter, TelegramError

from config import DEDUPE_SECONDS, MIN_SEND_GAP
from logging_config import counters

logger = logging.getLogger(__name__)

GLOBAL_PER_SECOND = 25  # stay under Telegram's ~30 messages per second


def seconds_of(value) -> float:
    """RetryAfter.retry_after is a number of seconds (or a timedelta in newer versions)."""
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    return float(value)


class ChatThrottle:
    def __init__(self, min_gap=MIN_SEND_GAP, per_second=GLOBAL_PER_SECOND):
        self.min_gap = min_gap
        self.per_second = per_second
        self.locks = defaultdict(asyncio.Lock)  # one lock per chat
        self.last_sent = {}  # chat_id -> time of the last send
        self.global_slots = None  # created on first use, inside the running event loop

    async def send(self, bot, chat_id, **kwargs):
        """Send one message (send_message keyword arguments). Returns the message, or None."""
        async with self.locks[chat_id]:  # one message at a time for this chat
            # perf_counter is precise; asyncio.sleep may wake a few ms early, so wait in a loop.
            while True:
                wait = self.last_sent.get(chat_id, -math.inf) + self.min_gap - time.perf_counter()
                if wait <= 0:
                    break
                await asyncio.sleep(wait)
            await self.take_global_slot()
            message = await self.send_with_one_retry(bot, chat_id, kwargs)
            self.last_sent[chat_id] = time.perf_counter()
            return message

    async def take_global_slot(self):
        """At most `per_second` sends start in any one second, across all chats."""
        if self.global_slots is None:
            self.global_slots = asyncio.Semaphore(self.per_second)
        await self.global_slots.acquire()
        # The slot is given back one second later.
        asyncio.get_running_loop().call_later(1.0, self.global_slots.release)

    async def send_with_one_retry(self, bot, chat_id, kwargs):
        try:
            return await bot.send_message(chat_id=chat_id, **kwargs)
        except RetryAfter as error:
            counters.record_telegram_429()
            await asyncio.sleep(seconds_of(error.retry_after))  # wait as Telegram asks
        except TelegramError as error:
            return self.drop(error)
        try:
            return await bot.send_message(chat_id=chat_id, **kwargs)  # the one retry
        except TelegramError as error:
            return self.drop(error)

    def drop(self, error):
        counters.record_failed_send()
        logger.warning("message dropped: %s", error)  # never silent; no chat ID in the log
        return None


class RecentReplies:
    """Each chat's searches from the last 30 seconds, keyed by what the user sent.

    The bot stores the running search (an asyncio task), so a duplicate that
    arrives while the first search is still running waits for that same search.
    """

    def __init__(self, seconds=DEDUPE_SECONDS):
        self.seconds = seconds
        self.items = {}
        self.clock = time.monotonic  # tests replace this to move time forward

    def get(self, chat_id, key):
        found = self.items.get((chat_id, key.strip().casefold()))
        if found is None:
            return None
        saved_at, reply = found
        if self.clock() - saved_at > self.seconds:
            return None
        return reply

    def put(self, chat_id, key, reply):
        now = self.clock()
        self.items[(chat_id, key.strip().casefold())] = (now, reply)
        # Forget replies older than 30 seconds, so memory stays small.
        for old_key, (saved_at, _) in list(self.items.items()):
            if now - saved_at > self.seconds:
                del self.items[old_key]

    def forget(self, chat_id, key):
        """Drop one entry (a failed search must not be repeated from memory)."""
        self.items.pop((chat_id, key.strip().casefold()), None)
