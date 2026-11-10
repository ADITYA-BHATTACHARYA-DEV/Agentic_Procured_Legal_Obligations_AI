import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import database as dbx
from ..config import settings
from ..services import document_processor as dp
from ..services import obligation_extractor as ox
from ..services import vector_store

router = APIRouter(prefix="/api", tags=["documents"])
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}


async def analyze_document(doc_id: str, pages: list[dict]) -> None:
    """Slow LLM work, run after the chat has already opened."""
    try:
        profile = await ox.profile_document(pages)           # SYSTEM prompt #1
        parties = [str(x) for x in (profile.get("parties") or [])]
        dbx.execute("UPDATE documents SET doc_type=?, parties=?, summary=? WHERE id=?",
                    (profile.get("doc_type"), dbx.dumps(parties), profile.get("summary"), doc_id))
        obligations = await ox.extract_obligations(pages, parties)   # SYSTEM prompt #2
        ox.save_obligations(doc_id, obligations)                     # SQLite + tasks
    except Exception as e:  # never leave a document stuck in "processing"
        print(f"[analyze_document] {doc_id}: {e}")
    finally:
        dbx.execute("UPDATE documents SET status='ready' WHERE id=?", (doc_id,))


@router.post("/documents/upload")
async def upload(background: BackgroundTasks, file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported file type {ext}. Upload a PDF or image.")
    doc_id = uuid.uuid4().hex
    path = settings.upload_dir / f"{doc_id}{ext}"
    path.write_bytes(await file.read())

    pages = dp.extract_pages(path)
    if not any(p["text"].strip() for p in pages):
        path.unlink(missing_ok=True)
        raise HTTPException(422, "No readable text found in this file.")

    dbx.execute(
        "INSERT INTO documents (id, filename, mime, path, parties, page_count, status) VALUES (?,?,?,?,?,?,?)",
        (doc_id, file.filename, file.content_type, str(path), "[]", len(pages), "processing"))
    await vector_store.index_chunks(doc_id, dp.chunk_pages(pages))   # ChromaDB: chat works from here

    chat_id = uuid.uuid4().hex
    dbx.execute("INSERT INTO chats (id, document_id, title) VALUES (?,?,?)", (chat_id, doc_id, file.filename))
    background.add_task(analyze_document, doc_id, pages)
    return {"document_id": doc_id, "chat_id": chat_id, "status": "processing"}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str):
    doc = dbx.one("SELECT id, filename, mime, doc_type, parties, summary, page_count, status FROM documents WHERE id=?", (doc_id,))
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/documents/{doc_id}/file")
def get_file(doc_id: str):
    doc = dbx.one("SELECT path, mime, filename FROM documents WHERE id=?", (doc_id,))
    if not doc:
        raise HTTPException(404, "Document not found")
    return FileResponse(doc["path"], media_type=doc["mime"], filename=doc["filename"], content_disposition_type="inline")


@router.get("/documents/{doc_id}/obligations")
def obligations(doc_id: str):
    return dbx.rows(
        "SELECT o.*, t.id AS task_id, t.status AS task_status FROM obligations o"
        " LEFT JOIN tasks t ON t.obligation_id=o.id WHERE o.document_id=?"
        " ORDER BY o.deadline IS NULL, o.deadline", (doc_id,))


class TaskUpdate(BaseModel):
    status: str  # todo | in_progress | done


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate):
    if body.status not in {"todo", "in_progress", "done"}:
        raise HTTPException(400, "Invalid status")
    dbx.execute("UPDATE tasks SET status=? WHERE id=?", (body.status, task_id))
    dbx.execute("UPDATE obligations SET status=? WHERE id=(SELECT obligation_id FROM tasks WHERE id=?)",
                ("completed" if body.status == "done" else "open", task_id))
    return {"ok": True}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    doc = dbx.one("SELECT path FROM documents WHERE id=?", (doc_id,))
    if doc:
        Path(doc["path"]).unlink(missing_ok=True)
        vector_store.delete_document(doc_id)
        dbx.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return {"ok": True}