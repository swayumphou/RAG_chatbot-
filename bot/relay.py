"""Consent-gated relay: a student writes to a senior through the bot.

Flow (T5.8):
  1. A portfolio shows "Send a message" only when can_relay(senior):
     the senior agreed (consent_relay=1) AND has started the bot.
  2. The student taps it and types the message.
  3. queue_relay() stores it as "queued" with the student's HASHED id.
  4. deliver_relay() sends it to the senior, only with consent and only when
     the senior has started the bot.
  5. The senior taps "Reply"; forward_reply() sends the answer back.
  6. Neither side ever sees the other's Telegram name or ID: the bot is the
     sender of every message, and the database stores only hashes.

`chats` maps a hashed user to the chat the bot last saw them in. It lives
only in memory (bot_data), so raw Telegram IDs are never written to disk.
`send(chat_id, text, markup)` is the bot's throttled send function.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from answer.consent import can_relay
from bot.format import esc, relay_reply_keyboard

logger = logging.getLogger(__name__)

RELAY_LIMIT_PER_HOUR = 3  # messages one student may send per hour


def user_hash(user_id, salt) -> str:
    """sha256(user_id + salt): the only form in which a Telegram user ID is stored."""
    return hashlib.sha256(f"{user_id}{salt}".encode()).hexdigest()


def queue_relay(db, senior_id, from_hash, body, now=None) -> str | None:
    """Store the message as 'queued'. Returns the relay_id, or None when it is not allowed."""
    senior = db.get_senior(senior_id)
    if not can_relay(senior):
        logger.info("relay refused: no relay consent or the senior has not started the bot")
        return None

    now = now or datetime.now(UTC)
    one_hour_ago = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    if db.relay_count_since(from_hash, one_hour_ago) >= RELAY_LIMIT_PER_HOUR:
        logger.warning("relay dropped: sender reached %d messages/hour", RELAY_LIMIT_PER_HOUR)
        return None

    relay_id = db.add_relay_message(senior_id, from_hash, body.strip())
    logger.info("relay queued relay_id=%s", relay_id)
    return relay_id


async def deliver_relay(db, relay_id, chats, send) -> bool:
    """Send a queued message to the senior. False when the senior cannot get it now."""
    relay = db.get_relay(relay_id)
    if relay is None:
        return False
    senior = db.get_senior(relay["senior_id"])
    if not can_relay(senior):  # never forward without consent or before the senior started the bot
        return False
    chat_id = chats.get(senior["telegram_user_hash"])
    if chat_id is None:
        return False  # not reachable right now: it stays queued until the senior opens the bot

    text = f"A student sent you a message through the project bot:\n\n{esc(relay['body'])}"
    await send(chat_id, text, relay_reply_keyboard(relay_id))
    db.set_relay_status(relay_id, "delivered")
    logger.info("relay delivered relay_id=%s", relay_id)
    return True


async def deliver_queued(db, senior, chats, send) -> int:
    """When a senior opens the bot, hand over every message waiting for them."""
    delivered = 0
    for relay in db.queued_relays(senior["senior_id"]):
        if await deliver_relay(db, relay["relay_id"], chats, send):
            delivered += 1
    return delivered


async def forward_reply(db, relay_id, body, chats, send) -> bool:
    """Send the senior's reply back to the student. False when the student is not reachable."""
    relay = db.get_relay(relay_id)
    if relay is None:
        return False
    chat_id = chats.get(relay["from_hash"])
    if chat_id is None:
        return False
    senior = db.get_senior(relay["senior_id"])
    text = f"Reply from {esc(senior['full_name_latin'])}:\n\n{esc(body)}"
    await send(chat_id, text, None)
    db.set_relay_status(relay_id, "replied")
    logger.info("relay reply forwarded relay_id=%s", relay_id)
    return True
