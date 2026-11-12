"""Text extraction from PDFs and images (OCR fallback) + page-aware chunking."""
import io
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PIL import Image

from ..config import settings

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}


def ocr_png(png: bytes) -> str:
    """Classic OCR (Tesseract). Returns "" if Tesseract is not installed."""
    try:
        return pytesseract.image_to_string(Image.open(io.BytesIO(png))).strip()
    except Exception:
        return ""


def render_page_png(path: Path, page_no: int) -> bytes:
    """One page (or the image itself) as PNG bytes, for the vision model."""
    if path.suffix.lower() == ".pdf":
        with fitz.open(path) as pdf:
            return pdf[page_no - 1].get_pixmap(dpi=settings.ocr_dpi).tobytes("png")
    buf = io.BytesIO()
    Image.open(path).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def extract_pages(path: Path, ocr: bool = True) -> list[dict]:
    """Return [{'page': 1, 'text': '...', 'scanned': bool}]. Scanned pages and images are flagged;
    with ocr=False their text is left empty so the vision model can read them instead."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        pages = []
        with fitz.open(path) as pdf:
            for i, page in enumerate(pdf, start=1):
                text = page.get_text("text").strip()
                scanned = len(text) < 30  # no usable text layer
                if scanned and ocr:
                    text = ocr_png(page.get_pixmap(dpi=settings.ocr_dpi).tobytes("png"))
                pages.append({"page": i, "text": text, "scanned": scanned})
        return pages
    if ext in IMAGE_EXT:
        text = ocr_png(render_page_png(path, 1)) if ocr else ""
        return [{"page": 1, "text": text, "scanned": True}]
    raise ValueError(f"Unsupported file type: {ext}")


def chunk_pages(pages: list[dict]) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " "],
    )
    chunks = []
    for p in pages:
        for piece in splitter.split_text(p["text"]):
            chunks.append({"page": p["page"], "text": piece, "idx": len(chunks)})
    return chunks


def windows(pages: list[dict], size: int = 3500) -> list[str]:
    """Group pages into larger windows for obligation extraction (keeps [p.N] markers)."""
    out, buf = [], ""
    for p in pages:
        block = f"\n[p.{p['page']}]\n{p['text']}\n"
        if len(buf) + len(block) > size and buf:
            out.append(buf)
            buf = ""
        buf += block
    if buf.strip():
        out.append(buf)
    return out