"""End-to-end ingestion on the fixture corpus (T2.9).

Uses a fake embedder (no model download). Qdrant is the server at QDRANT_URL when
that variable is set, otherwise the embedded in-memory mode (smoke test only).
"""

import importlib.util
import math
import os
import re
import shutil
import uuid
import zlib
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("langchain_text_splitters")

from qdrant_client import QdrantClient, models  # noqa: E402

from ingestion.indexer import count_points  # noqa: E402
from ingestion.run import run_ingestion  # noqa: E402
from store.db import DB  # noqa: E402

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PROJECTS = FIXTURES / "manifest" / "projects.csv"
SENIORS = FIXTURES / "manifest" / "seniors.csv"


class FakeEmbedder:
    """Deterministic 1024-d word-hash vectors."""

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


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    committed = FIXTURES / "corpus"
    if (committed / "dse2024-001" / "report.pdf").exists():
        return committed
    for library in ("pymupdf", "docx", "pptx", "PIL"):
        pytest.importorskip(library)
    spec = importlib.util.spec_from_file_location("build_fixtures", FIXTURES / "build_fixtures.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder.build_all(tmp_path_factory.mktemp("corpus"))


@pytest.fixture
def qdrant():
    """(client, collection name). The collection is deleted afterwards."""
    url = os.environ.get("QDRANT_URL")
    client = QdrantClient(url=url) if url else QdrantClient(":memory:")
    name = f"test_ingest_{uuid.uuid4().hex[:8]}"
    yield client, name
    if client.collection_exists(name):
        client.delete_collection(name)


def run(corpus, db, client, name, tmp_path, projects=PROJECTS, seniors=SENIORS, **options):
    return run_ingestion(
        projects,
        seniors,
        corpus,
        db,
        client,
        FakeEmbedder(),
        collection=name,
        log_dir=tmp_path / "logs",
        **options,
    )


def project_points(client, name, project_id):
    flt = models.Filter(
        must=[models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id))]
    )
    return client.count(name, count_filter=flt, exact=True).count


def test_fixture_corpus_ingests_end_to_end(corpus, qdrant, tmp_path):
    client, name = qdrant
    db = DB(tmp_path / "kb.sqlite3")
    report = run(corpus, db, client, name, tmp_path)

    assert report["status"] == "ok", report["errors"]
    statuses = {p["project_id"]: p["status"] for p in report["projects"]}
    assert statuses["dse2024-001"] == "ok"
    assert statuses["dse2024-003"] == "ok"
    assert statuses["dse2024-004"] == "ok"
    assert statuses["dse2024-005"] == "failed"  # metadata-only: zero chunks
    assert statuses["dse2024-006"] == "failed"  # blank PDF
    assert "dse2024-007" not in statuses  # pending
    assert "dse2022-008" not in statuses  # withdrawn author

    # Every document is named in the report with its parse status.
    docs = {d["doc_id"]: d for p in report["projects"] for d in p["documents"] if "doc_id" in d}
    assert docs["dse2024-006__report.pdf"]["parse_status"] == "failed"
    assert docs["dse2024-001__slides.pptx"]["doc_type"] == "slides"

    assert count_points(client, name) == report["chunk_count"]
    assert project_points(client, name, "dse2024-007") == 0
    assert project_points(client, name, "dse2022-008") == 0

    # SQLite was rebuilt from the manifest; metadata-only project still exists there.
    assert db.get_project("dse2024-005") is not None
    assert db.get_project("dse2024-007") is None
    assert "s-2022-077" in db.withdrawn_senior_ids()
    assert Path(report["report_path"]).is_file()


def test_second_run_adds_zero_points(corpus, qdrant, tmp_path):
    client, name = qdrant
    db = DB(tmp_path / "kb.sqlite3")
    first = run(corpus, db, client, name, tmp_path)
    second = run(corpus, db, client, name, tmp_path)
    assert second["status"] == "ok"
    assert second["new_points"] == 0
    assert second["points_after"] == first["points_after"]
    assert second["validation"]["diff"]["projects"]["added"] == []


def test_validation_error_stops_before_qdrant(corpus, tmp_path):
    class NoQdrant:
        def __getattr__(self, name):
            raise AssertionError("Qdrant must not be touched when validation fails")

    db = DB(tmp_path / "kb.sqlite3")
    bad = FIXTURES / "manifest" / "invalid" / "duplicate_project_id.csv"
    report = run(corpus, db, NoQdrant(), "unused", tmp_path, projects=bad)
    assert report["status"] == "invalid"
    assert report["validation"]["errors"]


def test_withdrawn_senior_is_removed_on_the_next_run(corpus, qdrant, tmp_path):
    client, name = qdrant
    db = DB(tmp_path / "kb.sqlite3")
    work = tmp_path / "corpus"
    shutil.copytree(corpus, work)
    (work / "dse2022-008").mkdir()
    (work / "dse2022-008" / "report.md").write_text(
        "# Rice Price Forecasting\n\n## Methods\n\n" + "An invented ARIMA forecasting method. " * 5,
        encoding="utf-8",
    )
    seniors_text = SENIORS.read_text(encoding="utf-8")
    active = tmp_path / "seniors_active.csv"
    active.write_text(seniors_text.replace(",withdrawn", ",active"), encoding="utf-8")

    run(work, db, client, name, tmp_path, seniors=active)
    assert project_points(client, name, "dse2022-008") > 0

    report = run(work, db, client, name, tmp_path)  # the real file: s-2022-077 is withdrawn
    assert report["status"] == "ok", report["errors"]
    assert project_points(client, name, "dse2022-008") == 0
    assert "dse2022-008" in report["validation"]["diff"]["projects"]["removed"]
