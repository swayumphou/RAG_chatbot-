"""answer() — THE single entry point for the bot, the CLI, and the evaluator.

    result = answer("khmer ocr", client, embedder, db)

It decides the intent, runs the right search, applies the relevance gate, and
returns an AnswerResult. It never sends Telegram messages and never builds
HTML: formatting is the caller's job. Because the evaluator calls this same
function, the scores in the report describe what students really see.

Dispatch (task T4.1):
  SENIOR_LOOKUP / PORTFOLIO -> resolve the name -> portfolio (or ambiguous / not_found)
  FACET_BROWSE              -> facet filter + hybrid search + SQLite facet counts
  TOPIC_SEARCH / ANSWER     -> hybrid search -> gate -> extractive (or Gemma for ANSWER)
  "Sok Dara's OCR project"  -> multi-intent: the senior's portfolio + the topic inside it

Modes of the result:
  extractive          real passages with [Project: title, p.N] citations (default)
  llm                 Gemma wrote the answer and every citation was checked
  extractive_fallback Gemma failed or cited something invented -> passages instead
  abstain             nothing relevant enough; the LLM was NOT called
  portfolio           one senior's portfolio
  project             one project by ID, from SQLite (bot Abstract button / deep link)
  ambiguous           several seniors match the name; the user must choose
  not_found           no senior (or project) with that name or ID
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field, replace

from qdrant_client import models

from answer.prompts import LLM_NO_ANSWER, LLMError, synthesize
from answer.validate import validate_citations
from config import ABSTAIN_THRESHOLD
from logging_config import counters, log_answer_validation
from retrieval.entity import SeniorResolution, resolve_senior
from retrieval.filters import build_facet_filter, facet_counts
from retrieval.hybrid import RetrievedProject, hybrid_retrieve
from retrieval.intent import decompose_multi_intent, route
from retrieval.portfolio import Portfolio, portfolio
from store.db import as_list

logger = logging.getLogger(__name__)

ABSTAIN = (
    "No indexed project covers this question. Try a topic keyword, "
    "a senior's name, or a year filter."
)
NOT_FOUND = "No senior with that name in the indexed cohort."
PROJECT_NOT_FOUND = "No indexed project with that ID."

INTENTS = ("TOPIC_SEARCH", "SENIOR_LOOKUP", "PORTFOLIO", "FACET_BROWSE", "ANSWER")


@dataclass(frozen=True)
class Citation:
    project_id: str
    title: str
    senior_ids: tuple[str, ...]
    page_number: int
    snippet: str
    matched_by: str
    score: float
    # Display fields for the project card (T5.3). From SQLite; never contact data.
    authors: tuple[str, ...] = ()
    year: int = 0
    program: str = ""
    supervisor_id: str = ""
    repo_url: str | None = None


@dataclass
class AnswerResult:
    query_id: str
    route: str
    mode: str
    answer: str | None
    citations: list[Citation]
    projects: list[RetrievedProject]
    portfolio: Portfolio | None
    weak_matches: list[RetrievedProject]
    normalized_query: str
    intent: str
    resolved_senior: str | None
    latency_ms: int
    # Additions (see INTERFACES.md §5.3 notes):
    query: str = ""  # the raw query, for the LLM prompt and the query log
    resolution: SeniorResolution | None = None  # candidates for ambiguous / not_found
    facets: dict = field(default_factory=dict)  # FACET_BROWSE: the active filters
    facet_counts: dict = field(default_factory=dict)  # FACET_BROWSE: SQLite GROUP BY counts

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Context:
    """Everything a search needs, passed around as one object."""

    client: object
    embedder: object
    db: object
    threshold: float
    enable_llm: bool = False
    collection: str | None = None


# ---------------------------------------------------------------- helpers


def make_citation(project: RetrievedProject, db) -> Citation:
    row = db.get_project(project.project_id) or {}
    return Citation(
        project_id=project.project_id,
        title=project.title,
        senior_ids=project.senior_ids,
        page_number=project.page_number,
        snippet=project.snippet,
        matched_by=project.matched_by,
        score=round(project.score, 4),
        authors=tuple(db.senior_names(project.senior_ids)),
        year=project.year,
        program=project.program,
        supervisor_id=project.supervisor_id,
        repo_url=row.get("repo_url"),
    )


def make_citations(projects, db) -> list[Citation]:
    """One citation per (project, page), in the fused order."""
    citations = []
    seen = set()
    for project in projects:
        key = (project.project_id, project.page_number)
        if key not in seen:
            seen.add(key)
            citations.append(make_citation(project, db))
    return citations


def abstain(result: AnswerResult, candidates) -> AnswerResult:
    """Nothing is relevant enough. Keep up to 3 weak matches, shown as low confidence."""
    result.mode = "abstain"
    result.answer = ABSTAIN
    result.weak_matches = list(candidates)[:3]
    result.projects = []
    result.citations = []
    return result


def search(ctx: Context, text, flt=None) -> list[RetrievedProject]:
    """Hybrid search. If Qdrant is down, RetrievalUnavailable goes up to the caller."""
    return hybrid_retrieve(ctx.client, ctx.embedder, text, flt=flt, collection=ctx.collection)


# ---------------------------------------------------------------- paths


def search_path(result, routed, ctx: Context):
    """TOPIC_SEARCH and ANSWER: hybrid search -> gate -> extractive or LLM."""
    projects = search(ctx, routed.lexical)

    # The gate uses the top project's dense cosine, never the RRF score.
    top_score = projects[0].dense_score if projects else None
    logger.info("gate top_dense=%s threshold=%s", top_score, ctx.threshold)
    if top_score is None or top_score < ctx.threshold:
        return abstain(result, projects)  # the LLM is never called below the gate

    result.mode = "extractive"
    result.projects = projects
    result.citations = make_citations(projects, ctx.db)
    if ctx.enable_llm and routed.intent == "ANSWER":
        try_llm(result)
    return result


def try_llm(result: AnswerResult):
    """Let Gemma write the answer. Any problem -> keep the extractive answer."""
    try:
        draft = synthesize(result.query, result.citations)
    except LLMError as error:
        result.mode = "extractive_fallback"
        log_answer_validation("extractive_fallback", reason=str(error), query_id=result.query_id)
        return

    if draft.startswith(LLM_NO_ANSWER):
        counters.record_llm_attempt()
        abstain(result, result.projects)
        return
    if validate_citations(draft, result.citations):
        counters.record_llm_attempt()
        result.mode = "llm"
        result.answer = draft
        return
    counters.record_llm_attempt(rejected=True)
    result.mode = "extractive_fallback"
    log_answer_validation(
        "citation_rejected", reason="missing or invented citation", query_id=result.query_id
    )


def name_path(result, routed, ctx: Context):
    """SENIOR_LOOKUP and PORTFOLIO: resolve the name, then show the portfolio."""
    name = routed.name_span or routed.normalized
    resolution = resolve_senior(name, ctx.db, ctx.client, collection=ctx.collection)
    result.resolution = resolution
    if resolution.status == "ambiguous":
        result.mode = "ambiguous"  # the user must choose; we never pick one
        return result
    if resolution.status == "not_found":
        result.mode = "not_found"  # resolution.candidates: up to 3 "not confirmed" names
        result.answer = NOT_FOUND
        return result

    result.mode = "portfolio"
    result.resolved_senior = resolution.senior_id
    result.portfolio = portfolio(ctx.db, resolution.senior_id)
    return result


def facet_path(result, routed, ctx: Context, facets):
    """FACET_BROWSE: filtered hybrid search in Qdrant + corpus counts from SQLite GROUP BY."""
    facets = {key: value for key, value in facets.items() if as_list(value)}
    result.route = result.intent = "FACET_BROWSE"
    result.mode = "extractive"
    result.facets = facets
    result.facet_counts = facet_counts(ctx.db, facets)
    # The filter goes INTO the search, so Qdrant filters before ranking.
    # A facet chip has no typed text, so the facet values are the search text.
    words = [str(v) for value in facets.values() for v in as_list(value)]
    text = routed.lexical or " ".join(words)
    result.projects = search(ctx, text, build_facet_filter(facets))
    result.citations = make_citations(result.projects, ctx.db)
    return result


def senior_path(result, senior_id, ctx: Context):
    """A portfolio by senior_id (bot: candidate pick, Portfolio button, senior_<id> link)."""
    result.route = result.intent = "PORTFOLIO"
    try:
        result.portfolio = portfolio(ctx.db, senior_id)
    except LookupError:  # unknown or withdrawn senior
        result.mode = "not_found"
        result.answer = NOT_FOUND
        return result
    result.mode = "portfolio"
    result.resolved_senior = senior_id
    return result


def project_path(result, project_id, ctx: Context):
    """One project by project_id, from SQLite only (bot: Abstract button, proj_<id> link)."""
    result.route = result.intent = "PROJECT"
    row = ctx.db.get_project(project_id)
    if row is None:  # unknown, pending, or removed project
        result.mode = "not_found"
        result.answer = PROJECT_NOT_FOUND
        return result
    result.mode = "project"
    result.citations = [
        Citation(
            project_id=row["project_id"],
            title=row["title_en"],
            senior_ids=tuple(row["senior_ids"]),
            page_number=0,  # the manifest abstract has no page
            snippet=row["abstract"],
            matched_by="manifest",
            score=0.0,
            authors=tuple(ctx.db.senior_names(row["senior_ids"])),
            year=row["year"],
            program=row["program"],
            supervisor_id=row["supervisor_id"] or "",
            repo_url=row.get("repo_url"),
        )
    ]
    return result


def multi_intent_path(result, name_query, topic_query, ctx: Context):
    """For "Sok Dara's OCR project": the senior's portfolio, matching project first."""
    db = ctx.db
    result.route = "MULTI_INTENT"
    resolution = resolve_senior(name_query.normalized, db, ctx.client, collection=ctx.collection)
    result.resolution = resolution
    if resolution.status == "ambiguous":
        result.mode = "ambiguous"  # ask first; the topic half is NOT run
        return result
    if resolution.status == "not_found":
        result.mode = "not_found"
        result.answer = NOT_FOUND
        return result

    senior_id = resolution.senior_id
    result.mode = "portfolio"
    result.resolved_senior = senior_id
    result.portfolio = portfolio(db, senior_id)
    senior_name = result.portfolio.senior["full_name_latin"]
    topic = topic_query.normalized

    # Intersection on senior_ids, inside Qdrant: only this senior's chunks are searched.
    only_this_senior = models.Filter(
        must=[models.FieldCondition(key="senior_ids", match=models.MatchAny(any=[senior_id]))]
    )
    found = search(ctx, topic_query.lexical, only_this_senior)
    matches = [p for p in found if p.dense_score >= ctx.threshold]
    if not matches:
        # Never drop the topic silently: say that nothing matched.
        result.answer = f"None of {senior_name}'s indexed projects matched '{topic}'."
        return result

    # Matching projects first, then the senior's other projects.
    result.projects = matches
    result.citations = make_citations(matches, db)
    by_id = {p["project_id"]: p for p in result.portfolio.projects}
    matched_ids = [p.project_id for p in matches if p.project_id in by_id]  # search order
    first = [by_id[project_id] for project_id in matched_ids]
    others = [p for p in result.portfolio.projects if p["project_id"] not in matched_ids]
    old = result.portfolio
    result.portfolio = Portfolio(old.senior, first + others, old.metadata_only)
    return result


# ---------------------------------------------------------------- entry point


def answer(
    query,
    client,
    embedder,
    db,
    abstain_threshold=ABSTAIN_THRESHOLD,
    enable_llm=False,
    *,
    intent=None,
    senior_id=None,
    project_id=None,
    facets=None,
    collection=None,
) -> AnswerResult:
    """Answer one query. Raises RetrievalUnavailable when a search needs Qdrant and it is down.

    Optional entry points for the Telegram bot (ADR-005), at most one at a time:
      intent="SENIOR_LOOKUP"  /senior <name>: treat the text as a name, skip routing
      senior_id="s-..."       open that senior's portfolio
      project_id="dse..."     open that project (from SQLite)
      facets={...}            facet chips; {} means "Clear all"
    `collection` is only for tests that use a temporary Qdrant collection.
    """
    if intent is not None and intent not in INTENTS:
        raise ValueError(f"unknown intent: {intent}")
    start = time.perf_counter()
    query = (query or "").strip()
    routed = route(query, db)
    if intent is not None:
        # The caller already knows the intent (for example /senior), so the router is skipped.
        name_span = routed.normalized if intent in ("SENIOR_LOOKUP", "PORTFOLIO") else None
        routed = replace(routed, intent=intent, name_span=name_span or routed.name_span)

    result = AnswerResult(
        query_id=str(uuid.uuid4()),
        route=routed.intent,
        mode="extractive",
        answer=None,
        citations=[],
        projects=[],
        portfolio=None,
        weak_matches=[],
        normalized_query=routed.normalized,
        intent=routed.intent,
        resolved_senior=None,
        latency_ms=0,
        query=query,
    )
    ctx = Context(client, embedder, db, abstain_threshold, enable_llm, collection)
    multi = decompose_multi_intent(query, db) if intent is None else None

    if senior_id is not None:
        senior_path(result, senior_id, ctx)
    elif project_id is not None:
        project_path(result, project_id, ctx)
    elif facets is not None:
        facet_path(result, routed, ctx, facets)
    elif not routed.normalized:
        abstain(result, [])
    elif multi:
        multi_intent_path(result, multi[0], multi[1], ctx)
    elif routed.intent in ("SENIOR_LOOKUP", "PORTFOLIO"):
        name_path(result, routed, ctx)
    elif routed.intent == "FACET_BROWSE":
        routed_facets = {"year": list(routed.years), "program": list(routed.programs)}
        facet_path(result, routed, ctx, routed_facets)
    else:  # TOPIC_SEARCH and ANSWER
        search_path(result, routed, ctx)

    result.latency_ms = int((time.perf_counter() - start) * 1000)
    counters.record_retrieval(
        result.latency_ms,
        abstained=result.mode == "abstain",
        zero_results=result.mode == "abstain" and not result.weak_matches,
    )
    return result


# ---------------------------------------------------------------- query log (T6.4)


def retrieved_json(result: AnswerResult) -> list[dict]:
    """[{project_id, rank, score, matched_by}] of what the user was shown."""
    shown = result.projects or result.weak_matches
    rows = []
    for rank, project in enumerate(shown, start=1):
        rows.append(
            {
                "project_id": project.project_id,
                "rank": rank,
                "score": round(project.score, 4),
                "matched_by": project.matched_by,
            }
        )
    return rows


def log_result(db, result: AnswerResult, surface="telegram", tg_user_hash=None, tg_message_id=None):
    """Write one query_log row. Returns False (and logs a warning) if the write fails.

    `tg_user_hash` must already be a salted hash: a raw Telegram id is never logged.
    No contact field is written: only ids, scores, and the query text.
    """
    try:
        db.log_query(
            query_id=result.query_id,
            surface=surface,
            tg_user_hash=tg_user_hash,
            tg_message_id=tg_message_id,
            raw_query=result.query,
            normalized_query=result.normalized_query,
            intent=result.intent,
            resolved_senior=result.resolved_senior,
            retrieved_json=retrieved_json(result),
            latency_ms=result.latency_ms,
        )
        return True
    except Exception as error:  # logging must never break the answer
        logger.warning("query log write failed: %s", error)
        return False
