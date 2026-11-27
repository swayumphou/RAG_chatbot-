"""Retrieval-quality metrics (Architecture §13.2), computed from plain dicts.

One evaluated query is one dict ("row"):

    {
        "query_id": "t01",
        "slice": "topic_en",
        "expected_project_ids": ["dse2024-001"],   # [] for negatives
        "expected_senior_id": "s-2024-014",       # None when not a name query
        "retrieved": ["dse2024-004", "dse2024-001"],  # project ids, best first
        "resolved_senior": "s-2024-014",          # None when no senior was resolved
        "abstained": False,
        "payload_ok": [True, True],               # one per retrieved project
    }

Every metric returns None for an empty group, never a division by zero.
Negatives (no expected project) are never counted as positives.
"""

from __future__ import annotations

import math

REQUIRED_SLICES = (
    "topic_en",
    "topic_km",
    "name_latin",
    "name_typo",
    "name_khmer",
    "facet",
    "negative",
)


def first_hit_rank(row, k) -> int | None:
    """1-based rank of the first expected project within the top k, or None."""
    expected = set(row["expected_project_ids"])
    for rank, project_id in enumerate(row["retrieved"][:k], start=1):
        if project_id in expected:
            return rank
    return None


def with_expected_projects(rows) -> list:
    return [row for row in rows if row["expected_project_ids"]]


def mean(values) -> float | None:
    values = list(values)
    if not values:
        return None
    return sum(values) / len(values)


def recall_at_k(rows, k=5) -> float | None:
    """Share of queries with at least one expected project in the top k."""
    return mean(first_hit_rank(row, k) is not None for row in with_expected_projects(rows))


def mrr_at_k(rows, k=10) -> float | None:
    """Mean of 1/rank of the first expected project (0 when it is not in the top k)."""
    scores = []
    for row in with_expected_projects(rows):
        rank = first_hit_rank(row, k)
        scores.append(1 / rank if rank else 0.0)
    return mean(scores)


def ndcg_at_k(rows, k=10) -> float | None:
    """nDCG with binary gains: each expected project counts 1, DCG = Σ rel_i / log2(i + 1)."""
    scores = []
    for row in with_expected_projects(rows):
        expected = set(row["expected_project_ids"])
        dcg = 0.0
        for rank, project_id in enumerate(row["retrieved"][:k], start=1):
            if project_id in expected:
                dcg += 1 / math.log2(rank + 1)
        ideal_hits = min(len(expected), k)  # the best ordering puts every expected project first
        idcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
        scores.append(dcg / idcg)
    return mean(scores)


def name_top1(rows) -> float | None:
    """Share of name queries whose resolved senior is the expected one."""
    name_rows = [row for row in rows if row.get("expected_senior_id")]
    return mean(row.get("resolved_senior") == row["expected_senior_id"] for row in name_rows)


def abstention_precision(rows) -> float | None:
    """Share of negative queries that abstained."""
    negatives = [row for row in rows if row["slice"] == "negative"]
    return mean(bool(row["abstained"]) for row in negatives)


def payload_integrity(rows) -> float | None:
    """Share of retrieved projects whose payload had every required key."""
    checks = [ok for row in rows for ok in row.get("payload_ok", [])]
    return mean(checks)


def slice_metrics(rows) -> dict:
    """All metrics for one group of rows."""
    return {
        "n": len(rows),
        "recall_at_5": recall_at_k(rows, 5),
        "mrr_at_10": mrr_at_k(rows, 10),
        "ndcg_at_10": ndcg_at_k(rows, 10),
        "name_top1": name_top1(rows),
        "abstention_precision": abstention_precision(rows),
        "payload_integrity": payload_integrity(rows),
    }


def metrics_by_slice(rows) -> dict:
    """{slice: metrics} for every slice present, plus "all"."""
    result = {}
    for slice_name in sorted({row["slice"] for row in rows}):
        result[slice_name] = slice_metrics([row for row in rows if row["slice"] == slice_name])
    result["all"] = slice_metrics(rows)
    return result
