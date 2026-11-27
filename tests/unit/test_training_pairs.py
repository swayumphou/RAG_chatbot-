"""Tests for the training-pair exporter (T6.5)."""

import json

from datasets.build_training_pairs import build_pairs, main, write_jsonl
from store.db import DB


def log_row(query_id, created_at, clicked=None, rating=None, shown=("p1", "p2", "p3")):
    retrieved = [
        {"project_id": project_id, "rank": rank, "score": 0.03, "matched_by": "both"}
        for rank, project_id in enumerate(shown, start=1)
    ]
    return {
        "query_id": query_id,
        "raw_query": f"query {query_id}",
        "retrieved_json": json.dumps(retrieved),
        "clicked_project": clicked,
        "rating": rating,
        "created_at": created_at,
    }


def test_one_positive_and_two_hard_negatives():
    [pair] = build_pairs([log_row("q1", "2026-11-27T10:00:00+00:00", clicked="p2")])
    assert pair == {
        "query": "query q1",
        "positive_id": "p2",
        "hard_negatives": ["p1", "p3"],  # shown but not clicked, in rank order
        "schema_version": 1,
    }


def test_negative_rating_is_dropped():
    rows = [log_row("q1", "2026-11-27T10:00:00+00:00", clicked="p1", rating=-1)]
    assert build_pairs(rows) == []
    rows = [log_row("q1", "2026-11-27T10:00:00+00:00", clicked="p1", rating=1)]
    assert len(build_pairs(rows)) == 1


def test_a_query_without_a_click_is_skipped():
    assert build_pairs([log_row("q1", "2026-11-27T10:00:00+00:00")]) == []


def test_export_is_deterministic_and_valid_jsonl(tmp_path):
    rows = [
        log_row("q2", "2026-11-27T10:00:00+00:00", clicked="p1"),
        log_row("q1", "2026-11-27T10:00:00+00:00", clicked="p3"),  # same time: query_id decides
        log_row("q0", "2026-11-27T09:00:00+00:00", clicked="p2"),
    ]
    pairs = build_pairs(rows)
    assert [p["query"] for p in pairs] == ["query q0", "query q1", "query q2"]
    assert build_pairs(list(reversed(rows))) == pairs  # the input order does not matter

    out = tmp_path / "pairs.jsonl"
    write_jsonl(pairs, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    for line in lines:
        record = json.loads(line)
        assert set(record) == {"query", "positive_id", "hard_negatives", "schema_version"}
        assert isinstance(record["hard_negatives"], list)


def test_command_line_reads_query_log(tmp_path):
    db = DB(tmp_path / "kb.sqlite3")
    query_id = db.log_query(
        raw_query="khmer ocr",
        normalized_query="khmer ocr",
        intent="TOPIC_SEARCH",
        retrieved_json=[{"project_id": "p1", "rank": 1, "score": 0.03, "matched_by": "both"}],
    )
    db.clicked_project(query_id, "p1")
    db.close()
    out = tmp_path / "pairs.jsonl"
    assert main(["--db", str(tmp_path / "kb.sqlite3"), "--out", str(out)]) == 0
    record = json.loads(out.read_text(encoding="utf-8"))
    assert (record["query"], record["positive_id"]) == ("khmer ocr", "p1")
    assert record["hard_negatives"] == []
    assert main(["--db", str(tmp_path / "missing.sqlite3"), "--out", str(out)]) == 1
