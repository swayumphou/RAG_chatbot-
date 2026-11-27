"""The whole retrieval plane on the fixture corpus (T3.8).

The fixture index is built ONCE for this file with the real ingestion pipeline
and a fake embedder (no model download, no LLM, no Telegram). Qdrant is the
server at QDRANT_URL when that variable is set, otherwise the embedded
in-memory mode (smoke test only).
"""

import importlib.util
import math
import os
import re
import uuid
import zlib
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("langchain_text_splitters")
pytest.importorskip("rapidfuzz")

from qdrant_client import QdrantClient  # noqa: E402

from ingestion.run import run_ingestion  # noqa: E402
from retrieval.entity import resolve_senior  # noqa: E402
from retrieval.filters import build_facet_filter, facet_counts  # noqa: E402
from retrieval.hybrid import hybrid_retrieve  # noqa: E402
from retrieval.intent import route  # noqa: E402
from retrieval.portfolio import portfolio  # noqa: E402
from store.db import DB  # noqa: E402

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PROJECTS = FIXTURES / "manifest" / "projects.csv"
SENIORS = FIXTURES / "manifest" / "seniors.csv"
SOK_DARA = "s-2024-014"


class FakeEmbedder:
    """Deterministic 1024-d word-hash vectors (same as the ingestion test)."""

    revision = "fake-bge-m3@test"

    def encode(self, texts):
        dense, sparse = [], []
        for text in texts:
            vector = [0.0] * 1024
            weights = {}
            for word in re.findall(r"\w+", text.lower()):
                code = zlib.crc32(word.encode("utf-8"))
                vector[code % 1024] += 1.0
                weights[code % 50_000] = weights.get(code % 50_000, 0.0) + 1.0
            vector[0] += 0.01
            norm = math.sqrt(sum(v * v for v in vector))
            dense.append([v / norm for v in vector])
            indices = sorted(weights)
            sparse.append((indices, [weights[i] for i in indices]))
        return dense, sparse

    def encode_query(self, text):
        dense, sparse = self.encode([text])
        return dense[0], sparse[0]


class StoppedQdrant:
    """Stands in for a Qdrant server that is not running: every call fails."""

    def __getattr__(self, name):
        raise ConnectionError("Qdrant is stopped")


def fixture_corpus(tmp_path_factory) -> Path:
    committed = FIXTURES / "corpus"
    if (committed / "dse2024-001" / "report.pdf").exists():
        return committed
    for library in ("pymupdf", "docx", "pptx", "PIL"):
        pytest.importorskip(library)
    spec = importlib.util.spec_from_file_location("build_fixtures", FIXTURES / "build_fixtures.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder.build_all(tmp_path_factory.mktemp("corpus"))


@pytest.fixture(scope="module")
def kb(tmp_path_factory):
    """Build the fixture index once: (client, collection, embedder, db)."""
    folder = tmp_path_factory.mktemp("retrieval")
    url = os.environ.get("QDRANT_URL")
    client = QdrantClient(url=url) if url else QdrantClient(":memory:")
    collection = f"test_retrieval_{uuid.uuid4().hex[:8]}"
    embedder = FakeEmbedder()
    db = DB(folder / "kb.sqlite3")

    report = run_ingestion(
        PROJECTS,
        SENIORS,
        fixture_corpus(tmp_path_factory),
        db,
        client,
        embedder,
        rebuild=True,
        collection=collection,
        log_dir=folder / "logs",
    )
    assert report["status"] == "ok", report

    yield client, collection, embedder, db

    db.close()
    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.close()


def search(kb, text, facets=None):
    client, collection, embedder, _ = kb
    lexical = route(text, kb[3]).lexical
    return hybrid_retrieve(
        client, embedder, lexical, flt=build_facet_filter(facets), collection=collection
    )


# ---------------------------------------------------------------- the six assertions


def test_topic_query_returns_at_least_three_distinct_projects(kb):
    results = search(kb, "khmer ocr")
    ids = [r.project_id for r in results]
    assert len(ids) >= 3
    assert len(ids) == len(set(ids))  # grouped by project: no project twice
    assert all(r.matched_by in {"dense", "lexical", "both"} for r in results)
    assert all(r.dense_score > 0 for r in results)  # the gate has a score to use


def test_name_query_returns_the_resolved_senior(kb):
    client, collection, _, db = kb
    routed = route("Sok Dara", db)
    assert routed.intent == "SENIOR_LOOKUP"
    result = resolve_senior(routed.name_span, db, client, collection=collection)
    assert (result.status, result.senior_id) == ("resolved", SOK_DARA)

    # A name that is not an alias goes through the real Qdrant full-text index.
    result = resolve_senior("sreyneang", db, client, collection=collection)
    assert (result.status, result.senior_id, result.via) == ("resolved", "s-2024-021", "text_index")


def test_three_spellings_return_the_same_senior(kb):
    client, collection, _, db = kb
    ids = {
        resolve_senior(name, db, client, collection=collection).senior_id
        for name in ("Sok Dara", "សុខ ដារា", "sokdaraa")
    }
    assert ids == {SOK_DARA}


def test_ambiguous_name_returns_candidates(kb):
    client, collection, _, db = kb
    result = resolve_senior("Keo Sophea", db, client, collection=collection)
    assert result.status == "ambiguous"
    assert result.senior_id is None
    assert {c["senior_id"] for c in result.candidates} == {"s-2024-031", "s-2024-044"}


def test_year_and_program_filter_returns_only_matching_projects(kb):
    db = kb[3]
    for facets in ({"year": 2024, "program": "ITE"}, {"year": 2024, "program": "DSE"}):
        results = search(kb, "project", facets)
        assert results, facets
        for r in results:
            assert (r.year, r.program) == (facets["year"], facets["program"])
        # Every result is a project that SQLite also counts for these facets.
        allowed = {p["project_id"] for p in db.list_projects(facets)}
        assert {r.project_id for r in results} <= allowed
        assert facet_counts(db, facets)["total"] == len(allowed)

    # A year with no projects returns nothing (filtered inside Qdrant).
    assert search(kb, "project", {"year": 2019}) == []


def test_portfolio_renders_from_sqlite_with_qdrant_stopped(kb):
    db = kb[3]
    stopped = StoppedQdrant()

    # Name resolution still works without Qdrant (alias, then fuzzy).
    assert resolve_senior("Sok Dara", db, stopped).senior_id == SOK_DARA
    assert resolve_senior("sok daraa", db, stopped).senior_id == SOK_DARA

    result = portfolio(db, SOK_DARA)
    assert [p["project_id"] for p in result.projects] == ["dse2024-004", "dse2024-001"]
    assert result.metadata_only is False
    assert result.senior["full_name_latin"] == "Sok Dara"
