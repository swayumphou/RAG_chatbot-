"""Ask the knowledge base from the terminal, without Telegram (demo fallback).

    python cli.py --query "khmer ocr"
    python cli.py --query "who built a Khmer OCR system" --llm

The CLI calls the same answer() function as the bot, so it shows the same
results. Errors (no index, no model, Qdrant down) print a short message,
not a traceback.
"""

from __future__ import annotations

import argparse
import sys

from answer.consent import CONSENT_DENIED
from answer.service import answer, log_result

LOW_CONFIDENCE = "(low confidence)"


def render(result) -> str:
    """Turn an AnswerResult into plain text."""
    lines = []
    if result.mode == "portfolio":
        lines += render_portfolio(result.portfolio)
        if result.answer:  # "Sok Dara's OCR project" with no matching project
            lines += ["", result.answer]
        if result.citations:
            lines += ["", "Matching projects:"]
            lines += render_citations(result.citations)
    elif result.mode == "ambiguous":
        lines.append("Several seniors match this name. Please be more specific:")
        for c in result.resolution.candidates:
            name, program, cohort = c.get("full_name_latin"), c.get("program"), c.get("cohort_year")
            lines.append(f"  - {name} — {program} {cohort}")
    elif result.mode == "not_found":
        lines.append(result.answer)
        if result.resolution and result.resolution.candidates:
            lines.append("Closest matches (not confirmed):")
            for c in result.resolution.candidates:
                lines.append(
                    f"  - {c.get('full_name_latin')} — {c.get('program')} "
                    f"{c.get('cohort_year')} (similarity {c['score']:.0f})"
                )
    elif result.mode == "abstain":
        lines.append(result.answer)
        for p in result.weak_matches:
            lines.append(f"  - {p.title} ({p.year}) {LOW_CONFIDENCE}")
    else:
        if result.route == "FACET_BROWSE":
            lines.append(f"{result.facet_counts.get('total', 0)} projects")
        if result.mode == "llm":
            lines += [result.answer, "", "Sources:"]
        lines += render_citations(result.citations)
    return "\n".join(lines)


def render_citations(citations) -> list[str]:
    lines = []
    for number, c in enumerate(citations, start=1):
        snippet = c.snippet[:400] + ("…" if len(c.snippet) > 400 else "")
        lines.append(f"{number}. {c.title}")
        lines.append(f"   {', '.join(c.authors)} · {c.year} · {c.program} · sup. {c.supervisor_id}")
        lines.append(f'   "{snippet}"')
        lines.append(f"   [Project: {c.title}, p.{c.page_number}]   matched: {c.matched_by}")
    return lines


def render_portfolio(p) -> list[str]:
    s = p.senior
    name = s["full_name_latin"] + (f"  ·  {s['full_name_khmer']}" if s["full_name_khmer"] else "")
    lines = [name, f"{s['program']} · Cohort {s['cohort_year']} · Supervisor {s['supervisor_id']}"]
    if p.metadata_only:
        lines.append("(metadata only: no documents are indexed for this senior yet)")
    lines.append(f"Projects ({len(p.projects)})")
    for number, project in enumerate(p.projects, start=1):
        lines.append(f"  {number}. {project['title_en']} ({project['year']})")
    if s.get("contact"):
        lines.append("Connect: " + " · ".join(s["contact"].values()))
    else:
        lines.append(CONSENT_DENIED)
    return lines


def main(argv=None, answer_fn=None, resources=None) -> int:
    """`answer_fn` and `resources` let tests run the CLI without real services."""
    parser = argparse.ArgumentParser(description="Search past RUPP senior projects.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--llm", action="store_true", help="let Gemma write the answer")
    parser.add_argument("--db", default=None, help="SQLite file (default data/kb.sqlite3)")
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-path", default=None, help="embedded Qdrant (smoke tests)")
    args = parser.parse_args(argv)

    try:
        client, embedder, db, threshold = resources or open_resources(args)
        run = answer_fn or answer
        result = run(args.query, client, embedder, db, threshold, enable_llm=args.llm)
    except Exception as error:  # show a readable message, never a traceback
        print(f"Error: {error}", file=sys.stderr)
        return 1
    log_result(db, result, surface="cli")  # never fails the command (T6.4)
    print(render(result))
    return 0


def open_resources(args):
    """Connect to SQLite, Qdrant, and the embedding model. Fails with a clear message."""
    from config import get_config
    from ingestion.embedder import Embedder
    from ingestion.indexer import connect_qdrant, count_points
    from store.db import DB, DEFAULT_DB_PATH

    config = get_config()
    db = DB(args.db or DEFAULT_DB_PATH)
    client = connect_qdrant(args.qdrant_url, args.qdrant_path)
    if count_points(client) == 0:
        raise RuntimeError(
            "the search index is empty. Run: python -m ingestion.run --projects "
            "data/projects.csv --seniors data/seniors.csv --corpus data/corpus"
        )
    embedder = Embedder(config.embed_model)
    embedder.load()
    return client, embedder, db, config.abstain_threshold


if __name__ == "__main__":
    sys.exit(main())
