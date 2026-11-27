"""Run the whole ingestion pipeline and write a report.

    python -m ingestion.run --projects data/projects.csv --seniors data/seniors.csv \
        --corpus data/corpus [--rebuild]

Order:
  1. validate the manifest       (exit code 2 on errors, before Qdrant is touched)
  2. parse every document of every eligible project
  3. chunk
  4. embed + save into Qdrant, then delete stale points
  5. rebuild SQLite from the manifest (one transaction)
  6. verify the index
  7. write logs/ingest_<timestamp>.json

Only the paths given on the command line are read. data/reference/ is old
curriculum data and is never ingested.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from config import get_config
from ingestion.chunker import build_chunks
from ingestion.embedder import Embedder, EmbedderLoadError
from ingestion.indexer import (
    collection_name,
    connect_qdrant,
    count_points,
    delete_stale_points,
    ensure_collection,
    recreate_collection,
    upsert_chunks,
)
from ingestion.parsers import parse_document
from ingestion.validate_manifest import ValidationReport, project_documents, validate_manifest
from ingestion.verify import IntegrityError, assert_run_integrity
from logging_config import write_ingest_report
from store.db import DEFAULT_DB_PATH, DB

ROOT = Path(__file__).resolve().parent.parent


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def parse_project(project: dict, authors: list[dict], corpus_dir: Path):
    """Parse and chunk every document of one project.

    Returns (document rows for SQLite, chunks, report entry). A document that
    fails is reported and skipped; the run continues.
    """
    documents, chunks, doc_reports = [], [], []
    for path in project_documents(corpus_dir, project["project_id"]):
        try:
            parsed = parse_document(path, project)
            doc_chunks = build_chunks(parsed, project, authors)
        except Exception as error:  # one bad file must not stop the whole run
            doc_reports.append({"file": path.name, "parse_status": "failed", "error": str(error)})
            continue
        documents.append(
            {
                "doc_id": parsed.doc_id,
                "project_id": project["project_id"],
                "doc_type": parsed.doc_type,
                "file_path": path.relative_to(corpus_dir).as_posix(),
                "sha256": parsed.sha256,
                "page_count": parsed.page_count,
                "parser": parsed.parser,
                "parse_status": parsed.parse_status,
                "chunk_count": len(doc_chunks),
            }
        )
        doc_reports.append(
            {
                "doc_id": parsed.doc_id,
                "doc_type": parsed.doc_type,
                "parse_status": parsed.parse_status,
                "parser": parsed.parser,
                "page_count": parsed.page_count,
                "pages_with_text": len(parsed.pages),
                "chunk_count": len(doc_chunks),
            }
        )
        chunks.extend(doc_chunks)

    if chunks:
        status, reason = "ok", None
    elif not doc_reports:
        status, reason = "failed", "no documents (metadata-only project)"
    else:
        status, reason = "failed", "documents produced zero chunks"
    entry = {
        "project_id": project["project_id"],
        "status": status,
        "reason": reason,
        "chunks": len(chunks),
        "documents": doc_reports,
    }
    return documents, chunks, entry


def ingest(
    validation: ValidationReport, corpus_dir, db, client, embedder, rebuild=False, collection=None
) -> dict:
    """Steps 2-6. `validation` must be OK. Returns the run report (a dict)."""
    corpus_dir = Path(corpus_dir)
    name = collection_name(collection)
    report = {
        "started_at": now(),
        "status": "ok",
        "collection": name,
        "rebuild": rebuild,
        "corpus_hash": validation.corpus_hash,
        "validation": {
            "errors": validation.errors,
            "warnings": validation.warnings,
            "diff": validation.diff,
        },
        "projects": [],
        "errors": [],
    }

    # Steps 2 + 3: parse and chunk.
    seniors = {s["senior_id"]: s for s in validation.seniors}
    all_chunks, documents_by_project = [], {}
    for project in validation.projects:
        authors = [seniors[sid] for sid in project["senior_ids"]]
        documents, chunks, entry = parse_project(project, authors, corpus_dir)
        documents_by_project[project["project_id"]] = documents
        all_chunks.extend(chunks)
        report["projects"].append(entry)

    # Step 4: Qdrant.
    try:
        report["points_before"] = count_points(client, name)
        if rebuild:
            recreate_collection(client, name)
        else:
            ensure_collection(client, name)
        upsert_chunks(client, embedder, all_chunks, name)
        keep = {}
        for chunk in all_chunks:
            keep.setdefault(chunk.metadata["project_id"], set()).add(chunk.chunk_id)
        report["deleted_stale_points"] = delete_stale_points(client, keep, name)
        report["model_revision"] = embedder.revision
    except Exception as error:
        report["status"] = "failed"
        report["errors"].append(f"Qdrant step failed: {error}")
        report["finished_at"] = now()
        return report

    # Step 5: SQLite, all in one transaction.
    try:
        with db.transaction():
            for senior in validation.seniors:
                db.upsert_senior(senior)
            for project in validation.projects:
                db.upsert_project(project)
                db.replace_authorship(project["project_id"], project["senior_ids"])
                project_id = project["project_id"]
                db.replace_documents(project_id, documents_by_project[project_id])
            db.prune_projects([p["project_id"] for p in validation.projects])
            db.prune_seniors([s["senior_id"] for s in validation.seniors])
    except Exception as error:
        report["status"] = "failed"
        report["errors"].append(f"SQLite rebuild failed (re-run ingestion to repair): {error}")
        report["finished_at"] = now()
        return report

    # Step 6: verify.
    indexed = sorted({c.metadata["project_id"] for c in all_chunks})
    try:
        assert_run_integrity(client, db, len(all_chunks), indexed, name)
    except IntegrityError as error:
        report["status"] = "failed"
        report["errors"].append(str(error))

    report["points_after"] = count_points(client, name)
    report["new_points"] = report["points_after"] - report["points_before"]
    report["chunk_count"] = len(all_chunks)
    report["finished_at"] = now()
    return report


def run_ingestion(
    projects_csv,
    seniors_csv,
    corpus_dir,
    db,
    client,
    embedder,
    rebuild=False,
    collection=None,
    log_dir=None,
) -> dict:
    """Validate, then ingest, then write the JSON report. Used by tests and main()."""
    validation = validate_manifest(projects_csv, seniors_csv, corpus_dir, db.manifest_snapshot())
    if not validation.ok:
        report = {
            "started_at": now(),
            "status": "invalid",
            "corpus_hash": validation.corpus_hash,
            "validation": {"errors": validation.errors, "warnings": validation.warnings},
        }
    else:
        report = ingest(validation, corpus_dir, db, client, embedder, rebuild, collection)
    report["report_path"] = str(write_ingest_report(report, log_dir=log_dir))
    return report


def print_summary(report: dict):
    for entry in report.get("projects", []):
        reason = f" ({entry['reason']})" if entry.get("reason") else ""
        print(f"{entry['project_id']:<20} {entry['status']:<7} chunks={entry['chunks']}{reason}")
    for message in report.get("validation", {}).get("errors", []):
        print("ERROR  ", message)
    for message in report.get("errors", []):
        print("ERROR  ", message)
    print(
        f"status={report['status']} points={report.get('points_after', 0)} "
        f"new={report.get('new_points', 0)} model={report.get('model_revision', '-')}"
    )
    print("report:", report.get("report_path"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ingest the senior-project corpus.")
    parser.add_argument("--projects", type=Path, default=ROOT / "data" / "projects.csv")
    parser.add_argument("--seniors", type=Path, default=ROOT / "data" / "seniors.csv")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--rebuild", action="store_true", help="delete and recreate the collection")
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-path", default=None, help="embedded Qdrant (smoke tests only)")
    args = parser.parse_args(argv)

    db = DB(args.db)
    validation = validate_manifest(args.projects, args.seniors, args.corpus, db.manifest_snapshot())
    if not validation.ok:
        for message in validation.errors:
            print("ERROR  ", message)
        write_ingest_report({"status": "invalid", "validation": validation.to_dict()})
        print("Validation failed; nothing was written to Qdrant or SQLite.")
        return 2

    config = get_config()
    client = connect_qdrant(args.qdrant_url, args.qdrant_path)
    embedder = Embedder(config.embed_model)
    try:
        embedder.load()
    except EmbedderLoadError as error:
        print(error)
        return 1

    report = ingest(validation, args.corpus, db, client, embedder, args.rebuild)
    report["report_path"] = str(write_ingest_report(report))
    print_summary(report)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
