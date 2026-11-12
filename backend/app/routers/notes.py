import json as _json

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import database as dbx
from ..services import vision

router = APIRouter(prefix="/api", tags=["handwriting"])


def _load(n: dict) -> dict:
    for key, default in (("flagged", []), ("analysis", {})):
        try:
            n[key] = _json.loads(n[key] or "null") or default
        except ValueError:
            n[key] = default
    n["script_match"] = bool(n["script_match"])
    return n


@router.get("/documents/{doc_id}/notes")
def list_notes(doc_id: str):
    return [_load(n) for n in dbx.rows("SELECT * FROM handwriting_notes WHERE document_id=? ORDER BY page", (doc_id,))]


class NoteConfirm(BaseModel):
    handwritten: str | None = None
    translation_en: str | None = None


@router.patch("/notes/{note_id}")
def confirm_note(note_id: int, body: NoteConfirm, background: BackgroundTasks):
    """A person has checked the reading (and maybe corrected it). It becomes searchable as confirmed and its tasks are created."""
    n = dbx.one("SELECT * FROM handwriting_notes WHERE id=?", (note_id,))
    if not n:
        raise HTTPException(404, "Note not found")
    handwritten = body.handwritten if body.handwritten is not None else n["handwritten"]
    translation = body.translation_en if body.translation_en is not None else n["translation_en"]
    edited = body.handwritten is not None and body.handwritten.strip() != (n["handwritten"] or "").strip()
    retranslate = edited and body.translation_en is None and n["language_code"] not in ("en", "", None)
    dbx.execute("UPDATE handwriting_notes SET handwritten=?, translation_en=?, status='confirmed' WHERE id=?",
                (handwritten, translation, note_id))
    background.add_task(vision.finalize_note, note_id, retranslate)
    return {"ok": True}


@router.post("/vision/preview")
async def preview(file: UploadFile = File(...)):
    """Read and verify one handwriting image without saving anything. Handy for testing the model."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Upload an image (PNG, JPG, WEBP).")
    result = await vision.read_page(await file.read())
    if not result["ok"]:
        raise HTTPException(422, "The vision model returned no text. Is the model installed? Try: ollama pull " + vision.settings.vision_model)
    return result


@router.get("/vision/health")
async def vision_health():
    return await vision.health()