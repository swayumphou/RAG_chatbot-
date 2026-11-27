"""The evaluation runner with a fake service and the small golden set (T6.3)."""

import json
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from answer.service import AnswerResult  # noqa: E402
from eval.metrics import REQUIRED_SLICES  # noqa: E402
from eval.run_eval import GoldenSetError, load_golden, run, tag_warnings  # noqa: E402
from retrieval.hybrid import RetrievedProject  # noqa: E402
from retrieval.portfolio import Portfolio  # noqa: E402

pytestmark = pytest.mark.integration

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "sample.csv"
TAGS = {"embed_model": "BAAI/bge-m3@abc", "llm_model": "gemma4:12b-it-qat", "qdrant_version": "x"}


def project(project_id, dense_score):
    return RetrievedProject(
        project_id=project_id,
        title=f"Title {project_id}",
        senior_ids=("s-2024-014",),
        year=2024,
        program="DSE",
        supervisor_id="sup-003",
        page_number=1,
        snippet="passage",
        score=0.03,
        dense_score=dense_score,
        matched_by="both",
    )


def result(query, route, mode, projects=(), weak=(), senior=None, portfolio=None):
    return AnswerResult(
        query_id=f"id-{query}",
        route=route,
        mode=mode,
        answer=None,
        citations=[],
        projects=list(projects),
        portfolio=portfolio,
        weak_matches=list(weak),
        normalized_query=query,
        intent=route,
        resolved_senior=senior,
        latency_ms=5,
        query=query,
    )


class FakeService:
    """Answers the sample queries like answer() would, including the relevance gate."""

    def __init__(self):
        self.calls = 0

    def __call__(self, query, threshold):
        self.calls += 1
        sok_dara = Portfolio(
            {"senior_id": "s-2024-014"},
            [{"project_id": "dse2024-004"}, {"project_id": "dse2024-001"}],
            False,
        )
        if query in ("Sok Dara", "sokdaraa", "សុខ ដារា"):
            senior = "s-2024-014"
            return result(query, "SENIOR_LOOKUP", "portfolio", senior=senior, portfolio=sok_dara)
        if query == "ITE 2024":
            return result(query, "FACET_BROWSE", "extractive", [project("dse2024-003", 0.5)])
        scores = {
            "khmer handwriting recognition": ("dse2024-001", 0.80),
            "ការសម្គាល់អក្សរខ្មែរ": ("dse2024-001", 0.60),
            "quantum computing lab schedule": ("dse2024-003", 0.40),  # a negative
        }
        project_id, score = scores[query]
        found = [project(project_id, score)]
        if score < threshold:  # the relevance gate
            return result(query, "TOPIC_SEARCH", "abstain", weak=found)
        return result(query, "TOPIC_SEARCH", "extractive", found)


def test_runner_sweeps_chooses_and_reports():
    service = FakeService()
    report = run(SAMPLE, service, threshold=0.45, do_sweep=True, model_tags=TAGS)

    # The smallest threshold where the negative (top score 0.40) abstains
    # while both topic queries (0.80 and 0.60) still answer.
    assert report["threshold"] == 0.41
    sweep = {line["threshold"]: line for line in report["sweep"]}
    assert sweep[0.40]["abstention_precision"] == 0.0
    assert sweep[0.41]["abstention_precision"] == 1.0
    assert sweep[0.61]["topic_recall_at_5"] == 0.5  # the Khmer query would abstain

    # Every slice and every metric is in the report.
    assert set(REQUIRED_SLICES) <= set(report["slices"])
    expected_metrics = {
        "n",
        "recall_at_5",
        "mrr_at_10",
        "ndcg_at_10",
        "name_top1",
        "abstention_precision",
        "payload_integrity",
    }
    for metrics in report["slices"].values():
        assert expected_metrics <= set(metrics)
    assert report["slices"]["topic_en"]["recall_at_5"] == 1.0
    assert report["slices"]["name_typo"]["name_top1"] == 1.0
    assert report["slices"]["negative"]["abstention_precision"] == 1.0
    assert len(report["golden_sha256"]) == 64
    assert report["model_tags"] == TAGS
    assert len(report["queries"]) == 7
    first = report["queries"][0]
    assert {"route", "mode", "top_score", "rank"} <= set(first)
    json.dumps(report)  # the report can be written as JSON
    assert service.calls == 14  # one sweep pass + one final pass over 7 queries


def test_changed_model_tag_gives_an_explicit_warning(tmp_path):
    report = run(SAMPLE, FakeService(), threshold=0.45, model_tags=TAGS)
    baseline = tmp_path / "baseline.json"
    old_tags = {**TAGS, "embed_model": "old"}
    baseline.write_text(json.dumps({"model_tags": old_tags}), encoding="utf-8")
    assert tag_warnings(report, baseline) == [
        "WARNING: embed_model is BAAI/bge-m3@abc, the baseline used old"
    ]
    baseline.write_text("", encoding="utf-8")  # no baseline yet: nothing to compare
    assert tag_warnings(report, baseline) == []


def test_a_missing_slice_is_an_error(tmp_path):
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    without_negative = [line for line in lines if ",negative," not in line]
    golden = tmp_path / "golden.csv"
    golden.write_text("\n".join(without_negative) + "\n", encoding="utf-8")
    with pytest.raises(GoldenSetError, match="negative"):
        load_golden(golden)
