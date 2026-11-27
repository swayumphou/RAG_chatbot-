"""A senior's portfolio: who they are and all their projects, newest first.

It is built from SQLite only (no Qdrant), so it still works when the vector
database is down. Contact details appear only when the senior consented.
"""

from __future__ import annotations

from dataclasses import dataclass

from answer.consent import contact_card

PROJECT_FIELDS = (
    "project_id",
    "title_en",
    "title_khmer",
    "year",
    "program",
    "domain",
    "tech_stack",
    "repo_url",
    "demo_url",
    "supervisor_id",
    "abstract",
    "senior_ids",
    "document_count",
)


@dataclass(frozen=True)
class Portfolio:
    senior: dict  # public fields, plus "contact" only when the senior consented
    projects: list[dict]  # newest first
    metadata_only: bool  # True when the senior has no parsed documents at all


def portfolio(db, senior_id) -> Portfolio:
    """Build the portfolio. Raises LookupError for an unknown or withdrawn senior."""
    row = db.get_senior(senior_id)
    if row is None or row["status"] != "active":
        raise LookupError(f"no active senior with id {senior_id!r}")

    senior = {
        "senior_id": row["senior_id"],
        "full_name_latin": row["full_name_latin"],
        "full_name_khmer": row["full_name_khmer"],
        "program": row["program"],
        "cohort_year": row["cohort_year"],
        "supervisor_id": row["supervisor_id"],
    }
    contact = contact_card(row)  # None unless consent_public_contact = 1
    if contact:
        senior["contact"] = contact

    projects = []
    for project in db.projects_for_senior(senior_id):  # already newest first
        projects.append({key: project.get(key) for key in PROJECT_FIELDS})

    documents = sum(p["document_count"] or 0 for p in projects)
    return Portfolio(senior=senior, projects=projects, metadata_only=documents == 0)
