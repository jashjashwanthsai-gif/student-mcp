"""Local, student-namespaced vector storage via ChromaDB.

Uses Chroma's bundled default embedding function (ONNX all-MiniLM-L6-v2,
runs locally) so no embeddings API key is needed for the MVP.

This is the only store of a student's academic history across sessions and
accounts: notes/syllabus chunks from ingestion, plus live write-backs
(`remember`, `update_learning_progress`, `save_session_summary`) from
whatever AI client is connected.
"""

import uuid

import chromadb
from chromadb.api.models.Collection import Collection

from student_adaptive_mcp import config
from student_adaptive_mcp.chunking import Chunk

# Distance below which a newly-written live memory is treated as a near-duplicate
# of something already stored and skipped. Heuristic, not exact - tuned loose
# enough to catch "said the same thing twice" without blocking genuinely new facts.
DUPLICATE_DISTANCE_THRESHOLD = 0.05


def get_chroma_client(student_name: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(config.get_chroma_dir(student_name)))


def get_collection(student_name: str) -> Collection:
    client = get_chroma_client(student_name)
    return client.get_or_create_collection(name=f"{student_name}_memory")


def reset_collection(student_name: str) -> Collection:
    client = get_chroma_client(student_name)
    try:
        client.delete_collection(name=f"{student_name}_memory")
    except Exception:
        pass
    return client.get_or_create_collection(name=f"{student_name}_memory")


def add_chunks(collection: Collection, chunks: list[Chunk]) -> None:
    if not chunks:
        return
    ids = [f"{c.metadata.get('source_file', 'unknown')}:{c.metadata.get('chunk_index', i)}" for i, c in enumerate(chunks)]
    collection.add(
        ids=ids,
        documents=[c.text for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )


def query_memories(collection: Collection, query: str, k: int = 5, where: dict | None = None) -> list[dict]:
    if collection.count() == 0:
        return []
    k = min(k, collection.count())
    results = collection.query(query_texts=[query], n_results=k, where=where)
    documents = results.get("documents") or [[]]
    metadatas = results.get("metadatas") or [[]]
    distances = results.get("distances") or [[]]

    flattened = []
    for text, meta, dist in zip(documents[0], metadatas[0], distances[0]):
        flattened.append({"text": text, "metadata": meta, "distance": dist})
    return flattened


def is_near_duplicate(collection: Collection, text: str) -> bool:
    nearest = query_memories(collection, text, k=1)
    return bool(nearest) and nearest[0]["distance"] < DUPLICATE_DISTANCE_THRESHOLD


def add_memory(collection: Collection, text: str, metadata: dict) -> str:
    """Write a single live-session memory (as opposed to bulk ingestion via add_chunks)."""
    memory_id = f"live:{uuid.uuid4().hex}"
    collection.add(ids=[memory_id], documents=[text], metadatas=[metadata])
    return memory_id
