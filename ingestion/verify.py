"""Check the index after ingestion. These checks are code, not discipline.

1. number of points == number of chunks we expected
2. every point has the required payload keys
3. every senior_id in a payload exists and is active
4. every project we expected has at least one point

Run it:
    python -m ingestion.verify [--db data/kb.sqlite3]
"""

from __future__ import annotations

import argparse
import sys

from ingestion.indexer import collection_name, connect_qdrant, count_points, scroll_all
from store.db import DB, DEFAULT_DB_PATH

# Every point in Qdrant must carry these payload keys.
REQUIRED_KEYS = (
    "project_id",
    "senior_ids",
    "doc_id",
    "doc_type",
    "page_number",
    "year",
    "program",
    "text",
)


class IntegrityError(Exception):
    pass


def assert_run_integrity(client, db, expected_points, project_ids, collection=None):
    """Raise IntegrityError listing every problem (each names its project)."""
    name = collection_name(collection)
    problems = []

    count = count_points(client, name)
    if count != expected_points:
        problems.append(f"point count {count} != expected {expected_points}")

    withdrawn = db.withdrawn_senior_ids()
    seen_projects = set()
    for point in scroll_all(client, name):
        payload = point.payload or {}
        project_id = payload.get("project_id", "?")
        seen_projects.add(project_id)
        missing = [key for key in REQUIRED_KEYS if key not in payload]
        if missing:
            problems.append(f"{project_id}: point {point.id} is missing {missing}")
        for senior_id in payload.get("senior_ids", []):
            if not db.senior_exists(senior_id):
                problems.append(f"{project_id}: unknown senior_id {senior_id}")
            elif senior_id in withdrawn:
                problems.append(f"{project_id}: senior {senior_id} is withdrawn but still indexed")

    for project_id in project_ids:
        if project_id not in seen_projects:
            problems.append(f"{project_id}: produced zero indexed chunks")

    if problems:
        unique = list(dict.fromkeys(problems))
        raise IntegrityError("Index check failed:\n- " + "\n- ".join(unique[:50]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify the Qdrant index against SQLite.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-path", default=None, help="embedded Qdrant (smoke tests)")
    args = parser.parse_args(argv)

    db = DB(args.db)
    counts = db.indexed_chunk_counts()
    client = connect_qdrant(args.qdrant_url, args.qdrant_path)
    try:
        assert_run_integrity(client, db, sum(counts.values()), list(counts))
    except IntegrityError as error:
        print(error)
        return 1
    print(f"OK: {sum(counts.values())} points across {len(counts)} projects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
