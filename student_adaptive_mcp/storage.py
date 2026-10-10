"""Student-namespaced vector storage via Qdrant (hosted, persists across
restarts/redeploys - unlike the earlier local-Chroma MVP). Embeddings are
computed locally via fastembed (ONNX, no API key, no per-call cost), same
zero-cost-embeddings property the original Chroma-based design had.

This is the only store of a student's academic history across sessions and
accounts: notes/syllabus chunks from ingestion, plus live write-backs
(`remember`, `save_session_summary`) from whatever AI client is connected.
The per-topic mastery trend is NOT stored here - see `progress.py`.
"""

import os
import uuid
from dataclasses import dataclass

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from student_adaptive_mcp.chunking import Chunk

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# Cosine-similarity score above which a newly-written live memory is treated
# as a near-duplicate of something already stored and skipped. Heuristic, not
# exact - catches "said the same thing twice" without blocking genuinely new
# but related facts.
DUPLICATE_SCORE_THRESHOLD = 0.95

_client: QdrantClient | None = None
_embedder: TextEmbedding | None = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=os.environ["QDRANT_URL"], api_key=os.environ["QDRANT_API_KEY"])
    return _client


def _get_embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    return _embedder


def _embed(texts: list[str]) -> list[list[float]]:
    return [vec.tolist() for vec in _get_embedder().embed(texts)]


def _collection_name(student_id: str) -> str:
    return f"student_{student_id}"


@dataclass
class StudentCollection:
    """A handle to one student's Qdrant collection - passed around the way a
    Chroma Collection object used to be, so call sites elsewhere don't change."""

    student_id: str
    collection_name: str


def get_collection(student_id: str) -> StudentCollection:
    client = _get_client()
    name = _collection_name(student_id)
    if not client.collection_exists(name):
        dim = len(_embed(["dimension probe"])[0])
        client.create_collection(collection_name=name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
    return StudentCollection(student_id=student_id, collection_name=name)


def reset_collection(student_id: str) -> StudentCollection:
    client = _get_client()
    name = _collection_name(student_id)
    if client.collection_exists(name):
        client.delete_collection(name)
    return get_collection(student_id)


def add_chunks(collection: StudentCollection, chunks: list[Chunk]) -> None:
    if not chunks:
        return
    client = _get_client()
    vectors = _embed([c.text for c in chunks])
    points = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        raw_id = f"{chunk.metadata.get('source_file', 'unknown')}:{chunk.metadata.get('chunk_index', i)}"
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, raw_id))
        points.append(PointStruct(id=point_id, vector=vector, payload={"text": chunk.text, **chunk.metadata}))
    client.upsert(collection_name=collection.collection_name, points=points)


def query_memories(collection: StudentCollection, query: str, k: int = 5) -> list[dict]:
    client = _get_client()
    count = client.count(collection.collection_name).count
    if count == 0:
        return []
    k = min(k, count)
    query_vector = _embed([query])[0]
    results = client.query_points(collection_name=collection.collection_name, query=query_vector, limit=k)

    flattened = []
    for point in results.points:
        payload = dict(point.payload or {})
        text = payload.pop("text", "")
        flattened.append({"text": text, "metadata": payload, "score": point.score})
    return flattened


def is_near_duplicate(collection: StudentCollection, text: str) -> bool:
    nearest = query_memories(collection, text, k=1)
    return bool(nearest) and nearest[0]["score"] > DUPLICATE_SCORE_THRESHOLD


def add_memory(collection: StudentCollection, text: str, metadata: dict) -> str:
    """Write a single live-session memory (as opposed to bulk ingestion via add_chunks)."""
    client = _get_client()
    vector = _embed([text])[0]
    memory_id = str(uuid.uuid4())
    client.upsert(
        collection_name=collection.collection_name,
        points=[PointStruct(id=memory_id, vector=vector, payload={"text": text, **metadata})],
    )
    return memory_id
