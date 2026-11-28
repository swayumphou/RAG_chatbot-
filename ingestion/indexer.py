"""Step 03b: create the Qdrant collection and save chunks into it.

Collection contract (INTERFACES §4):
- named dense vector "dense": 1024 numbers, cosine distance, HNSW m=16 / ef_construct=200
- named sparse vector "lexical": bge-m3 keyword weights
- payload indexes for filters, plus a full-text index on author names

Point ids come from uuid5(NAMESPACE, chunk_id). The same chunk always gets the
same id, so running ingestion twice overwrites points instead of adding new ones.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient, models

from config import get_config
from ingestion.embedder import DENSE_DIM

logger = logging.getLogger(__name__)

NAMESPACE = uuid.UUID("6f2a1c3e-0000-4000-8000-000000000000")
# Payload fields that get a keyword index, so Qdrant can filter on them quickly.
KEYWORD_FIELDS = [
    "project_id",
    "doc_id",
    "doc_type",
    "program",
    "supervisor_id",
    "senior_ids",
    "domain",
    "tech_stack",
]
UPSERT_BATCH = 64


def collection_name(collection=None) -> str:
    return collection or get_config().kb_collection


def connect_qdrant(url=None, path=None) -> QdrantClient:
    """Connect to the Qdrant server, or open a local folder for smoke tests.

    Local mode (path=...) is only for quick tests. Its hybrid search may not
    behave exactly like the server, so never trust evaluation numbers from it.
    """
    if path:
        logger.warning("Using embedded Qdrant at %s: smoke tests only, not for evaluation", path)
        return QdrantClient(":memory:") if path == ":memory:" else QdrantClient(path=str(path))
    return QdrantClient(url=url or get_config().qdrant_url)


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, chunk_id))


def ensure_collection(client, collection=None):
    """Create the collection and its indexes if it does not exist yet."""
    name = collection_name(collection)
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config={
            "dense": models.VectorParams(
                size=DENSE_DIM,
                distance=models.Distance.COSINE,
                hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
            )
        },
        sparse_vectors_config={
            "lexical": models.SparseVectorParams(index=models.SparseIndexParams(on_disk=False))
        },
    )
    for field in KEYWORD_FIELDS:
        client.create_payload_index(name, field, field_schema=models.PayloadSchemaType.KEYWORD)
    client.create_payload_index(name, "year", field_schema=models.PayloadSchemaType.INTEGER)
    # The full-text index is what makes name lookup in Qdrant possible.
    client.create_payload_index(
        name,
        "author_names_normalized",
        field_schema=models.TextIndexParams(
            type=models.TextIndexType.TEXT,
            tokenizer=models.TokenizerType.WORD,
            min_token_len=2,
            lowercase=True,
        ),
    )
    logger.info("created Qdrant collection %s", name)


def recreate_collection(client, collection=None):
    """Delete and create the collection again (used by --rebuild)."""
    name = collection_name(collection)
    if client.collection_exists(name):
        client.delete_collection(name)
    ensure_collection(client, name)


def upsert_chunks(client, embedder, chunks, collection=None) -> int:
    """Embed the chunks and save them. Returns how many points were written."""
    name = collection_name(collection)
    written = 0
    for start in range(0, len(chunks), UPSERT_BATCH):
        batch = chunks[start : start + UPSERT_BATCH]
        dense, sparse = embedder.encode([c.text for c in batch])
        points = []
        for chunk, dense_vector, (indices, values) in zip(batch, dense, sparse, strict=True):
            points.append(
                models.PointStruct(
                    id=point_id(chunk.chunk_id),
                    vector={
                        "dense": dense_vector,
                        "lexical": models.SparseVector(indices=indices, values=values),
                    },
                    payload={**chunk.metadata, "text": chunk.text},
                )
            )
        client.upsert(name, points=points, wait=True)
        written += len(points)
    return written


def scroll_all(client, collection=None, fields=True):
    """Yield every point in the collection (payload only, no vectors)."""
    name = collection_name(collection)
    if not client.collection_exists(name):
        return
    offset = None
    while True:
        points, offset = client.scroll(
            name, limit=256, offset=offset, with_payload=fields, with_vectors=False
        )
        yield from points
        if offset is None:
            break


def count_points(client, collection=None) -> int:
    name = collection_name(collection)
    if not client.collection_exists(name):
        return 0
    return client.count(name, exact=True).count


def delete_stale_points(client, keep: dict[str, set[str]], collection=None) -> int:
    """Delete points whose project or chunk is no longer wanted.

    `keep` maps project_id -> chunk ids of that project. This removes:
    - projects that became pending, withdrawn, or left the manifest
    - old chunks of a document that now produces fewer chunks
    """
    name = collection_name(collection)
    stale = []
    for point in scroll_all(client, name, fields=["project_id", "chunk_id"]):
        project_id = point.payload.get("project_id")
        if point.payload.get("chunk_id") not in keep.get(project_id, set()):
            stale.append(point.id)
    for start in range(0, len(stale), 256):
        client.delete(
            name, points_selector=models.PointIdsList(points=stale[start : start + 256]), wait=True
        )
    return len(stale)
