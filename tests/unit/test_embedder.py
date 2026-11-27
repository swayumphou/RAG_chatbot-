"""Tests for the bge-m3 embedder (T2.7). A fake model is used; nothing is downloaded."""

import sys

import pytest

from ingestion.embedder import DENSE_DIM, Embedder, EmbedderLoadError


class FakeModel:
    """Has the same encode() signature as FlagEmbedding's BGEM3FlagModel."""

    def __init__(self, dim=DENSE_DIM):
        self.dim = dim
        self.calls = []

    def encode(self, texts, **options):
        self.calls.append((list(texts), options))
        return {
            "dense_vecs": [[0.5] * self.dim for _ in texts],
            "lexical_weights": [{"7": 0.25, "3": 0.5} for _ in texts],
        }


def test_dense_is_1024_and_sparse_is_int_float_pairs():
    embedder = Embedder(model=FakeModel())
    dense, sparse = embedder.encode(["chunk one", "a query"])
    assert len(dense) == 2 and all(len(v) == DENSE_DIM for v in dense)
    indices, values = sparse[0]
    assert indices == [3, 7]
    assert all(isinstance(i, int) for i in indices)
    assert all(isinstance(v, float) for v in values)
    assert values == [0.5, 0.25]


def test_same_settings_for_chunks_and_queries():
    model = FakeModel()
    embedder = Embedder(model=model)
    embedder.encode(["a chunk"])
    embedder.encode_query("a query")
    for _, options in model.calls:
        assert options["return_dense"] and options["return_sparse"]
        assert options["return_colbert_vecs"] is False
        assert options["max_length"] == 512


def test_large_inputs_are_batched():
    model = FakeModel()
    embedder = Embedder(model=model, batch_size=16)
    dense, _ = embedder.encode([f"text {i}" for i in range(40)])
    assert len(dense) == 40
    assert [len(texts) for texts, _ in model.calls] == [16, 16, 8]


def test_wrong_dimension_is_an_error():
    with pytest.raises(RuntimeError, match="1024"):
        Embedder(model=FakeModel(dim=384)).encode(["text"])


def test_load_failure_is_loud_and_names_the_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "FlagEmbedding", None)  # makes the import fail
    embedder = Embedder("BAAI/bge-m3")
    with pytest.raises(EmbedderLoadError, match="BAAI/bge-m3"):
        embedder.encode(["text"])


def test_empty_input():
    assert Embedder(model=FakeModel()).encode([]) == ([], [])
