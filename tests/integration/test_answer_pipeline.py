"""The answer layer end to end on the fixture index (Phase 4).

The index is built once with the real ingestion pipeline, a fake embedder, and
in-memory Qdrant. Ollama is replaced by a fake HTTP response. The fake
embedder's cosine scores are much lower than bge-m3's, so these tests use a
low relevance threshold (0.1); the real threshold comes from the eval sweep.
"""

import importlib.util
import math
import re
import uuid
import zlib
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("langchain_text_splitters")
pytest.importorskip("rapidfuzz")

from qdrant_client import QdrantClient  # noqa: E402

import answer.prompts as prompts  # noqa: E402
from answer.service import ABSTAIN, answer  # noqa: E402
from ingestion.run import run_ingestion  # noqa: E402
from store.db import DB  # noqa: E402

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
THRESHOLD = 0.1
OCR_TITLE = "Khmer Handwritten Text Recognition with Tesseract"


class FakeEmbedder:
    """Deterministic 1024-d word-hash vectors (same as the other integration tests)."""

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


class FakeOllamaResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self.content}, "done_reason": "stop"}


def fixture_corpus(tmp_path_factory):
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
    """(ask, db): ask(query, **options) is answer() on the fixture index."""
    folder = tmp_path_factory.mktemp("answer")
    client = QdrantClient(":memory:")
    collection = f"test_answer_{uuid.uuid4().hex[:8]}"
    embedder = FakeEmbedder()
    db = DB(folder / "kb.sqlite3")
    report = run_ingestion(
        FIXTURES / "manifest" / "projects.csv",
        FIXTURES / "manifest" / "seniors.csv",
        fixture_corpus(tmp_path_factory),
        db,
        client,
        embedder,
        rebuild=True,
        collection=collection,
        log_dir=folder / "logs",
    )
    assert report["status"] == "ok"

    def ask(query, enable_llm=False, threshold=THRESHOLD, **options):
        return answer(
            query, client, embedder, db, threshold, enable_llm, collection=collection, **options
        )

    yield ask, db
    db.close()
    client.close()


def test_topic_query_gives_extractive_project_citations(kb):
    ask, _ = kb
    result = ask("khmer handwriting recognition tesseract")
    assert result.mode == "extractive" and result.answer is None
    first = result.citations[0]
    assert first.title == OCR_TITLE
    assert first.page_number >= 1  # a real PDF page or slide number
    assert first.authors == ("Sok Dara", "Chan Sreyneang")  # from SQLite
    ids = [c.project_id for c in result.citations]
    assert len(ids) == len(set(ids))  # one citation per project


def test_llm_answer_is_kept_only_with_valid_citations(kb, monkeypatch):
    ask, _ = kb
    question = "who built a khmer handwriting recognition system with tesseract"
    page = ask(question).citations[0].page_number
    sent = []

    def fake_post(url, json, timeout):
        sent.append(json)
        return FakeOllamaResponse(reply)

    monkeypatch.setattr(prompts.httpx, "post", fake_post)

    reply = f"Sok Dara and Chan Sreyneang built it [Project: {OCR_TITLE}, p.{page}]."
    result = ask(question, enable_llm=True)
    assert (result.mode, result.answer) == ("llm", reply)
    prompt = str(sent[-1]["messages"])
    assert "example.invalid" not in prompt and "@" not in prompt  # no contact data

    reply = f"They used a GPU cluster [Project: {OCR_TITLE}, p.99]."  # an invented page
    result = ask(question, enable_llm=True)
    assert result.mode == "extractive_fallback"
    assert result.answer is None and result.citations  # sources are still shown


def test_nonsense_abstains_and_never_calls_the_llm(kb, monkeypatch):
    ask, _ = kb

    def no_llm(*args, **kwargs):
        raise AssertionError("the LLM must not be called below the gate")

    monkeypatch.setattr(prompts.httpx, "post", no_llm)
    result = ask("who knows qzxv wplk mnbt", enable_llm=True, threshold=0.45)
    assert (result.mode, result.answer) == ("abstain", ABSTAIN)


def test_portfolios_respect_consent(kb):
    ask, _ = kb
    with_consent = ask("Sok Dara")
    assert with_consent.mode == "portfolio"
    assert "github" in with_consent.portfolio.senior["contact"]
    without_consent = ask("Chan Sreyneang")
    assert "contact" not in without_consent.portfolio.senior
    assert ask("Keo Sophea").mode == "ambiguous"


def test_name_plus_topic_puts_the_matching_project_first(kb):
    ask, _ = kb
    result = ask("Sok Dara's news classification project")
    assert (result.route, result.mode) == ("MULTI_INTENT", "portfolio")
    assert result.portfolio.projects[0]["project_id"] == "dse2024-004"

    nothing = ask("Sok Dara's irrigation project")  # no indexed project of Sok Dara matches
    assert nothing.answer == "None of Sok Dara's indexed projects matched 'irrigation'."
    assert len(nothing.portfolio.projects) == 2  # the senior is still shown

    # An ambiguous name asks first; the topic half is not run.
    ambiguous = ask("Keo Sophea's library project")
    assert (ambiguous.route, ambiguous.mode) == ("MULTI_INTENT", "ambiguous")
    assert len(ambiguous.resolution.candidates) == 2
    assert ambiguous.projects == [] and ambiguous.portfolio is None
