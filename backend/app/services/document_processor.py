"""Text extraction from PDFs and images (OCR fallback) + page-aware chunking."""
import io
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PIL import Image

from ..config import settings

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}


def extract_pages(path: Path) -> list[dict]:
    """Return [{'page': 1, 'text': '...'}]. Scanned pages fall back to OCR."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        pages = []
        with fitz.open(path) as pdf:
            for i, page in enumerate(pdf, start=1):
                text = page.get_text("text").strip()
                if len(text) < 30:  # likely a scan
                    pix = page.get_pixmap(dpi=settings.ocr_dpi)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    text = pytesseract.image_to_string(img).strip()
                pages.append({"page": i, "text": text})
        return pages
    if ext in IMAGE_EXT:
        return [{"page": 1, "text": pytesseract.image_to_string(Image.open(path)).strip()}]
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
