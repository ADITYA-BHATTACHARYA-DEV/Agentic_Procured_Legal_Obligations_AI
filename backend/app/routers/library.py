"""Library API: document list with tags, full-text search, cross-contract chat, comparison."""
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import database as dbx
from ..services import compare, rag

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/documents")
def list_documents(q: str = "", tag: str = ""):
    out = []
    for d in dbx.rows("SELECT id, filename, doc_type, parties, effective_date, renewal_date, status, created_at"
                      " FROM documents ORDER BY created_at DESC"):
        d["tags"] = [r["tag"] for r in dbx.rows("SELECT tag FROM tags WHERE document_id=? ORDER BY tag", (d["id"],))]
        haystack = f"{d['filename']} {d['parties'] or ''} {d['doc_type'] or ''}".lower()
        if (tag and tag not in d["tags"]) or (q and q.lower() not in haystack):
            continue
        d["open_tasks"] = dbx.one("SELECT COUNT(*) AS n FROM tasks WHERE document_id=? AND status != 'done'", (d["id"],))["n"]
        chat = dbx.one("SELECT id FROM chats WHERE document_id=? ORDER BY created_at LIMIT 1", (d["id"],))
        d["chat_id"] = chat["id"] if chat else None
        out.append(d)
    return {"documents": out, "tags": [r["tag"] for r in dbx.rows("SELECT DISTINCT tag FROM tags ORDER BY tag")]}


class TagIn(BaseModel):
    tag: str


@router.post("/documents/{doc_id}/tags")
def add_tag(doc_id: str, body: TagIn):
    tag = body.tag.strip().lower()[:30]
    if not tag:
        raise HTTPException(400, "Empty tag")
    dbx.execute("INSERT OR IGNORE INTO tags (document_id, tag) VALUES (?,?)", (doc_id, tag))
    return {"ok": True}


@router.delete("/documents/{doc_id}/tags")
def remove_tag(doc_id: str, tag: str):
    dbx.execute("DELETE FROM tags WHERE document_id=? AND tag=?", (doc_id, tag))
    return {"ok": True}


@router.get("/search")
def search(q: str):
    """Full-text search across every contract's text. Matches are wrapped in << >>."""
    words = re.findall(r"\w+", q)[:8]
    if not words:
        return []
    if dbx.HAS_FTS:
        return dbx.rows(
            "SELECT pages_fts.document_id AS document_id, pages_fts.page AS page,"
            " snippet(pages_fts, 0, '<<', '>>', '…', 24) AS snippet, d.filename AS filename"
            " FROM pages_fts JOIN documents d ON d.id = pages_fts.document_id"
            " WHERE pages_fts MATCH ? ORDER BY pages_fts.rank LIMIT 30", (" ".join(f'"{w}"' for w in words),))
    like = " AND ".join("p.text LIKE ?" for _ in words)  # fallback when SQLite has no FTS5
    rows = dbx.rows(f"SELECT p.document_id, p.page, p.text AS snippet, d.filename FROM pages_fts p"
                    f" JOIN documents d ON d.id = p.document_id WHERE {like} LIMIT 30", tuple(f"%{w}%" for w in words))
    for r in rows:
        r["snippet"] = r["snippet"][:240]
    return rows


class AskAll(BaseModel):
    question: str
    document_ids: list[str] | None = None


@router.post("/ask")
async def ask_all(body: AskAll):
    return StreamingResponse(rag.library_stream(body.question, body.document_ids), media_type="application/x-ndjson")


class CompareIn(BaseModel):
    document_a: str
    document_b: str


@router.post("/compare")
async def compare_documents(body: CompareIn):
    if body.document_a == body.document_b:
        raise HTTPException(400, "Choose two different contracts")
    for doc_id in (body.document_a, body.document_b):
        if not dbx.one("SELECT id FROM documents WHERE id=?", (doc_id,)):
            raise HTTPException(404, "Document not found")
    return StreamingResponse(compare.compare_stream(body.document_a, body.document_b), media_type="application/x-ndjson")