import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import database as dbx
from ..config import settings
from ..services import audit, crypto, dates
from ..services import document_processor as dp
from ..services import obligation_extractor as ox
from ..services import vector_store, vision

router = APIRouter(prefix="/api", tags=["documents"])
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}


async def analyze_document(doc_id: str, pages: list[dict], path: Path, cleanup: bool = False) -> None:
    """Slow model work, run after the chat has already opened."""
    try:
        if settings.vision_enabled and any(p.get("scanned") for p in pages):
            pages = await vision.upgrade_pages(doc_id, path, pages)   # vision model reads handwriting; Llama gets verified text only
        if not any(p["text"].strip() for p in pages):
            dbx.execute("UPDATE documents SET doc_type=?, summary=? WHERE id=?",
                        ("Handwritten notes", "The handwriting on this page has not been verified yet. Review it in the Handwriting tab; tasks are created once you confirm it.", doc_id))
            return
        prof = ox.normalise_profile(await ox.profile_document(pages))          # SYSTEM prompt #1
        dbx.execute(
            "UPDATE documents SET doc_type=?, parties=?, summary=?, effective_date=?, term_months=?, auto_renews=?,"
            " notice_days=?, renewal_date=?, governing_law=? WHERE id=?",
            (prof["doc_type"], dbx.dumps(prof["parties"]), prof["summary"], prof["effective_date"], prof["term_months"],
             prof["auto_renews"], prof["notice_days"], prof["renewal_date"], prof["governing_law"], doc_id))
        effective, end = dates.parse_iso(prof["effective_date"]), dates.parse_iso(prof["renewal_date"])
        obligations = await ox.extract_obligations(pages, prof["parties"])      # SYSTEM prompt #2
        ox.save_obligations(doc_id, obligations, effective, end)                # dates resolved, recurring split, owners mapped
        ox.save_renewal_notice(doc_id, prof["renewal_date"], prof["notice_days"], prof["auto_renews"])
    except Exception as e:  # never leave a document stuck in "processing"
        print(f"[analyze_document] {doc_id}: {e}")
    finally:
        dbx.execute("UPDATE documents SET status='ready' WHERE id=?", (doc_id,))
        if cleanup:
            crypto.shred(path)  # the plaintext scratch copy is wiped as soon as processing ends


@router.post("/documents/upload")
async def upload(request: Request, background: BackgroundTasks, file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported file type {ext}. Upload a PDF or image.")
    doc_id = uuid.uuid4().hex
    scratch = settings.tmp_dir / f"{doc_id}{ext}"   # plaintext exists only here, and only while we work on it
    scratch.write_bytes(await file.read())

    pages = dp.extract_pages(scratch, ocr=not settings.vision_enabled)
    if not any(p["text"].strip() for p in pages) and not (settings.vision_enabled and any(p["scanned"] for p in pages)):
        crypto.shred(scratch)
        raise HTTPException(422, "No readable text found in this file.")

    if settings.encrypt_at_rest:
        dek = crypto.new_dek()
        stored = settings.upload_dir / f"{doc_id}{ext}.enc"
        crypto.encrypt_file(scratch, stored, dek, doc_id)            # AES-256-GCM, key wrapped by the master key
        wrapped, encrypted = crypto.wrap_dek(dek, doc_id), 1
    else:
        stored = settings.upload_dir / f"{doc_id}{ext}"
        shutil.move(str(scratch), stored)
        scratch, wrapped, encrypted = stored, None, 0

    dbx.execute(
        "INSERT INTO documents (id, filename, mime, path, parties, page_count, status, encrypted, wrapped_key) VALUES (?,?,?,?,?,?,?,?,?)",
        (doc_id, file.filename, file.content_type, str(stored), "[]", len(pages), "processing", encrypted, wrapped))
    for p in pages:  # full-text search index (SQLite FTS5)
        dbx.execute("INSERT INTO pages_fts (text, document_id, page) VALUES (?,?,?)", (p["text"], doc_id, p["page"]))
    await vector_store.index_chunks(doc_id, dp.chunk_pages(pages))   # ChromaDB: chat works from here

    chat_id = uuid.uuid4().hex
    dbx.execute("INSERT INTO chats (id, document_id, title) VALUES (?,?,?)", (chat_id, doc_id, file.filename))
    background.add_task(analyze_document, doc_id, pages, scratch, bool(encrypted))
    audit.log("document.upload", file.filename, f"encrypted={bool(encrypted)}", request)
    return {"document_id": doc_id, "chat_id": chat_id, "status": "processing"}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str):
    doc = dbx.one("SELECT id, filename, mime, doc_type, parties, summary, page_count, status FROM documents WHERE id=?", (doc_id,))
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/documents/{doc_id}/file")
def get_file(doc_id: str, request: Request):
    doc = dbx.one("SELECT id, path, mime, filename, encrypted, wrapped_key FROM documents WHERE id=?", (doc_id,))
    if not doc:
        raise HTTPException(404, "Document not found")
    audit.log("document.view", doc["filename"], "", request)
    if doc["encrypted"]:
        try:
            data = crypto.decrypt_file(Path(doc["path"]), doc["wrapped_key"], doc["id"])
        except Exception:
            raise HTTPException(500, "Could not decrypt this document. Is the master key the one it was encrypted with?")
        return Response(data, media_type=doc["mime"] or "application/octet-stream",
                        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc['filename'])}"})
    return FileResponse(doc["path"], media_type=doc["mime"], filename=doc["filename"], content_disposition_type="inline")  # older, unencrypted uploads


@router.get("/documents/{doc_id}/obligations")
def obligations(doc_id: str):
    """One row per TASK (a recurring obligation yields several), with the resolved date when known."""
    return dbx.rows(
        "SELECT o.id, o.priority, o.clause_ref, o.category, o.kind, o.recurrence,"
        " COALESCE(t.due_date, o.deadline) AS deadline, t.title AS description,"
        " COALESCE(t.assignee, o.owner) AS owner, t.id AS task_id, t.status AS task_status"
        " FROM obligations o JOIN tasks t ON t.obligation_id = o.id WHERE o.document_id=?"
        " ORDER BY t.due_date IS NULL, t.due_date", (doc_id,))


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
def delete_document(doc_id: str, request: Request):
    doc = dbx.one("SELECT path FROM documents WHERE id=?", (doc_id,))
    if doc:
        Path(doc["path"]).unlink(missing_ok=True)
        vector_store.delete_document(doc_id)
        dbx.execute("DELETE FROM pages_fts WHERE document_id=?", (doc_id,))
        dbx.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        audit.log("document.delete", doc_id, "", request)
    return {"ok": True}