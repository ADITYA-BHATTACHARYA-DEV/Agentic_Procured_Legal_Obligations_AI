"""ChromaDB persistence: one collection, filtered per document_id."""
import chromadb

from ..config import settings
from . import llm

_client = chromadb.PersistentClient(path=str(settings.chroma_dir))
_col = _client.get_or_create_collection("contracts", metadata={"hnsw:space": "cosine"})


async def index_chunks(document_id: str, chunks: list[dict]) -> None:
    if not chunks:
        return
    for i in range(0, len(chunks), 32):  # batch embeddings
        batch = chunks[i : i + 32]
        vectors = await llm.embed([c["text"] for c in batch])
        _col.add(
            ids=[f"{document_id}:{c['idx']}" for c in batch],
            documents=[c["text"] for c in batch],
            embeddings=vectors,
            metadatas=[{"document_id": document_id, "page": c["page"]} for c in batch],
        )


async def search(document_id: str, query: str, k: int) -> list[dict]:
    [vec] = await llm.embed([query])
    res = _col.query(query_embeddings=[vec], n_results=k, where={"document_id": document_id})
    return [
        {"text": d, "page": m["page"], "score": round(1 - dist, 3)}
        for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def delete_document(document_id: str) -> None:
    _col.delete(where={"document_id": document_id})
