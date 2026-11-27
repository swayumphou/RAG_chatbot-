"""CI gate: compare a new evaluation report with the committed baseline.

    python -m eval.compare_baseline eval/baseline.json eval/last_report.json

Fails (exit 1) when:
- the golden set changed (different SHA-256);
- the embedding or LLM model tag changed;
- a required slice is missing from the report;
- any slice's nDCG@10 dropped by more than 0.02.
Passes with exit 0 otherwise. The baseline is never rewritten by this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.metrics import REQUIRED_SLICES

MAX_NDCG_DROP = 0.02


def load_report(path) -> dict:
    """Read a report or baseline JSON file. Raises ValueError with a readable message."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"{path} is empty (commit a baseline from an approved run first)")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error


def compare(baseline, report) -> tuple[list[str], list[str]]:
    """Return (failures, warnings). No failures means the gate passes."""
    failures = []
    warnings = []

    if baseline.get("golden_sha256") != report.get("golden_sha256"):
        failures.append("the golden set changed (SHA-256 differs from the baseline)")

    old_tags = baseline.get("model_tags", {})
    new_tags = report.get("model_tags", {})
    for tag in ("embed_model", "llm_model"):
        if old_tags.get(tag) != new_tags.get(tag):
            failures.append(f"{tag} changed: {old_tags.get(tag)} -> {new_tags.get(tag)}")
    if old_tags.get("qdrant_version") != new_tags.get("qdrant_version"):
        warnings.append(
            f"qdrant_version changed: {old_tags.get('qdrant_version')} -> "
            f"{new_tags.get('qdrant_version')}"
        )

    report_slices = report.get("slices", {})
    for slice_name in REQUIRED_SLICES:
        if slice_name not in report_slices:
            failures.append(f"required slice missing from the report: {slice_name}")

    for slice_name, old in baseline.get("slices", {}).items():
        new = report_slices.get(slice_name)
        if new is None:
            if slice_name not in REQUIRED_SLICES:
                failures.append(f"slice missing from the report: {slice_name}")
            continue
        old_ndcg, new_ndcg = old.get("ndcg_at_10"), new.get("ndcg_at_10")
        if old_ndcg is None or new_ndcg is None:
            continue  # this slice has no ranking to compare (for example negatives)
        drop = round(old_ndcg - new_ndcg, 6)  # round: 0.85 - 0.83 is 0.0200000000000000018
        if drop > MAX_NDCG_DROP:
            failures.append(
                f"{slice_name}: nDCG@10 dropped from {old_ndcg:.3f} to {new_ndcg:.3f} "
                f"(more than {MAX_NDCG_DROP})"
            )
    return failures, warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare an eval report with the baseline.")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)

    try:
        baseline = load_report(args.baseline)
        report = load_report(args.report)
    except (OSError, ValueError) as error:
        print(f"FAIL  {error}")
        return 1

    failures, warnings = compare(baseline, report)
    for message in warnings:
        print(f"WARN  {message}")
    for message in failures:
        print(f"FAIL  {message}")
    if failures:
        return 1
    print("PASS  the report matches the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
