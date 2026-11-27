"""Tests for the short-lived button memory (T5.5)."""

import pytest

from bot import state


@pytest.fixture(autouse=True)
def fresh_stores(monkeypatch):
    """Each test gets empty stores and a clock it can move."""
    now = {"t": 1000.0}
    for name in ("candidates", "pagers"):
        store = state.TTLStore()
        store.clock = lambda: now["t"]
        monkeypatch.setattr(state, name, store)
    return now


def test_candidates_round_trip_with_a_short_key():
    key = state.put_candidates(["s-2024-031", "s-2024-044"])
    assert len(key) == 8
    assert len(f"pick:{key}:1".encode()) <= 64  # the button stays far below 64 bytes
    assert state.get_candidates(key) == ["s-2024-031", "s-2024-044"]
    assert state.get_candidates("unknown!") is None


def test_entries_expire_after_ten_minutes(fresh_stores):
    key = state.put_candidates(["s-2024-031"])
    fresh_stores["t"] += 599
    assert state.get_candidates(key) == ["s-2024-031"]
    fresh_stores["t"] += 2  # 601 seconds old
    assert state.get_candidates(key) is None


def test_the_store_is_capped_at_500_entries():
    keys = [state.put_candidates([f"s-{i}"]) for i in range(501)]
    assert len(state.candidates.items) == 500
    assert state.get_candidates(keys[0]) is None  # the oldest was forgotten
    assert state.get_candidates(keys[-1]) == ["s-500"]


def test_pager_keeps_the_result_cards(fresh_stores):
    key = state.put_pager("query-1", 0, ["card A", "card B"])
    pager = state.get_pager(key)
    assert (pager.query_id, pager.page, pager.citations) == ("query-1", 0, ["card A", "card B"])
    fresh_stores["t"] += 601
    assert state.get_pager(key) is None
