"""Tests for the answer service (Phase 4)."""

import dataclasses
import json
import logging
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("rapidfuzz")

import answer.service as service  # noqa: E402
import cli  # noqa: E402
from answer.service import (  # noqa: E402
    ABSTAIN,
    NOT_FOUND,
    AnswerResult,
    answer,
    make_citations,
)
from ingestion.validate_manifest import validate_manifest  # noqa: E402
from retrieval.hybrid import RetrievedProject  # noqa: E402
from store.db import DB  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest"

# The fields INTERFACES.md §5.3 requires on every AnswerResult.
SPEC_FIELDS = {
    "query_id",
    "route",
    "mode",
    "answer",
    "citations",
    "projects",
    "portfolio",
    "weak_matches",
    "normalized_query",
    "intent",
    "resolved_senior",
    "latency_ms",
}


@pytest.fixture
def db(tmp_path):
    """A database with the fixture manifest loaded (no Qdrant needed)."""
    corpus = tmp_path / "corpus"
    for number in range(1, 7):
        (corpus / f"dse2024-{number:03d}").mkdir(parents=True)
    report = validate_manifest(MANIFEST / "projects.csv", MANIFEST / "seniors.csv", corpus)
    database = DB(tmp_path / "kb.sqlite3")
    with database.transaction():
        for senior in report.seniors:
            database.upsert_senior(senior)
        for project in report.projects:
            database.upsert_project(project)
            database.replace_authorship(project["project_id"], project["senior_ids"])
    yield database
    database.close()


TITLES = {
    "dse2024-001": ("Khmer Handwritten Text Recognition with Tesseract", "s-2024-014"),
    "dse2024-003": ("Campus Library Booking System", "s-2024-044"),
    "dse2024-004": ("Khmer News Topic Classification", "s-2024-014"),
}


def project(project_id, dense_score=0.80, page=1):
    """A retrieved project, as hybrid_retrieve would return it."""
    title, senior_id = TITLES.get(project_id, (f"Weak match {project_id}", "s-2024-014"))
    return RetrievedProject(
        project_id=project_id,
        title=title,
        senior_ids=(senior_id,),
        year=2024,
        program="ITE" if project_id == "dse2024-003" else "DSE",
        supervisor_id="sup-003",
        page_number=page,
        snippet=f"Passage from {project_id}.",
        score=0.03,
        dense_score=dense_score,
        matched_by="both",
    )


class FakeSearch:
    """Replaces hybrid_retrieve and records every call."""

    def __init__(self, projects):
        self.projects = projects
        self.calls = []

    def __call__(self, client, embedder, text, flt=None, collection=None):
        self.calls.append({"text": text, "flt": flt})
        return list(self.projects)


class NoQdrant:
    """A Qdrant client that fails if anything uses it."""

    def __getattr__(self, name):
        raise AssertionError(f"Qdrant must not be used here (called {name})")


@pytest.fixture
def fake_search(monkeypatch):
    fake = FakeSearch([project("dse2024-001"), project("dse2024-004", dense_score=0.6)])
    monkeypatch.setattr(service, "hybrid_retrieve", fake)
    return fake


# ---------------------------------------------------------------- T4.1


def test_service_dispatch_and_result_shape(db, fake_search):
    def ask(query):
        result = answer(query, NoQdrant(), embedder=None, db=db)
        assert isinstance(result, AnswerResult)
        assert SPEC_FIELDS <= {f.name for f in dataclasses.fields(result)}
        json.dumps(result.to_dict(), default=str)  # the result can be logged as JSON
        return result

    # TOPIC_SEARCH -> extractive, LLM off by default.
    topic = ask("khmer ocr")
    assert (topic.route, topic.intent, topic.mode) == ("TOPIC_SEARCH", "TOPIC_SEARCH", "extractive")
    assert topic.answer is None
    assert [c.project_id for c in topic.citations] == ["dse2024-001", "dse2024-004"]
    assert topic.portfolio is None
    assert topic.normalized_query == "khmer ocr"
    assert fake_search.calls[-1]["flt"] is None  # a topic search has no facet filter

    # ANSWER with enable_llm=False -> extractive too.
    question = ask("who built a Khmer OCR system")
    assert (question.intent, question.mode) == ("ANSWER", "extractive")

    # SENIOR_LOOKUP and PORTFOLIO -> portfolio, no answer text.
    for query in ("Sok Dara", "projects by Sok Dara"):
        result = ask(query)
        assert result.mode == "portfolio"
        assert result.answer is None
        assert result.resolved_senior == "s-2024-014"
        assert result.portfolio.senior["full_name_latin"] == "Sok Dara"

    # Two seniors share a name -> ambiguous, with both candidates.
    ambiguous = ask("Keo Sophea")
    assert (ambiguous.mode, ambiguous.portfolio) == ("ambiguous", None)
    assert {c["senior_id"] for c in ambiguous.resolution.candidates} == {
        "s-2024-031",
        "s-2024-044",
    }

    # Unknown name -> not_found.
    unknown = ask("Sok Panha")
    assert (unknown.mode, unknown.answer) == ("not_found", NOT_FOUND)

    # FACET_BROWSE -> filtered search + SQLite counts.
    facet = ask("DSE 2024")
    assert (facet.route, facet.mode) == ("FACET_BROWSE", "extractive")
    assert facet.facets == {"year": [2024], "program": ["DSE"]}
    assert facet.facet_counts["total"] == 4
    keys = {c.key for c in fake_search.calls[-1]["flt"].must}
    assert keys == {"year", "program"}  # the filter goes INTO the search

    # Multi-intent -> the senior's portfolio with the matching project first.
    multi = ask("Sok Dara's news project")
    assert (multi.route, multi.mode) == ("MULTI_INTENT", "portfolio")
    assert multi.portfolio.projects[0]["project_id"] == "dse2024-001"


def test_portfolio_makes_no_qdrant_http_or_telegram_call(db, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("no network call may happen for a portfolio")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(service, "hybrid_retrieve", no_network)
    monkeypatch.setattr(service, "synthesize", no_network)

    result = answer("projects by Sok Dara", NoQdrant(), embedder=None, db=db, enable_llm=True)
    assert result.mode == "portfolio"
    assert [p["project_id"] for p in result.portfolio.projects] == ["dse2024-004", "dse2024-001"]


class SpyLLM:
    """Replaces synthesize(); counts calls and returns a fixed reply."""

    def __init__(self, reply="unused"):
        self.reply = reply
        self.calls = []

    def __call__(self, query, citations):
        self.calls.append((query, list(citations)))
        return self.reply


# ---------------------------------------------------------------- T4.2


def test_gate_abstains_without_llm(db, monkeypatch, caplog):
    spy = SpyLLM()
    monkeypatch.setattr(service, "synthesize", spy)
    weak = [project(f"p{i}", dense_score=0.30 - i * 0.05) for i in range(5)]
    monkeypatch.setattr(service, "hybrid_retrieve", FakeSearch(weak))

    with caplog.at_level(logging.INFO, logger="answer.service"):
        result = answer(
            "who built a Khmer OCR system", NoQdrant(), None, db, abstain_threshold=0.45,
            enable_llm=True,
        )

    # Below the gate: abstain, the exact ABSTAIN text, and NO LLM call.
    assert result.mode == "abstain"
    assert result.answer == ABSTAIN
    assert spy.calls == []
    assert result.projects == [] and result.citations == []
    # Weak matches: at most 3, best first, kept apart from real results.
    assert [p.project_id for p in result.weak_matches] == ["p0", "p1", "p2"]
    # The decision is logged with the top dense score and the threshold.
    assert "gate top_dense=0.3 threshold=0.45" in caplog.text


def test_gate_passes_a_high_score_to_extractive(db, monkeypatch):
    spy = SpyLLM()
    monkeypatch.setattr(service, "synthesize", spy)
    monkeypatch.setattr(service, "hybrid_retrieve", FakeSearch([project("dse2024-001", 0.80)]))
    result = answer("khmer handwriting", NoQdrant(), None, db, abstain_threshold=0.45)
    assert result.mode == "extractive"
    assert [c.project_id for c in result.citations] == ["dse2024-001"]
    assert spy.calls == []  # enable_llm is False by default


def test_gate_uses_dense_score_not_rrf_score(db, monkeypatch):
    # A high RRF score must not open the gate when the dense cosine is low.
    strong_rrf = dataclasses.replace(project("dse2024-001", dense_score=0.20), score=0.99)
    monkeypatch.setattr(service, "hybrid_retrieve", FakeSearch([strong_rrf]))
    assert answer("khmer ocr", NoQdrant(), None, db, abstain_threshold=0.45).mode == "abstain"


def test_no_results_abstains_with_no_weak_matches(db, monkeypatch):
    monkeypatch.setattr(service, "hybrid_retrieve", FakeSearch([]))
    result = answer("quantum computing lab schedule 2019", NoQdrant(), None, db)
    assert (result.mode, result.answer, result.weak_matches) == ("abstain", ABSTAIN, [])


def test_weak_matches_are_labelled_low_confidence(db, monkeypatch):
    weak = [project(f"p{i}", dense_score=0.10) for i in range(2)]
    monkeypatch.setattr(service, "hybrid_retrieve", FakeSearch(weak))
    text = cli.render(answer("khmer ocr", NoQdrant(), None, db))
    assert text.startswith(ABSTAIN)
    assert text.count("(low confidence)") == 2


# ---------------------------------------------------------------- T4.3


class FakeEmbedder:
    def encode_query(self, text):
        return [1.0, 0.0], ([1], [1.0])


class FakeQdrant:
    """Returns the same chunk points for the dense, sparse, and fused searches."""

    def __init__(self, points):
        self.points = points

    def query_points(self, collection_name, **kwargs):
        return SimpleNamespace(points=self.points)


def chunk(project_id, page, score):
    title, senior_id = TITLES[project_id]
    payload = {
        "project_id": project_id,
        "title_en": title,
        "senior_ids": ["s-2024-014", "s-2024-021"] if project_id == "dse2024-001" else [senior_id],
        "year": 2024,
        "program": "DSE",
        "supervisor_id": "sup-003",
        "page_number": page,
        "text": f"[Project: {title} | p.{page}]\nVerbatim text of {project_id} page {page}.",
    }
    return SimpleNamespace(payload=payload, score=score, vector=None)


def test_extractive_citations_are_project_level(db, monkeypatch):
    spy = SpyLLM()
    monkeypatch.setattr(service, "synthesize", spy)
    points = [
        chunk("dse2024-001", page=3, score=0.90),  # best chunk of project 001
        chunk("dse2024-001", page=1, score=0.70),  # a second chunk of the same project
        chunk("dse2024-004", page=0, score=0.60),
    ]
    result = answer("khmer handwriting", FakeQdrant(points), FakeEmbedder(), db)

    assert (result.mode, result.answer) == ("extractive", None)
    # Two chunks of one project -> ONE project citation; fused order is kept.
    assert [c.project_id for c in result.citations] == ["dse2024-001", "dse2024-004"]
    first = result.citations[0]
    assert first.page_number == 3  # the page comes from the best chunk
    assert first.snippet == "Verbatim text of dse2024-001 page 3."  # verbatim, no breadcrumb
    assert first.matched_by == "both"
    assert first.authors == ("Sok Dara", "Chan Sreyneang")  # display fields from SQLite
    assert spy.calls == []  # no LLM in extractive mode


def test_citations_are_deduplicated_by_project_and_page(db):
    same = project("dse2024-001", page=4)
    citations = make_citations([same, same, project("dse2024-004", page=4)], db)
    assert [(c.project_id, c.page_number) for c in citations] == [
        ("dse2024-001", 4),
        ("dse2024-004", 4),
    ]


# ---------------------------------------------------------------- T4.4


class FakeResponse:
    """What httpx.post would return from Ollama /api/chat."""

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_llm_success_timeout_and_length(db, monkeypatch, caplog):
    import httpx

    import answer.prompts as prompts
    from config import get_config

    citations = make_citations([project("dse2024-001", page=3)], db)
    title = citations[0].title
    sent = []

    def success(url, json, timeout):
        sent.append((url, json))
        reply = f"Sok Dara built it [Project: {title}, p.3]"
        return FakeResponse({"message": {"content": reply}, "done_reason": "stop"})

    # Success: the answer text comes back; the request uses the pinned settings.
    monkeypatch.setattr(prompts.httpx, "post", success)
    with caplog.at_level(logging.INFO, logger="answer.prompts"):
        text = prompts.synthesize("who built khmer ocr", citations)
    assert text == f"Sok Dara built it [Project: {title}, p.3]"
    url, payload = sent[0]
    assert url == get_config().ollama_url + "/api/chat"
    assert payload["model"] == get_config().ollama_model
    assert payload["options"] == {"temperature": 0.0, "num_ctx": 8192, "num_predict": 600}
    assert payload["stream"] is False
    assert f"ollama model={get_config().ollama_model}" in caplog.text  # model tag logged
    assert "latency_ms=" in caplog.text

    # The service accepts a valid answer as mode="llm".
    monkeypatch.setattr(service, "hybrid_retrieve", FakeSearch([project("dse2024-001", page=3)]))
    result = answer("who built a Khmer OCR system", NoQdrant(), None, db, enable_llm=True)
    assert (result.mode, result.answer) == ("llm", text)

    # Timeout -> a typed error, and the service falls back to the extractive answer.
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(prompts.httpx, "post", timeout)
    with pytest.raises(prompts.LLMTimeout):
        prompts.synthesize("who built khmer ocr", citations)
    result = answer("who built a Khmer OCR system", NoQdrant(), None, db, enable_llm=True)
    assert result.mode == "extractive_fallback"
    assert result.answer is None and result.citations  # the user still gets sources

    # Ollama not running -> a typed error too.
    def refused(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(prompts.httpx, "post", refused)
    with pytest.raises(prompts.LLMUnavailable):
        prompts.synthesize("who built khmer ocr", citations)

    # done_reason="length" -> the answer was cut off -> a typed error.
    def cut_off(*args, **kwargs):
        return FakeResponse({"message": {"content": "Half an answ"}, "done_reason": "length"})

    monkeypatch.setattr(prompts.httpx, "post", cut_off)
    with pytest.raises(prompts.LLMTruncated):
        prompts.synthesize("who built khmer ocr", citations)


def test_prompt_contains_only_retrieved_projects(db):
    from answer.prompts import LLM_NO_ANSWER, build_messages

    citations = make_citations([project("dse2024-001", page=3), project("dse2024-004", page=0)], db)
    system, user = build_messages("who built khmer ocr", citations)
    assert (system["role"], user["role"]) == ("system", "user")

    # The system prompt carries every rule from the spec.
    assert "<retrieved_projects>" in system["content"]
    assert "[Project: <title>, p.<page>]" in system["content"]
    assert LLM_NO_ANSWER in system["content"]
    assert "Never invent a senior name, supervisor, year, or repository link" in system["content"]
    assert "900 characters" in system["content"]

    # The user message: only the retrieved projects and the question.
    assert user["content"].count("<project ") == 2
    assert "Khmer Handwritten Text Recognition with Tesseract" in user["content"]
    assert "Campus Library Booking System" not in user["content"]  # not retrieved
    assert user["content"].endswith("Question: who built khmer ocr")


# ---------------------------------------------------------------- T4.7


def test_cli_extractive_output(db, monkeypatch, capsys):
    monkeypatch.setattr(service, "hybrid_retrieve", FakeSearch([project("dse2024-001", page=3)]))
    llm_flags = []

    def fake_service(query, client, embedder, db_, threshold, enable_llm=False):
        llm_flags.append(enable_llm)
        return answer(query, client, embedder, db_, threshold, enable_llm=enable_llm)

    resources = (NoQdrant(), None, db, 0.45)

    # Extractive by default, with [Project: title, p.N] citations.
    assert cli.main(["--query", "khmer ocr"], answer_fn=fake_service, resources=resources) == 0
    out = capsys.readouterr().out
    assert "[Project: Khmer Handwritten Text Recognition with Tesseract, p.3]" in out
    assert "Passage from dse2024-001." in out
    assert llm_flags == [False]

    # --llm is opt-in.
    cli.main(["--query", "khmer ocr", "--llm"], answer_fn=fake_service, resources=resources)
    assert llm_flags[-1] is True
    capsys.readouterr()

    # A missing index (or model, or Qdrant) is a readable one-line error, not a traceback.
    def no_index(args):
        raise RuntimeError("the search index is empty. Run: python -m ingestion.run ...")

    monkeypatch.setattr(cli, "open_resources", no_index)
    assert cli.main(["--query", "khmer ocr"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: the search index is empty")
    assert "Traceback" not in err


def test_cli_does_not_import_telegram():
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    code = "import sys, cli; print('telegram' in sys.modules)"
    run = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "False"
