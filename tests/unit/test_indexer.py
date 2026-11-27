"""Tests for the Qdrant indexer and the integrity verifier (T2.8)."""

import math
import re
import zlib

import pytest

pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient, models  # noqa: E402

from ingestion.chunker import Chunk  # noqa: E402
from ingestion.indexer import (  # noqa: E402
    count_points,
    delete_stale_points,
    ensure_collection,
    point_id,
    upsert_chunks,
)
from ingestion.verify import IntegrityError, assert_run_integrity  # noqa: E402

COLLECTION = "test_collection"


class FakeEmbedder:
    """Deterministic 1024-d word-hash vectors. No model is downloaded."""

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
            vector[0] += 0.01  # never an all-zero vector
            norm = math.sqrt(sum(v * v for v in vector))
            dense.append([v / norm for v in vector])
            indices = sorted(weights)
            sparse.append((indices, [weights[i] for i in indices]))
        return dense, sparse

    def encode_query(self, text):
        dense, sparse = self.encode([text])
        return dense[0], sparse[0]


class RecordingClient:
    """Pretends to be Qdrant and records what the indexer asks for."""

    def __init__(self):
        self.created = None
        self.indexes = {}

    def collection_exists(self, name):
        return self.created is not None

    def create_collection(self, collection_name, vectors_config, sparse_vectors_config):
        self.created = (collection_name, vectors_config, sparse_vectors_config)

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexes[field_name] = field_schema


def make_chunk(project_id, index, senior_ids=("s1",), text="fixture chunk about khmer ocr"):
    chunk_id = f"{project_id}__report.pdf::c{index:03d}"
    metadata = {
        "project_id": project_id,
        "title_en": f"Project {project_id}",
        "senior_ids": list(senior_ids),
        "author_names_normalized": "sok dara",
        "doc_id": f"{project_id}__report.pdf",
        "doc_type": "report",
        "year": 2024,
        "program": "DSE",
        "domain": None,
        "tech_stack": [],
        "supervisor_id": "sup-001",
        "section": "body",
        "page_number": 1,
        "chunk_index": index,
        "chunk_id": chunk_id,
        "breadcrumb": "[Project: x | p.1]",
        "is_primary_doc": True,
    }
    return Chunk(chunk_id, f"[Project: x | p.1]\n{text} {index}", metadata)


class FakeDB:
    def __init__(self, active=("s1",), withdrawn=()):
        self.active = set(active)
        self.withdrawn = set(withdrawn)

    def senior_exists(self, senior_id):
        return senior_id in self.active | self.withdrawn

    def withdrawn_senior_ids(self):
        return self.withdrawn


def test_payload_indexes_and_deterministic_ids():
    client = RecordingClient()
    ensure_collection(client, COLLECTION)
    name, vectors, sparse = client.created
    assert name == COLLECTION
    dense = vectors["dense"]
    assert dense.size == 1024 and dense.distance == models.Distance.COSINE
    assert dense.hnsw_config.m == 16 and dense.hnsw_config.ef_construct == 200
    assert "lexical" in sparse

    keyword_fields = [
        "project_id",
        "doc_id",
        "doc_type",
        "program",
        "supervisor_id",
        "senior_ids",
        "domain",
        "tech_stack",
    ]
    for field in keyword_fields:
        assert client.indexes[field] == models.PayloadSchemaType.KEYWORD, field
    assert client.indexes["year"] == models.PayloadSchemaType.INTEGER
    text_index = client.indexes["author_names_normalized"]
    assert text_index.tokenizer == models.TokenizerType.WORD
    assert text_index.min_token_len == 2 and text_index.lowercase is True

    ensure_collection(client, COLLECTION)  # second call: nothing is created again
    assert point_id("a::c000") == point_id("a::c000")
    assert point_id("a::c000") != point_id("a::c001")


def test_reupsert_does_not_duplicate_points():
    client = QdrantClient(":memory:")
    ensure_collection(client, COLLECTION)
    chunks = [make_chunk("p1", i) for i in range(3)]
    assert upsert_chunks(client, FakeEmbedder(), chunks, COLLECTION) == 3
    upsert_chunks(client, FakeEmbedder(), chunks, COLLECTION)
    assert count_points(client, COLLECTION) == 3

    stored = client.retrieve(COLLECTION, ids=[point_id(chunks[0].chunk_id)], with_payload=True)
    assert stored[0].payload["text"] == chunks[0].text  # text lives in the payload


def test_delete_stale_points_removes_old_projects_and_chunks():
    client = QdrantClient(":memory:")
    ensure_collection(client, COLLECTION)
    chunks = [make_chunk("p1", 0), make_chunk("p1", 1), make_chunk("p2", 0)]
    upsert_chunks(client, FakeEmbedder(), chunks, COLLECTION)
    keep = {"p1": {chunks[0].chunk_id}}  # p1 lost one chunk, p2 was withdrawn
    assert delete_stale_points(client, keep, COLLECTION) == 2
    assert count_points(client, COLLECTION) == 1


def test_integrity_passes_and_fails_with_a_named_project():
    client = QdrantClient(":memory:")
    ensure_collection(client, COLLECTION)
    upsert_chunks(client, FakeEmbedder(), [make_chunk("p1", 0)], COLLECTION)
    assert_run_integrity(client, FakeDB(), 1, ["p1"], COLLECTION)

    with pytest.raises(IntegrityError, match="point count"):
        assert_run_integrity(client, FakeDB(), 2, ["p1"], COLLECTION)
    with pytest.raises(IntegrityError, match="p2: produced zero indexed chunks"):
        assert_run_integrity(client, FakeDB(), 1, ["p1", "p2"], COLLECTION)
    with pytest.raises(IntegrityError, match="p1: unknown senior_id s1"):
        assert_run_integrity(client, FakeDB(active=()), 1, ["p1"], COLLECTION)
    with pytest.raises(IntegrityError, match="withdrawn"):
        assert_run_integrity(client, FakeDB(active=(), withdrawn=("s1",)), 1, ["p1"], COLLECTION)

    broken = make_chunk("p3", 0)
    del broken.metadata["program"]
    upsert_chunks(client, FakeEmbedder(), [broken], COLLECTION)
    with pytest.raises(IntegrityError, match="p3: point .* is missing \\['program'\\]"):
        assert_run_integrity(client, FakeDB(), 2, ["p1", "p3"], COLLECTION)


def test_fake_embedder_has_1024_dimensions():
    dense, sparse = FakeEmbedder().encode(["hello world"])
    assert len(dense[0]) == 1024
    assert len(sparse[0][0]) == 2  # two words -> two sparse indices
