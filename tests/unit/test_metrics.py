"""Tests for the golden set, metrics, query log, and baseline gate (T6.1, T6.2, T6.4, T6.6)."""

import csv
import json
import math
from pathlib import Path

import pytest

from eval.compare_baseline import compare
from eval.compare_baseline import main as compare_main
from eval.metrics import (
    REQUIRED_SLICES,
    abstention_precision,
    mrr_at_k,
    name_top1,
    ndcg_at_k,
    payload_integrity,
    recall_at_k,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "eval" / "golden_set.csv"
MANIFEST = ROOT / "tests" / "fixtures" / "manifest"
HEADER = ["query_id", "query", "slice", "expected_project_ids", "expected_senior_id", "notes"]
SLICE_COUNTS = {
    "topic_en": 25,
    "topic_km": 10,
    "name_latin": 10,
    "name_typo": 8,
    "name_khmer": 7,
    "facet": 10,
    "negative": 20,
}
NAME_SLICES = ("name_latin", "name_typo", "name_khmer")


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


# ---------------------------------------------------------------- T6.1 golden set


def test_golden_set_schema_and_slice_counts():
    header, rows = read_csv(GOLDEN)
    assert header == HEADER
    assert len(rows) == 90
    counts = {}
    for row in rows:
        counts[row["slice"]] = counts.get(row["slice"], 0) + 1
    assert counts == SLICE_COUNTS
    ids = [row["query_id"] for row in rows]
    assert len(ids) == len(set(ids))  # no duplicate query_id

    # The test subset uses the same header.
    sample_header, _ = read_csv(ROOT / "tests" / "fixtures" / "golden" / "sample.csv")
    assert sample_header == HEADER


def test_every_name_row_has_a_senior_and_every_negative_has_no_project():
    _, rows = read_csv(GOLDEN)
    for row in rows:
        if row["slice"] in NAME_SLICES:
            assert row["expected_senior_id"], row["query_id"]
        if row["slice"] == "negative":
            assert not row["expected_project_ids"] and not row["expected_senior_id"]
        else:
            assert row["expected_project_ids"], row["query_id"]


def test_every_expected_project_and_senior_exists_in_the_fixture_manifest():
    _, projects = read_csv(MANIFEST / "projects.csv")
    _, seniors = read_csv(MANIFEST / "seniors.csv")
    granted = {p["project_id"] for p in projects if p["permission"] == "granted"}
    active = {s["senior_id"] for s in seniors if s["status"] == "active"}
    _, rows = read_csv(GOLDEN)
    for row in rows:
        for project_id in filter(None, row["expected_project_ids"].split("|")):
            assert project_id in granted, (row["query_id"], project_id)
        if row["expected_senior_id"]:
            assert row["expected_senior_id"] in active, row["query_id"]


def test_name_queries_route_to_a_name_lookup(tmp_path):
    """The golden name queries must reach the name path, or name top-1 measures nothing."""
    from ingestion.validate_manifest import validate_manifest
    from retrieval.intent import route
    from store.db import DB

    corpus = tmp_path / "corpus"
    for number in range(1, 7):
        (corpus / f"dse2024-{number:03d}").mkdir(parents=True)
    report = validate_manifest(MANIFEST / "projects.csv", MANIFEST / "seniors.csv", corpus)
    db = DB(tmp_path / "kb.sqlite3")
    for senior in report.seniors:
        db.upsert_senior(senior)
    _, rows = read_csv(GOLDEN)
    for row in rows:
        if row["slice"] in NAME_SLICES:
            assert route(row["query"], db).intent == "SENIOR_LOOKUP", row["query"]
        if row["slice"] == "facet":
            assert route(row["query"], db).intent == "FACET_BROWSE", row["query"]
    db.close()


# ---------------------------------------------------------------- T6.2 metrics


def row(expected, retrieved, slice_name="topic_en", **extra):
    return {
        "slice": slice_name,
        "expected_project_ids": expected,
        "retrieved": retrieved,
        "expected_senior_id": None,
        "resolved_senior": None,
        "abstained": False,
        **extra,
    }


def test_hand_computed_recall_mrr_and_ndcg():
    rows = [
        row(["p1"], ["p1", "p2"]),  # rank 1
        row(["p1"], ["p2", "p1"]),  # rank 2
        row(["p1"], ["p2", "p3"]),  # not found
        row([], ["p9"], slice_name="negative"),  # a negative never counts as a positive
    ]
    assert recall_at_k(rows, 5) == pytest.approx(2 / 3)
    assert mrr_at_k(rows, 10) == pytest.approx((1 + 0.5 + 0) / 3)
    assert ndcg_at_k(rows, 10) == pytest.approx((1 + 1 / math.log2(3) + 0) / 3)

    # Two expected projects: DCG = 1 + 1/log2(4), IDCG = 1 + 1/log2(3).
    two = [row(["p1", "p2"], ["p1", "p3", "p2"])]
    assert ndcg_at_k(two, 10) == pytest.approx(1.5 / (1 + 1 / math.log2(3)))
    # A perfect ranking scores exactly 1.0.
    assert ndcg_at_k([row(["p1", "p2"], ["p2", "p1", "p3"])], 10) == 1.0
    # Recall@k only looks at the top k.
    assert recall_at_k([row(["p1"], ["a", "b", "c", "d", "e", "p1"])], 5) == 0.0


def test_empty_slices_return_none():
    assert recall_at_k([]) is None
    assert mrr_at_k([]) is None
    assert ndcg_at_k([]) is None
    assert name_top1([row(["p1"], ["p1"])]) is None  # no name query in the group
    assert abstention_precision([row(["p1"], ["p1"])]) is None  # no negative in the group
    assert payload_integrity([]) is None
    assert recall_at_k([row([], [], slice_name="negative")]) is None


def test_abstention_precision_name_top1_and_payload_integrity():
    negatives = [
        row([], [], slice_name="negative", abstained=True),
        row([], [], slice_name="negative", abstained=True),
        row([], [], slice_name="negative", abstained=True),
        row([], ["p1"], slice_name="negative", abstained=False),  # answered a negative: bad
    ]
    assert abstention_precision(negatives) == 0.75

    names = [
        row(["p1"], ["p1"], "name_latin", expected_senior_id="s1", resolved_senior="s1"),
        row(["p1"], ["p1"], "name_typo", expected_senior_id="s1", resolved_senior="s2"),
    ]
    assert name_top1(names) == 0.5
    assert payload_integrity([{"payload_ok": [True, False]}, {"payload_ok": [True]}]) == 2 / 3


# ---------------------------------------------------------------- T6.4 query log


def test_query_log_row_has_ranks_scores_and_no_raw_id(tmp_path):
    pytest.importorskip("qdrant_client")
    from answer.service import AnswerResult, log_result
    from retrieval.hybrid import RetrievedProject
    from store.db import DB

    found = RetrievedProject(
        project_id="dse2024-001",
        title="Khmer Handwritten Text Recognition with Tesseract",
        senior_ids=("s-2024-014",),
        year=2024,
        program="DSE",
        supervisor_id="sup-003",
        page_number=3,
        snippet="passage",
        score=0.03279,
        dense_score=0.8,
        matched_by="both",
    )
    result = AnswerResult(
        query_id="q-1",
        route="TOPIC_SEARCH",
        mode="extractive",
        answer=None,
        citations=[],
        projects=[found],
        portfolio=None,
        weak_matches=[],
        normalized_query="khmer ocr",
        intent="TOPIC_SEARCH",
        resolved_senior=None,
        latency_ms=120,
        query="Khmer OCR",
    )
    db = DB(tmp_path / "kb.sqlite3")
    assert log_result(db, result, surface="telegram", tg_user_hash="ab12cd34") is True
    [saved] = db.query_log_rows()
    assert saved["surface"] == "telegram" and saved["tg_user_hash"] == "ab12cd34"
    assert saved["raw_query"] == "Khmer OCR"
    assert (saved["intent"], saved["latency_ms"]) == ("TOPIC_SEARCH", 120)
    assert json.loads(saved["retrieved_json"]) == [
        {"project_id": "dse2024-001", "rank": 1, "score": 0.0328, "matched_by": "both"}
    ]
    assert db.clicked_project("q-1", "dse2024-001")  # a click is recorded on the same row
    assert db.query_log_rows()[0]["clicked_project"] == "dse2024-001"
    db.close()


def test_a_failed_log_write_never_breaks_the_answer(caplog):
    pytest.importorskip("qdrant_client")
    from answer.service import AnswerResult, log_result

    class BrokenDB:
        def log_query(self, **row):
            raise OSError("disk full")

    result = AnswerResult(
        query_id="q",
        route="TOPIC_SEARCH",
        mode="abstain",
        answer=None,
        citations=[],
        projects=[],
        portfolio=None,
        weak_matches=[],
        normalized_query="x",
        intent="TOPIC_SEARCH",
        resolved_senior=None,
        latency_ms=1,
    )
    assert log_result(BrokenDB(), result) is False  # no exception
    assert "query log write failed" in caplog.text


# ---------------------------------------------------------------- T6.6 baseline gate


def report(ndcg=0.85, sha="abc", embed="BAAI/bge-m3@1", llm="gemma4:12b-it-qat", drop=()):
    slices = {name: {"ndcg_at_10": ndcg} for name in REQUIRED_SLICES if name not in drop}
    slices["negative"] = {"ndcg_at_10": None}
    if "negative" in drop:
        del slices["negative"]
    return {
        "golden_sha256": sha,
        "model_tags": {"embed_model": embed, "llm_model": llm, "qdrant_version": "1.0"},
        "slices": slices,
    }


def test_compare_baseline_gate(tmp_path, capsys):
    baseline = report()
    assert compare(baseline, report())[0] == []  # a clean report passes
    assert compare(baseline, report(ndcg=0.83))[0] == []  # a 0.02 drop is still allowed

    failures = compare(baseline, report(ndcg=0.82))[0]  # a 0.03 drop fails
    assert any("nDCG@10 dropped" in message for message in failures)
    assert any("missing" in m for m in compare(baseline, report(drop=("topic_km",)))[0])
    assert any("golden set changed" in m for m in compare(baseline, report(sha="xyz"))[0])
    assert any("embed_model changed" in m for m in compare(baseline, report(embed="other"))[0])
    assert any("llm_model changed" in m for m in compare(baseline, report(llm="gemma4:27b"))[0])

    # The command line: exit 0 on pass, 1 on failure, 1 on an empty baseline file.
    good, bad, empty = tmp_path / "base.json", tmp_path / "bad.json", tmp_path / "empty.json"
    good.write_text(json.dumps(baseline), encoding="utf-8")
    bad.write_text(json.dumps(report(ndcg=0.5)), encoding="utf-8")
    empty.write_text("", encoding="utf-8")
    assert compare_main([str(good), str(good)]) == 0
    assert compare_main([str(good), str(bad)]) == 1
    assert compare_main([str(empty), str(good)]) == 1
    assert "is empty" in capsys.readouterr().out
