"""Find one senior from a typed name: "Sok Dara", "សុខ ដារា", "sok daraa".

Names are matched by text, never by vector similarity. Three steps, in order:

  1. exact alias in SQLite          -> resolved, confidence 1.00
  2. Qdrant full-text author index  -> exactly one senior: confidence 0.90
  3. rapidfuzz token_set_ratio >= 88 -> exactly one senior: confidence score/100

Two or more seniors match -> "ambiguous": the bot asks the user to choose.
We NEVER pick one ourselves; showing the wrong person's portfolio is a false
statement about a real student.
Nothing matches -> "not_found", with up to 3 closest names marked "not confirmed".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from qdrant_client import models

from config import FUZZY_NAME_THRESHOLD
from ingestion.indexer import collection_name
from retrieval.intent import normalize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeniorResolution:
    status: str  # "resolved", "ambiguous", or "not_found"
    senior_id: str | None = None
    confidence: float = 0.0
    via: str | None = None  # "alias", "text_index", or "fuzzy"
    candidates: tuple[dict, ...] = ()


def candidate(db, senior_id, score, confirmed=True) -> dict:
    return {**db.senior_card(senior_id), "score": round(score, 1), "confirmed": confirmed}


def text_index_match(name, db, client, collection=None) -> str | None:
    """Step 2: search author names in Qdrant. Returns a senior_id only if exactly one fits."""
    if client is None:
        return None
    try:
        points, _ = client.scroll(
            collection_name(collection),
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="author_names_normalized", match=models.MatchText(text=name)
                    )
                ]
            ),
            limit=50,
            with_payload=["senior_ids", "author_names_normalized"],
        )
    except Exception as error:  # Qdrant down: skip this step, names still work
        logger.warning("text index lookup skipped: %s", error)
        return None

    # A project has several authors. Keep only the author whose own name
    # contains every word that was typed.
    typed_words = set(name.split())
    withdrawn = db.withdrawn_senior_ids()
    found = set()
    for point in points:
        senior_ids = point.payload.get("senior_ids", [])
        author_names = point.payload.get("author_names_normalized", "").split(" / ")
        for senior_id, author_name in zip(senior_ids, author_names, strict=False):
            if typed_words <= set(author_name.split()) and senior_id not in withdrawn:
                found.add(senior_id)
    return found.pop() if len(found) == 1 else None


def fuzzy_scores(name, db) -> list[tuple[str, float]]:
    """Best rapidfuzz score per senior, over their names and aliases, highest first."""
    from rapidfuzz import fuzz

    best = {}
    for senior_id, alias in db.alias_directory():
        score = fuzz.token_set_ratio(name, alias)
        best[senior_id] = max(best.get(senior_id, 0.0), score)
    return sorted(best.items(), key=lambda item: (-item[1], item[0]))


def resolve_senior(
    normalized, db, client=None, fuzzy_threshold=FUZZY_NAME_THRESHOLD, collection=None
) -> SeniorResolution:
    name = normalize(normalized)
    if not name:
        return SeniorResolution(status="not_found")

    # Step 1: exact alias.
    alias_hits = db.alias_matches(name)
    if len(alias_hits) == 1:
        return SeniorResolution("resolved", alias_hits[0], 1.0, "alias")
    if len(alias_hits) > 1:  # two people share this exact name
        return SeniorResolution(
            "ambiguous", candidates=tuple(candidate(db, sid, 100.0) for sid in alias_hits)
        )

    # Step 2: Qdrant full-text index.
    senior_id = text_index_match(name, db, client, collection)
    if senior_id:
        return SeniorResolution("resolved", senior_id, 0.90, "text_index")

    # Step 3: fuzzy match.
    scores = fuzzy_scores(name, db)
    good = [(sid, score) for sid, score in scores if score >= fuzzy_threshold]
    if len(good) == 1:
        sid, score = good[0]
        return SeniorResolution("resolved", sid, round(score / 100, 3), "fuzzy")
    if good:
        return SeniorResolution(
            "ambiguous", candidates=tuple(candidate(db, sid, score) for sid, score in good[:5])
        )
    closest = tuple(candidate(db, sid, score, confirmed=False) for sid, score in scores[:3])
    return SeniorResolution("not_found", candidates=closest)
