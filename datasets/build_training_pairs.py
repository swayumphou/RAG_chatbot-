"""Export (query, positive, hard negatives) training pairs from query_log (T6.5).

    python -m datasets.build_training_pairs --db data/kb.sqlite3 --out datasets/training_pairs.jsonl

- Positive: the project the user clicked (Abstract or Portfolio) for that query.
- Hard negatives: the other projects that were shown, in rank order.
- Rows rated 👎 (rating = -1) are dropped; queries without a click are skipped.
- Output: one JSON object per line, sorted by created_at then query_id, so the
  same log always gives the same file:
    {"query": "...", "positive_id": "...", "hard_negatives": ["..."], "schema_version": 1}
- Only the query text and project ids are written: never a senior's contact data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_OUT = Path(__file__).resolve().parent / "training_pairs.jsonl"


def build_pairs(log_rows) -> list[dict]:
    """Turn query_log rows (dicts) into training pairs."""
    ordered = sorted(log_rows, key=lambda row: (row["created_at"], row["query_id"]))
    pairs = []
    for row in ordered:
        if row.get("rating") == -1:
            continue  # the user said the results were bad
        positive = row.get("clicked_project")
        if not positive:
            continue  # no click, no positive
        shown = json.loads(row.get("retrieved_json") or "[]")
        shown = sorted(shown, key=lambda item: item.get("rank", 0))
        hard_negatives = [item["project_id"] for item in shown if item["project_id"] != positive]
        pairs.append(
            {
                "query": row["raw_query"],
                "positive_id": positive,
                "hard_negatives": hard_negatives,
                "schema_version": SCHEMA_VERSION,
            }
        )
    return pairs


def write_jsonl(pairs, path):
    lines = [json.dumps(pair, ensure_ascii=False) for pair in pairs]
    Path(path).write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def main(argv=None) -> int:
    from store.db import DB, DEFAULT_DB_PATH

    parser = argparse.ArgumentParser(description="Export training pairs from query_log.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: {args.db} not found")
        return 1
    db = DB(args.db)
    pairs = build_pairs(db.query_log_rows())
    write_jsonl(pairs, args.out)
    print(f"{len(pairs)} training pairs -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
