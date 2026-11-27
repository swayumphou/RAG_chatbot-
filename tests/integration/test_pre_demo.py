"""The pre-demo health check against a fixture index (T7.4).

The index is built with the real ingestion pipeline, a fake embedder, and
in-memory Qdrant. Telegram is never contacted.
"""

import importlib.util
import math
import re
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("telegram")
pytest.importorskip("langchain_text_splitters")

from qdrant_client import QdrantClient  # noqa: E402

from config import DEFAULT_OLLAMA_MODEL  # noqa: E402
from ingestion.indexer import collection_name  # noqa: E402
from ingestion.run import run_ingestion  # noqa: E402
from store.db import DB  # noqa: E402

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SCRIPT = ROOT / "scripts" / "pre_demo_check.py"
spec = importlib.util.spec_from_file_location("pre_demo_check", SCRIPT)
pre_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre_demo)


class FakeEmbedder:
    """Deterministic 1024-d word-hash vectors (no model download)."""

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


@pytest.fixture
def kit(tmp_path):
    corpus = FIXTURES / "corpus"
    if not (corpus / "dse2024-001" / "report.pdf").exists():
        pytest.skip("fixture corpus not built (python tests/fixtures/build_fixtures.py)")
    client = QdrantClient(":memory:")  # in memory only: never a real server's collection
    db = DB(tmp_path / "kb.sqlite3")
    manifest = FIXTURES / "manifest"
    embedder = FakeEmbedder()
    report = run_ingestion(
        manifest / "projects.csv",
        manifest / "seniors.csv",
        corpus,
        db,
        client,
        embedder,
        rebuild=True,
        log_dir=tmp_path / "logs",
    )
    assert report["status"] == "ok"
    baseline = tmp_path / "baseline.json"
    baseline.write_text("", encoding="utf-8")  # no approved baseline yet
    yield SimpleNamespace(
        client=client,
        embedder=embedder,
        db=db,
        collection=None,
        threshold=0.45,
        enable_llm=False,
        ollama_url="http://127.0.0.1:9",  # nothing listens here: Ollama is "down"
        embed_model="fake-bge-m3",
        llm_model=DEFAULT_OLLAMA_MODEL,
        baseline_path=baseline,
        projects_csv=manifest / "projects.csv",
        seniors_csv=manifest / "seniors.csv",
        corpus_dir=corpus,
        log_dir=tmp_path / "logs",
    )
    db.close()


def by_name(results):
    return {item["check"]: item for item in results}


def test_every_check_passes_on_a_healthy_index(kit, monkeypatch):
    import telegram

    def no_telegram(*args, **kwargs):
        raise AssertionError("the pre-demo check must not create a Telegram bot")

    monkeypatch.setattr(telegram.Bot, "__init__", no_telegram)
    results = pre_demo.run_checks(kit)
    assert len(results) == 12
    failed = [item for item in results if item["status"] == "FAIL"]
    assert failed == [], failed
    assert by_name(results)["re-ingest adds 0 points"]["detail"].startswith("0 new points")
    assert kit.db.query_log_rows() == []  # the 50-message check left no log rows behind


def test_a_missing_payload_key_fails_and_is_named(kit):
    point = next(iter(pre_demo.scroll_all(kit.client)))
    kit.client.delete_payload(collection_name(), keys=["text"], points=[point.id])
    result = by_name(pre_demo.run_checks(kit))["payload keys complete"]
    assert result["status"] == "FAIL"
    assert point.payload["chunk_id"] in result["detail"] and "text" in result["detail"]


def test_a_model_tag_mismatch_fails(kit):
    kit.embed_model = "BAAI/bge-m3"  # the loaded embedder is the fake one
    kit.llm_model = "gemma4:latest"
    result = pre_demo.check_model_tags(kit)
    assert result[0] == "FAIL"
    assert "fake-bge-m3@test" in result[1] and "gemma4:latest" in result[1]
