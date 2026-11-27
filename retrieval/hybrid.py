"""Topic search: dense + sparse search in Qdrant, fused with RRF, grouped by project.

Why both? Dense (meaning) search finds "handwriting recognition" for "OCR".
Sparse (keyword) search finds rare exact words such as "Khmer" or a library
name. Reciprocal Rank Fusion (RRF, k=60) merges the two ranked lists using
ranks only, so their different score scales never need to be compared.

Grouping by project_id happens AFTER fusion and BEFORE we cut to the top 5.
Otherwise one project with many chunks can fill all 5 places.

We also compute, per project:
- dense_score: the best dense cosine similarity. The relevance gate uses this,
  because RRF scores are not comparable between queries.
- matched_by: "dense", "lexical", or "both", from which search list the
  project appeared in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from qdrant_client import models

from config import FUSION_LIMIT, PREFETCH_K, RRF_K, TOP_K
from ingestion.indexer import collection_name


class RetrievalUnavailable(RuntimeError):
    """Qdrant could not be reached or the search failed."""


@dataclass(frozen=True)
class RetrievedProject:
    project_id: str
    title: str
    senior_ids: tuple[str, ...]
    year: int
    program: str
    supervisor_id: str
    page_number: int
    snippet: str  # the best chunk's text, without the breadcrumb line
    score: float  # fused RRF score (only used for ordering)
    dense_score: float  # best dense cosine of this project (the gate uses this)
    matched_by: str  # "dense", "lexical", or "both"


def strip_breadcrumb(text: str) -> str:
    """Chunk text is "breadcrumb\\nbody". Return only the body."""
    if text.startswith("[Project:") and "\n" in text:
        return text.split("\n", 1)[1].strip()
    return text.strip()


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0:
        return 0.0
    return dot / norm


def to_retrieved(point, dense_score=0.0, matched_by="both") -> RetrievedProject:
    payload = point.payload
    return RetrievedProject(
        project_id=payload["project_id"],
        title=payload["title_en"],
        senior_ids=tuple(payload.get("senior_ids", [])),
        year=int(payload["year"]),
        program=payload["program"],
        supervisor_id=payload.get("supervisor_id") or "",
        page_number=int(payload["page_number"]),
        snippet=strip_breadcrumb(payload.get("text", "")),
        score=float(point.score),
        dense_score=float(dense_score),
        matched_by=matched_by,
    )


def group_by_project(points, top_k=TOP_K, dense_scores=None, matched_by=None):
    """Keep the best chunk of each project, sort by score, return the top_k projects.

    `points` are the fused (RRF) results. `dense_scores` and `matched_by` are
    dicts keyed by project_id, filled in by hybrid_retrieve().
    """
    dense_scores = dense_scores or {}
    matched_by = matched_by or {}

    best = {}
    for point in points:
        project_id = point.payload["project_id"]
        if project_id not in best or point.score > best[project_id].score:
            best[project_id] = point

    ranked = sorted(best.values(), key=lambda p: p.score, reverse=True)[:top_k]

    results = []
    for point in ranked:
        project_id = point.payload["project_id"]
        results.append(
            to_retrieved(
                point,
                dense_score=dense_scores.get(project_id, 0.0),
                matched_by=matched_by.get(project_id, "both"),
            )
        )
    return results


def hybrid_retrieve(
    client,
    embedder,
    lexical_query,
    flt=None,
    prefetch_k=PREFETCH_K,
    fusion_limit=FUSION_LIMIT,
    top_k=TOP_K,
    collection=None,
) -> list[RetrievedProject]:
    """Search for projects. The filter `flt` is applied inside Qdrant."""
    if not lexical_query or not lexical_query.strip():
        return []

    # If the embedder fails we let the error through: we never fall back to
    # keyword-only search, because that would change the ranking silently.
    dense, (indices, values) = embedder.encode_query(lexical_query)
    sparse = models.SparseVector(indices=indices, values=values)
    name = collection_name(collection)

    try:
        # 1) dense list and 2) sparse list: these tell us matched_by.
        dense_hits = client.query_points(
            name,
            query=dense,
            using="dense",
            query_filter=flt,
            limit=prefetch_k,
            with_payload=["project_id"],
        ).points
        lexical_hits = client.query_points(
            name,
            query=sparse,
            using="lexical",
            query_filter=flt,
            limit=prefetch_k,
            with_payload=["project_id"],
        ).points

        # 3) the fused list (RRF over the same two searches): this gives the ranking.
        fused = client.query_points(
            name,
            prefetch=[
                models.Prefetch(query=dense, using="dense", filter=flt, limit=prefetch_k),
                models.Prefetch(query=sparse, using="lexical", filter=flt, limit=prefetch_k),
            ],
            query=models.RrfQuery(rrf=models.Rrf(k=RRF_K)),
            query_filter=flt,
            limit=fusion_limit,
            with_payload=True,
            with_vectors=["dense"],  # to compute the cosine of every fused result
        ).points
    except Exception as error:
        raise RetrievalUnavailable(f"Qdrant search failed: {error}") from error

    # Best dense cosine per project.
    dense_scores = {}
    for point in dense_hits:
        project_id = point.payload["project_id"]
        dense_scores[project_id] = max(dense_scores.get(project_id, 0.0), point.score)
    for point in fused:
        # A point found only by the sparse search still has a dense vector.
        vector = point.vector.get("dense") if isinstance(point.vector, dict) else None
        if vector:
            project_id = point.payload["project_id"]
            dense_scores[project_id] = max(dense_scores.get(project_id, 0.0), cosine(dense, vector))

    # Which list(s) each project came from.
    in_dense = {p.payload["project_id"] for p in dense_hits}
    in_lexical = {p.payload["project_id"] for p in lexical_hits}
    matched_by = {}
    for project_id in in_dense | in_lexical:
        if project_id in in_dense and project_id in in_lexical:
            matched_by[project_id] = "both"
        elif project_id in in_dense:
            matched_by[project_id] = "dense"
        else:
            matched_by[project_id] = "lexical"

    return group_by_project(fused, top_k, dense_scores, matched_by)


# ---------------------------------------------------------------- LangChain


class SeniorProjectKBRetriever(BaseRetriever):
    """LangChain retriever around hybrid_retrieve (Architecture §9.6).

    retriever.invoke("khmer ocr") -> list of Document, one per project.
    """

    client: object
    embedder: object
    top_k: int = TOP_K

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        from retrieval.intent import segment_for_lexical

        projects = hybrid_retrieve(
            self.client, self.embedder, segment_for_lexical(query), top_k=self.top_k
        )
        documents = []
        for p in projects:
            metadata = {
                "project_id": p.project_id,
                "title": p.title,
                "senior_ids": list(p.senior_ids),
                "page_number": p.page_number,
                "score": p.score,
            }
            documents.append(Document(page_content=p.snippet, metadata=metadata))
        return documents
