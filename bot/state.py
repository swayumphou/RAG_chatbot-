"""Short-lived memory for buttons: candidate lists and result pages.

A Telegram button can carry only 64 bytes, so it holds a short key (for
example "pick:3f9a1c2e:0") and the real data waits here.

- Each entry lives 10 minutes (STATE_TTL). After that the key is "expired"
  and the bot asks the user to search again.
- At most 500 entries of each kind; the oldest is forgotten first.
- Memory only: a restart forgets everything, which is fine for buttons.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from config import STATE_TTL

MAX_ENTRIES = 500


@dataclass
class CandidateState:
    senior_ids: list[str]  # the seniors offered by an "ambiguous name" keyboard
    created_at: float


@dataclass
class PagerState:
    query_id: str
    page: int  # the result card shown last
    created_at: float
    citations: list = field(default_factory=list)  # the result cards of that query


class TTLStore:
    """A small dict whose entries expire after `ttl` seconds."""

    def __init__(self, ttl=STATE_TTL, max_entries=MAX_ENTRIES):
        self.ttl = ttl
        self.max_entries = max_entries
        self.items = {}
        self.clock = time.monotonic  # tests replace this to move time forward

    def new_key(self) -> str:
        return secrets.token_hex(4)  # 8 characters: short enough for a button

    def put(self, key, value):
        self.items.pop(key, None)
        self.items[key] = value
        while len(self.items) > self.max_entries:
            oldest = next(iter(self.items))  # dicts keep insertion order
            del self.items[oldest]

    def get(self, key):
        value = self.items.get(key)
        if value is None:
            return None
        if self.clock() - value.created_at > self.ttl:
            del self.items[key]  # expired
            return None
        return value


candidates = TTLStore()
pagers = TTLStore()


def put_candidates(senior_ids) -> str:
    """Remember the seniors of an ambiguity keyboard. Returns the key for the buttons."""
    key = candidates.new_key()
    candidates.put(key, CandidateState(list(senior_ids), candidates.clock()))
    return key


def get_candidates(key) -> list[str] | None:
    """The remembered seniors, or None when the key is unknown or expired."""
    entry = candidates.get(key)
    return entry.senior_ids if entry else None


def put_pager(query_id, page, citations=()) -> str:
    """Remember the result cards of one query. Returns the key."""
    key = pagers.new_key()
    pagers.put(key, PagerState(query_id, page, pagers.clock(), list(citations)))
    return key


def get_pager(key) -> PagerState | None:
    """The remembered results, or None when the key is unknown or expired."""
    return pagers.get(key)
