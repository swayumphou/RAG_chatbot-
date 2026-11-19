"""The hybrid index: embeddings, the Qdrant collections, and search.

Dense   bge-m3 (1024-d, multilingual) served by Ollama. The same code encodes chunks and queries.
Sparse  BM25-style term weights computed here in plain Python; Qdrant applies the IDF part.
        Khmer has no spaces between words, so Khmer text is indexed as character trigrams.
Search  Qdrant fuses the dense and the sparse ranking with RRF. RRF only looks at ranks, so a weak
        keyword match can outrank a strong semantic one. Every fused candidate must therefore also
        pass the relevance gate on its OWN dense cosine: keywords can reorder relevant passages,
        but cannot add irrelevant ones.

Ingestion writes a collection to disk (index/qdrant). A chat copies it into memory when it starts and
releases the folder at once, so the terminal chat, the Telegram bot and the web page can all run at
the same time, and re-ingestion never collides with them. Restart a chat to load new data.
"""
import atexit
import re
import time
import unicodedata
import uuid
import zlib
from collections import Counter
from typing import Any

import httpx
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_qdrant import QdrantVectorStore, RetrievalMode, SparseEmbeddings, SparseVector
from qdrant_client import QdrantClient, models

import config

# --- embeddings --------------------------------------------------------------------------------

TOKEN_RE = re.compile(r"[a-z0-9]+|[ក-៿]+")
STOPWORDS = frozenset("a an and are as at be by can do does for from how i in is it me my of on or "
                      "the there to we what when where which who with you".split())
K1, B, AVG_TOKENS = 1.2, 0.75, 100                    # BM25 constants; chunks are ~100 tokens


def tokenize(text: str) -> list[str]:
    runs = TOKEN_RE.findall(unicodedata.normalize("NFKC", text).casefold())
    tokens = []
    for run in runs:
        if run[0] >= "ក":                        # Khmer run -> character trigrams
            tokens += [run[i:i + 3] for i in range(max(1, len(run) - 2))]
        elif run not in STOPWORDS:
            tokens.append(run[:-1] if len(run) > 3 and run.endswith("s") else run)  # courses -> course
    return tokens or runs                             # a query made only of stopwords keeps them


def sparse_vector(text: str, is_query: bool) -> SparseVector:
    counts = Counter(tokenize(text))
    length = sum(counts.values())
    weights: dict[int, float] = {}
    for token, tf in counts.items():
        value = 1.0 if is_query else tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / AVG_TOKENS))
        index = zlib.crc32(token.encode("utf-8"))     # stable token id (same in every process)
        weights[index] = max(weights.get(index, 0.0), value)
    return SparseVector(indices=list(weights), values=list(weights.values()))


class LexicalSparse(SparseEmbeddings):
    def embed_documents(self, texts: list[str]) -> list[SparseVector]:
        return [sparse_vector(t, is_query=False) for t in texts]

    def embed_query(self, text: str) -> SparseVector:
        return sparse_vector(text, is_query=True)


class OllamaDense(Embeddings):
    """Dense vectors from Ollama's /api/embed (returned L2-normalized)."""

    def __init__(self, model: str = config.EMBED_MODEL):
        self.model = model
        self._queries: dict[str, list[float]] = {}    # each search embeds the same query twice

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            response = httpx.post(f"{config.OLLAMA_URL}/api/embed",
                                  json={"model": self.model, "input": list(texts)},
                                  timeout=config.EMBED_TIMEOUT)
            response.raise_for_status()
        except httpx.HTTPError as exc:                # hard fail: never fall back to keyword-only
            raise RuntimeError(f"Embedding failed ({exc}). Is Ollama running, and is the model "
                               f"pulled?  ->  ollama pull {self.model}") from exc
        return response.json()["embeddings"]

    def embed_query(self, text: str) -> list[float]:
        if text not in self._queries:
            if len(self._queries) >= 256:
                self._queries.clear()
            self._queries[text] = self.embed_documents([text])[0]
        return self._queries[text]


# --- Qdrant collections ------------------------------------------------------------------------

NAMESPACE = uuid.UUID("6f2a1c3e-0000-4000-8000-000000000000")   # fixed -> stable point ids
RRF = models.RrfQuery(rrf=models.Rrf(k=config.RRF_K))


def open_disk() -> QdrantClient:
    """The on-disk index. One process at a time may hold it, so wait briefly if it is in use."""
    config.QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        try:
            return QdrantClient(path=str(config.QDRANT_PATH))
        except RuntimeError:                          # held by another process for a moment
            time.sleep(0.5)
    raise SystemExit("The index folder is in use by another program. Try again in a moment.")


def vector_store(client: QdrantClient, name: str, dense: OllamaDense) -> QdrantVectorStore:
    return QdrantVectorStore(client=client, collection_name=name, embedding=dense,
                             sparse_embedding=LexicalSparse(), retrieval_mode=RetrievalMode.HYBRID,
                             vector_name="dense", sparse_vector_name="lexical")


def write_collection(name: str, docs: list[Document], dense: OllamaDense) -> int:
    """Drop and rebuild one collection on disk. Point ids are deterministic, so nothing duplicates."""
    dim = len(dense.embed_query("dimension probe"))
    client = open_disk()
    try:
        if client.collection_exists(name):
            client.delete_collection(name)
        client.create_collection(
            collection_name=name,
            vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"lexical": models.SparseVectorParams(modifier=models.Modifier.IDF)})
        # On Windows, delete_collection cannot remove the old files while they are still open, and
        # qdrant-client ignores that error, so the new collection reloads the old points. Without this,
        # the chunks of a deleted source would stay searchable.
        leftover, _ = client.scroll(name, limit=1_000_000, with_payload=False)
        if leftover:
            client.delete(name, points_selector=models.PointIdsList(points=[p.id for p in leftover]))
        ids = [str(uuid.uuid5(NAMESPACE, d.metadata["chunk_id"])) for d in docs]
        vector_store(client, name, dense).add_documents(docs, ids=ids, batch_size=16)
        count = client.count(name, exact=True).count
    finally:
        client.close()
    if count != len(docs):
        raise RuntimeError(f"index integrity: {count} points != {len(docs)} chunks")
    return count


def read_collection(name: str) -> QdrantClient:
    """Copy one collection from disk into memory, then release the disk folder."""
    disk = open_disk()
    try:
        if not disk.collection_exists(name):
            raise SystemExit(f"There is no '{name}' index yet - run: python src\\ingest.py")
        params = disk.get_collection(name).config.params
        memory = QdrantClient(":memory:")
        memory.create_collection(name, vectors_config=params.vectors,
                                 sparse_vectors_config=params.sparse_vectors)
        offset = None
        while True:
            points, offset = disk.scroll(name, limit=256, offset=offset, with_payload=True, with_vectors=True)
            if points:
                memory.upsert(name, points=[models.PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                                            for p in points])
            if offset is None:
                break
    finally:
        disk.close()
    atexit.register(memory.close)
    return memory


# --- search ------------------------------------------------------------------------------------

def select(candidates: list[Document], threshold: float, top_k: int = config.TOP_K) -> list[Document]:
    """Keep fused order; require cosine >= threshold; at most one passage per (source, page)."""
    seen, passages = set(), []
    for doc in candidates:
        key = (doc.metadata["source_id"], doc.metadata["page"], doc.metadata.get("course_id"))
        if doc.metadata["cosine"] >= threshold and key not in seen:
            seen.add(key)
            passages.append(doc)
    return passages[:top_k]


class Searcher:
    def __init__(self, collection: str = "official"):
        self.name, self.dense = config.COLLECTIONS[collection], OllamaDense()
        self.client = read_collection(self.name)
        self.store = vector_store(self.client, self.name, self.dense)

    def candidates(self, query: str) -> list[Document]:
        """Fused (RRF) candidates in rank order, each tagged with its own dense cosine similarity."""
        dense_hits = self.client.query_points(
            self.name, query=self.dense.embed_query(query), using="dense",
            limit=config.FETCH_K, with_payload=False).points
        cosine = {str(p.id): p.score for p in dense_hits}
        fused = self.store.similarity_search_with_score(query, k=config.FETCH_K, hybrid_fusion=RRF)
        for doc, _ in fused:                          # keyword-only hits get 0.0 and never pass the gate
            doc.metadata["cosine"] = round(cosine.get(str(doc.metadata["_id"]), 0.0), 4)
        return [doc for doc, _ in fused]


class HybridRetriever(BaseRetriever):
    """The same search behind LangChain's retriever interface (`.invoke(query)`), for LCEL chains."""
    searcher: Any
    threshold: float = config.ABSTAIN_THRESHOLD
    top_k: int = config.TOP_K

    def _get_relevant_documents(self, query: str, *,
                                run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return select(self.searcher.candidates(query), self.threshold, self.top_k)
