"""ChromaDB persistence: one collection, filtered per document (or a set of documents)."""
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


async def search(document_id: str | None, query: str, k: int, document_ids: list[str] | None = None) -> list[dict]:
    """document_id -> one contract; document_ids -> a set of contracts; neither -> whole library."""
    [vec] = await llm.embed([query])
    kwargs: dict = {"query_embeddings": [vec], "n_results": k}
    if document_id:
        kwargs["where"] = {"document_id": document_id}
    elif document_ids:
        kwargs["where"] = {"document_id": {"$in": document_ids}}
    res = _col.query(**kwargs)
    return [
        {"text": d, "page": m["page"], "document_id": m["document_id"], "score": round(1 - dist, 3)}
        for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


async def replace_page(document_id: str, page: int, chunks: list[dict]) -> None:
    """Swap one page's chunks (after a handwriting re-read or a human correction)."""
    _col.delete(where={"$and": [{"document_id": document_id}, {"page": page}]})
    chunks = [c for c in chunks if c["text"].strip()]
    if not chunks:
        return
    vectors = await llm.embed([c["text"] for c in chunks])
    _col.add(
        ids=[f"{document_id}:p{page}:{i}" for i in range(len(chunks))],
        documents=[c["text"] for c in chunks],
        embeddings=vectors,
        metadatas=[{"document_id": document_id, "page": page} for _ in chunks],
    )


def delete_document(document_id: str) -> None:
    _col.delete(where={"document_id": document_id})