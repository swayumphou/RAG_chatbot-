"""Step 03a: turn text into vectors with bge-m3 (dense + sparse).

The SAME Embedder must encode the chunks and the queries. If the model version
differs, search quality drops without any error, so we record `revision`
in every ingestion report and evaluation run.

- dense:  1024 numbers per text (meaning)
- sparse: (token ids, weights) per text (keywords)

If the model cannot load, we raise EmbedderLoadError. We never quietly
switch to keyword-only search (Architecture §5.6).
"""

from __future__ import annotations

import logging
from pathlib import Path

from config import DEFAULT_EMBED_MODEL

logger = logging.getLogger(__name__)

DENSE_DIM = 1024
BATCH_SIZE = 16
MAX_LENGTH = 512


class EmbedderLoadError(RuntimeError):
    pass


class Embedder:
    """One bge-m3 model for the whole process.

    `model` lets tests pass a fake model with the same `encode()` method.
    """

    def __init__(
        self, model_name=DEFAULT_EMBED_MODEL, use_fp16=True, batch_size=BATCH_SIZE, model=None
    ):
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self.batch_size = batch_size
        self.model = model
        self.revision = getattr(model, "revision", None) or model_name

    def load(self):
        """Load bge-m3 the first time it is needed."""
        if self.model is not None:
            return self.model
        try:
            from FlagEmbedding import BGEM3FlagModel

            self.model = BGEM3FlagModel(self.model_name, use_fp16=self.use_fp16)
        except Exception as error:
            raise EmbedderLoadError(
                f"Could not load embedding model '{self.model_name}': {error}. "
                "Install requirements.txt. If PyTorch is blocked on this computer, "
                "a fallback needs docs/adr/ADR-002-embedding-runtime.md."
            ) from error
        self.revision = self._find_revision()
        logger.info("embedding model loaded: %s", self.revision)
        return self.model

    def _find_revision(self) -> str:
        """Model name plus the Hugging Face commit, when we can read it.

        The local Hugging Face cache keeps the model in a folder named after
        its commit: .../models--BAAI--bge-m3/snapshots/<commit>.
        """
        try:
            from huggingface_hub import snapshot_download

            folder = snapshot_download(self.model_name, local_files_only=True)
            commit = Path(folder).name
        except Exception:  # a local folder instead of a hub name, or no cache
            commit = None
        return f"{self.model_name}@{commit}" if commit else self.model_name

    def encode(self, texts):
        """Return (dense_vectors, sparse_pairs) for a list of texts.

        sparse_pairs[i] is (indices, values): token ids and their weights.
        """
        texts = list(texts)
        if not texts:
            return [], []
        model = self.load()
        dense, sparse = [], []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            output = model.encode(
                batch,
                batch_size=self.batch_size,
                max_length=MAX_LENGTH,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
            for vector in output["dense_vecs"]:
                vector = [float(x) for x in vector]
                if len(vector) != DENSE_DIM:
                    raise RuntimeError(f"dense vector has {len(vector)} numbers, not {DENSE_DIM}")
                dense.append(vector)
            for weights in output["lexical_weights"]:
                indices = sorted(int(token) for token in weights)
                values = [float(weights_value(weights, i)) for i in indices]
                sparse.append((indices, values))
        return dense, sparse

    def encode_query(self, text):
        dense, sparse = self.encode([text])
        return dense[0], sparse[0]


def weights_value(weights: dict, token_id: int) -> float:
    """FlagEmbedding uses string token ids as keys; tests may use ints."""
    if token_id in weights:
        return weights[token_id]
    return weights[str(token_id)]
