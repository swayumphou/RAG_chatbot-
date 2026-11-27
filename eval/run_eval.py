"""Run the golden set through answer() and report metrics per slice (T6.3).

    python -m eval.run_eval --golden eval/golden_set.csv --sweep-threshold

- Calls answer.service.answer() directly; Telegram is never in the path.
- Prints one row per slice: n, Recall@5, MRR@10, nDCG@10, name top-1,
  abstention precision.
- --sweep-threshold: the relevance gate only looks at each query's top dense
  score, so every query is run once with the gate open (threshold 0) and the
  outcome is worked out for thresholds 0.30-0.70. The chosen threshold is the
  smallest one with abstention precision >= 0.95 and topic Recall@5 >= 0.90.
  The final report is a real run at the chosen threshold.
- Writes eval/last_report.json (golden-set hash, model tags, threshold,
  per-slice metrics, per-query rows).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from eval.metrics import (
    REQUIRED_SLICES,
    abstention_precision,
    first_hit_rank,
    metrics_by_slice,
    recall_at_k,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "eval" / "golden_set.csv"
DEFAULT_REPORT = ROOT / "eval" / "last_report.json"
DEFAULT_BASELINE = ROOT / "eval" / "baseline.json"

GATED_ROUTES = ("TOPIC_SEARCH", "ANSWER")  # the routes that pass through the relevance gate
SWEEP = [round(0.30 + 0.01 * step, 2) for step in range(41)]  # 0.30 ... 0.70
TARGET_ABSTENTION = 0.95
TARGET_TOPIC_RECALL = 0.90


class GoldenSetError(ValueError):
    pass


# ---------------------------------------------------------------- golden set


def load_golden(path) -> list[dict]:
    """Read the golden CSV. Raises GoldenSetError for a missing slice or a duplicate id."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            expected = [p.strip() for p in (row["expected_project_ids"] or "").split("|")]
            rows.append(
                {
                    "query_id": row["query_id"].strip(),
                    "query": row["query"],
                    "slice": row["slice"].strip(),
                    "expected_project_ids": [p for p in expected if p],
                    "expected_senior_id": (row["expected_senior_id"] or "").strip() or None,
                }
            )
    ids = [row["query_id"] for row in rows]
    duplicates = sorted({query_id for query_id in ids if ids.count(query_id) > 1})
    if duplicates:
        raise GoldenSetError(f"duplicate query_id: {', '.join(duplicates)}")
    missing = [name for name in REQUIRED_SLICES if name not in {row["slice"] for row in rows}]
    if missing:
        raise GoldenSetError(f"golden set is missing slices: {', '.join(missing)}")
    return rows


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- evaluation


def payload_ok(project) -> bool:
    """A retrieved project carries the payload the cards and citations need."""
    has_ids = bool(project.project_id and project.title and project.senior_ids)
    return has_ids and isinstance(project.page_number, int)


def to_metric_row(golden, result) -> dict:
    """One golden query + its AnswerResult -> one row for eval/metrics.py."""
    if result.mode in ("abstain", "ambiguous", "not_found"):
        retrieved = []
    elif result.projects:
        retrieved = [p.project_id for p in result.projects]
    elif result.portfolio is not None:
        retrieved = [p["project_id"] for p in result.portfolio.projects]
    else:
        retrieved = []
    candidates = result.projects or result.weak_matches
    row = {
        **golden,
        "retrieved": retrieved,
        "resolved_senior": result.resolved_senior,
        "abstained": result.mode == "abstain",
        "payload_ok": [payload_ok(p) for p in result.projects],
        "route": result.route,
        "mode": result.mode,
        "top_score": candidates[0].dense_score if candidates else None,
    }
    row["rank"] = first_hit_rank(row, 10)
    return row


def evaluate(golden_rows, ask, threshold) -> list[dict]:
    """Ask every golden query once. `ask(query, threshold)` returns an AnswerResult."""
    return [to_metric_row(golden, ask(golden["query"], threshold)) for golden in golden_rows]


def gate_outcome(row, threshold) -> dict:
    """What the relevance gate would have done to this row at `threshold`."""
    if row["route"] not in GATED_ROUTES:
        return row
    if row["top_score"] is not None and row["top_score"] >= threshold:
        return row
    return {**row, "abstained": True, "retrieved": [], "payload_ok": []}


def sweep(rows) -> list[dict]:
    """Abstention precision and topic Recall@5 for every threshold in SWEEP."""
    table = []
    for threshold in SWEEP:
        gated = [gate_outcome(row, threshold) for row in rows]
        topic = [row for row in gated if row["slice"] in ("topic_en", "topic_km")]
        table.append(
            {
                "threshold": threshold,
                "abstention_precision": abstention_precision(gated),
                "topic_recall_at_5": recall_at_k(topic, 5),
            }
        )
    return table


def choose_threshold(table) -> float | None:
    """The smallest threshold meeting both targets, or None."""
    for line in table:
        abstention = line["abstention_precision"] or 0.0
        recall = line["topic_recall_at_5"] or 0.0
        if abstention >= TARGET_ABSTENTION and recall >= TARGET_TOPIC_RECALL:
            return line["threshold"]
    return None


def run(golden_path, ask, threshold, do_sweep=False, model_tags=None) -> dict:
    """The whole evaluation as a report dict (also used by the tests with a fake `ask`)."""
    golden = load_golden(golden_path)
    table = []
    chosen = threshold
    note = None
    if do_sweep:
        open_gate_rows = evaluate(golden, ask, 0.0)  # nothing abstains; every top score is seen
        table = sweep(open_gate_rows)
        best = choose_threshold(table)
        if best is None:
            note = f"no threshold met the targets; kept {threshold}"
        else:
            chosen = best
    rows = evaluate(golden, ask, chosen)  # the real run at the chosen threshold
    return {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "golden_path": str(golden_path),
        "golden_sha256": file_sha256(golden_path),
        "model_tags": model_tags or {},
        "threshold": chosen,
        "sweep": table,
        "sweep_note": note,
        "slices": metrics_by_slice(rows),
        "queries": rows,
    }


# ---------------------------------------------------------------- printing


def number(value) -> str:
    return "   -  " if value is None else f"{value:6.3f}"


def print_report(report):
    print(f"threshold {report['threshold']}   golden {report['golden_sha256'][:12]}")
    print("slice          n  Recall@5  MRR@10 nDCG@10  name@1 abstain")
    for name, m in report["slices"].items():
        print(
            f"{name:<12} {m['n']:>3}  {number(m['recall_at_5'])}  {number(m['mrr_at_10'])} "
            f"{number(m['ndcg_at_10'])}  {number(m['name_top1'])} "
            f"{number(m['abstention_precision'])}"
        )
    if report["sweep"]:
        print("\nthreshold  abstention  topic Recall@5")
        for line in report["sweep"]:
            chosen = "  <- chosen" if line["threshold"] == report["threshold"] else ""
            if round(line["threshold"] * 100) % 5 == 0 or chosen:
                print(
                    f"  {line['threshold']:.2f}     {number(line['abstention_precision'])}"
                    f"      {number(line['topic_recall_at_5'])}{chosen}"
                )
    if report.get("sweep_note"):
        print(f"\nNOTE: {report['sweep_note']}")


def tag_warnings(report, baseline_path) -> list[str]:
    """Model tags that differ from the committed baseline (numbers are then not comparable)."""
    try:
        text = Path(baseline_path).read_text(encoding="utf-8").strip()
        baseline = json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        baseline = {}
    old = baseline.get("model_tags", {})
    warnings = []
    for tag, value in report["model_tags"].items():
        if old.get(tag) is not None and old.get(tag) != value:
            warnings.append(f"WARNING: {tag} is {value}, the baseline used {old[tag]}")
    return warnings


# ---------------------------------------------------------------- command line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval on the golden set.")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--sweep-threshold", action="store_true")
    parser.add_argument("--threshold", type=float, default=None, help="default: config value")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--db", default=None, help="SQLite file (default data/kb.sqlite3)")
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-path", default=None, help="embedded Qdrant (smoke tests only)")
    args = parser.parse_args(argv)

    from answer.service import answer
    from config import get_config
    from ingestion.embedder import Embedder
    from ingestion.indexer import connect_qdrant
    from store.db import DB, DEFAULT_DB_PATH

    config = get_config()
    try:
        golden = load_golden(args.golden)  # fail early, before loading any model
    except (OSError, GoldenSetError) as error:
        print(f"ERROR: {error}")
        return 1
    print(f"{len(golden)} golden queries")

    db = DB(args.db or DEFAULT_DB_PATH)
    client = connect_qdrant(args.qdrant_url, args.qdrant_path)
    embedder = Embedder(config.embed_model)
    embedder.load()
    try:
        qdrant_version = client.info().version
    except Exception:
        qdrant_version = "embedded" if args.qdrant_path else "unknown"
    tags = {
        "embed_model": embedder.revision,
        "llm_model": config.ollama_model,
        "qdrant_version": qdrant_version,
    }

    def ask(query, threshold):
        return answer(query, client, embedder, db, threshold, enable_llm=False)

    threshold = config.abstain_threshold if args.threshold is None else args.threshold
    report = run(args.golden, ask, threshold, args.sweep_threshold, tags)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print_report(report)
    for warning in tag_warnings(report, args.baseline):
        print(warning)
    print(f"\nreport: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
