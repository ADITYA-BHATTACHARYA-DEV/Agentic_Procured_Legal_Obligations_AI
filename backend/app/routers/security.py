import asyncio
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import database as dbx
from ..config import settings
from ..services import audit, crypto

router = APIRouter(prefix="/api", tags=["security"])
MAX_BYTES = 12 * 1024 * 1024


def _passphrase(p: str) -> str:
    if len(p) < 8:
        raise HTTPException(400, "Use a passphrase of at least 8 characters.")
    return p


@router.post("/crypto/noise/encrypt")
async def noise_encrypt(request: Request, file: UploadFile = File(...), passphrase: str = Form(...)):
    """Turn an image (or any file) into keyed static, prove it recovers exactly, and report how random it looks."""
    _passphrase(passphrase)
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "That file is too large for the demo (12 MB limit).")
    is_image = (file.content_type or "").startswith("image/")
    try:
        if is_image:
            static, metrics = await asyncio.to_thread(crypto.scramble_image, data, passphrase)
        else:
            static = await asyncio.to_thread(crypto.scramble_file, data, passphrase, file.filename or "file",
                                             file.content_type or "application/octet-stream")
            metrics = {"entropy": {"static": round(crypto.png_entropy(static), 4), "ideal": 8.0}}
        back = await asyncio.to_thread(crypto.unscramble, static, passphrase)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Could not read that file as an image.")
    lossless = crypto.same_pixels(data, back["data"]) if is_image else back["data"] == data
    audit.log("noise.encrypt", file.filename or "", f"{'image' if is_image else 'file'}, {len(data)} bytes", request)
    return {
        "kind": "image" if is_image else "file", "lossless": lossless, "metrics": metrics, "bytes": len(data),
        "static_png": crypto.b64e(static), "recovered_png": crypto.b64e(back["data"]) if is_image else None,
    }


@router.post("/crypto/noise/decrypt")
async def noise_decrypt(request: Request, file: UploadFile = File(...), passphrase: str = Form(...)):
    """Recover the original from a static envelope. Fails closed on a wrong key or any change to the image."""
    png = await file.read()
    if len(png) > MAX_BYTES * 3:
        raise HTTPException(413, "That file is too large.")
    try:
        out = await asyncio.to_thread(crypto.unscramble, png, passphrase)
    except ValueError as e:
        audit.log("noise.decrypt.failed", file.filename or "", str(e), request)
        raise HTTPException(403, str(e))
    audit.log("noise.decrypt", file.filename or "", out["kind"], request)
    return Response(out["data"], media_type=out["type"], headers={
        "X-Obligo-Kind": out["kind"], "X-Obligo-Filename": quote(out["name"]),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(out['name'])}"})


@router.get("/security/status")
def security_status(request: Request):
    docs = dbx.one("SELECT COUNT(*) AS total, COALESCE(SUM(encrypted), 0) AS encrypted FROM documents")
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    return {
        "encryption_at_rest": settings.encrypt_at_rest, "key_source": crypto.key_source(),
        "documents_total": docs["total"], "documents_encrypted": int(docs["encrypted"]),
        "auth_enabled": bool(settings.obligo_api_token), "https": https,
        "cors_open": "*" in [o.strip() for o in settings.allowed_origins.split(",")],
    }


@router.get("/audit")
def audit_log():
    return dbx.rows("SELECT id, ts, actor, action, target, detail, ip FROM audit_log ORDER BY id DESC LIMIT 100")