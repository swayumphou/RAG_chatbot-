"""Tests for hybrid retrieval and project grouping (T3.3). No model and no Qdrant server."""

from types import SimpleNamespace

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("langchain_core")

from qdrant_client import models  # noqa: E402

from retrieval.hybrid import group_by_project, hybrid_retrieve  # noqa: E402


def point(project_id, score, page=1, vector=None):
    """A fake Qdrant point with the payload fields hybrid.py reads."""
    payload = {
        "project_id": project_id,
        "title_en": f"Title {project_id}",
        "senior_ids": ["s-1"],
        "year": 2024,
        "program": "DSE",
        "supervisor_id": "sup-001",
        "page_number": page,
        "text": f"[Project: Title {project_id} | p.{page}]\nBody of {project_id}",
    }
    return SimpleNamespace(payload=payload, score=score, vector=vector)


# ---------------------------------------------------------------- grouping


def test_one_project_repeated_five_times_gives_one_result():
    points = [point("p1", 0.9 - i * 0.1, page=i + 1) for i in range(5)]
    results = group_by_project(points)
    assert [r.project_id for r in results] == ["p1"]
    assert results[0].page_number == 1  # the best chunk is kept
    assert results[0].snippet == "Body of p1"  # breadcrumb removed


def test_top_five_has_five_distinct_projects():
    points = []
    for number in range(7):
        points.append(point(f"p{number}", 1.0 - number * 0.1))
        points.append(point(f"p{number}", 0.95 - number * 0.1, page=2))  # a second chunk
    results = group_by_project(points, top_k=5)
    ids = [r.project_id for r in results]
    assert len(ids) == 5
    assert len(set(ids)) == 5
    assert ids == ["p0", "p1", "p2", "p3", "p4"]


def test_weak_duplicate_does_not_displace_a_strong_unique_project():
    # Project A has many weak chunks; project B has one strong chunk.
    points = [point("A", 0.50), point("A", 0.40), point("A", 0.30), point("B", 0.45)]
    results = group_by_project(points, top_k=2)
    assert [r.project_id for r in results] == ["A", "B"]


# ---------------------------------------------------------------- hybrid_retrieve


class FakeEmbedder:
    def encode_query(self, text):
        return [1.0, 0.0], ([7], [1.0])


class FakeClient:
    """Returns prepared dense, lexical, and fused lists, and records each call."""

    def __init__(self, dense, lexical, fused):
        self.lists = {"dense": dense, "lexical": lexical, "fused": fused}
        self.calls = []

    def query_points(self, collection_name, **kwargs):
        self.calls.append(kwargs)
        kind = "fused" if kwargs.get("prefetch") else kwargs["using"]
        return SimpleNamespace(points=self.lists[kind])


def make_client():
    dense = [point("both", 0.80), point("dense_only", 0.70)]
    lexical = [point("both", 12.0), point("lexical_only", 9.0)]
    fused = [
        point("both", 0.033, vector={"dense": [0.8, 0.6]}),
        point("dense_only", 0.016, vector={"dense": [0.7, 0.51**0.5]}),  # cosine 0.70
        point("lexical_only", 0.015, vector={"dense": [0.3, 0.95]}),
    ]
    return FakeClient(dense, lexical, fused)


def test_matched_by_for_dense_lexical_and_both():
    results = hybrid_retrieve(make_client(), FakeEmbedder(), "khmer ocr")
    matched = {r.project_id: r.matched_by for r in results}
    assert matched == {"both": "both", "dense_only": "dense", "lexical_only": "lexical"}


def test_dense_score_is_present_for_the_gate():
    results = {r.project_id: r for r in hybrid_retrieve(make_client(), FakeEmbedder(), "ocr")}
    assert results["both"].dense_score == pytest.approx(0.80)
    assert results["dense_only"].dense_score == pytest.approx(0.70)
    # Found only by keywords, but its dense cosine is still computed from its vector.
    lexical_only = results["lexical_only"].dense_score
    assert lexical_only == pytest.approx(0.3 / (0.3**2 + 0.95**2) ** 0.5)
    # The fused RRF score is kept separately, for ordering only.
    assert results["both"].score == pytest.approx(0.033)


def test_fused_query_uses_rrf_k60_and_two_prefetches():
    client = make_client()
    flt = models.Filter(
        must=[models.FieldCondition(key="year", match=models.MatchValue(value=2024))]
    )
    hybrid_retrieve(client, FakeEmbedder(), "ocr", flt=flt)
    fused_call = [c for c in client.calls if c.get("prefetch")][0]
    assert [p.using for p in fused_call["prefetch"]] == ["dense", "lexical"]
    assert [p.limit for p in fused_call["prefetch"]] == [40, 40]
    assert fused_call["query"].rrf.k == 60
    assert fused_call["limit"] == 10
    # The filter is passed INTO every Qdrant call, never applied afterwards.
    assert all(c["query_filter"] is flt for c in client.calls)


def test_empty_query_does_not_search():
    client = make_client()
    assert hybrid_retrieve(client, FakeEmbedder(), "   ") == []
    assert client.calls == []
