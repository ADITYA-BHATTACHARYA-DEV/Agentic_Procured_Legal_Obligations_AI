from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import database as dbx
from ..services import rag

router = APIRouter(prefix="/api/chats", tags=["chats"])


class Ask(BaseModel):
    question: str


class Patch(BaseModel):
    title: str | None = None
    archived: bool | None = None


@router.get("")
def list_chats(archived: bool = False):
    return dbx.rows(
        "SELECT c.id, c.title, c.archived, c.updated_at, c.document_id, d.doc_type FROM chats c"
        " JOIN documents d ON d.id=c.document_id WHERE c.archived=? ORDER BY c.updated_at DESC",
        (1 if archived else 0,))


@router.get("/{chat_id}")
def get_chat(chat_id: str):
    chat = dbx.one("SELECT * FROM chats WHERE id=?", (chat_id,))
    if not chat:
        raise HTTPException(404, "Chat not found")
    msgs = dbx.rows("SELECT role, content, query_type, sources FROM messages WHERE chat_id=? ORDER BY id", (chat_id,))
    return {"chat": chat, "messages": msgs}


@router.patch("/{chat_id}")
def patch_chat(chat_id: str, body: Patch):
    if body.title is not None:
        dbx.execute("UPDATE chats SET title=? WHERE id=?", (body.title.strip()[:120] or "Untitled", chat_id))
    if body.archived is not None:
        dbx.execute("UPDATE chats SET archived=? WHERE id=?", (1 if body.archived else 0, chat_id))
    return {"ok": True}


@router.delete("/{chat_id}")
def delete_chat(chat_id: str):
    dbx.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    return {"ok": True}


@router.post("/{chat_id}/ask")
async def ask(chat_id: str, body: Ask):
    if not dbx.one("SELECT id FROM chats WHERE id=?", (chat_id,)):
        raise HTTPException(404, "Chat not found")
    return StreamingResponse(rag.answer_stream(chat_id, body.question), media_type="application/x-ndjson")
