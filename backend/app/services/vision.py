"""On-prem vision pipeline for handwritten and multilingual pages.

  1. Image processing   two variants of the page: the original, and a cleaned copy
                        (grayscale, median denoise, deskew, autocontrast, sharpen)
  2. Read twice         a local vision model (Gemma via Ollama) reads each variant independently
  3. Verify             a) the two readings agree   b) the Unicode script matches the language claimed
                        c) dates and amounts have no unreadable digits   d) legibility and uncertainty
  4. Hand to Llama      verified text -> note analysis and obligation extraction.
                        Unverified handwriting is searchable but NEVER becomes a deadline until a person confirms it."""
import io
import json
import string
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .. import database as dbx
from ..config import settings
from ..prompts.system_prompts import (HANDWRITING_REREAD_PROMPT, HANDWRITING_TRANSCRIBE_PROMPT,
                                      NOTE_ANALYSIS_PROMPT, TRANSLATE_PROMPT)
from . import dates
from . import document_processor as dp
from . import llm, obligation_extractor as ox, vector_store

MAX_SIDE = 1600


# ───────────────────────── 1. image processing ─────────────────────────
def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def prepare_original(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))  # only ever shrinks
    return img


def deskew_angle(gray: Image.Image) -> float:
    """Best rotation in [-5, 5] degrees: the one that makes text rows sharpest (max row-projection variance)."""
    small = gray.copy()
    small.thumbnail((500, 500))
    bw = small.point(lambda p: 0 if p < 150 else 255)

    def score(angle: float) -> float:
        ink = np.array(bw.rotate(angle, fillcolor=255)) == 0
        return float(ink.sum(axis=1).astype(float).var())

    base = score(0.0)
    best_angle, best = 0.0, base
    for a in np.arange(-5.0, 5.5, 1.0):
        s = score(float(a))
        if s > best:
            best_angle, best = float(a), s
    return best_angle if best > base * 1.08 else 0.0


def enhance(img: Image.Image) -> Image.Image:
    g = ImageOps.grayscale(img).filter(ImageFilter.MedianFilter(3))  # remove speckle noise
    angle = deskew_angle(g)
    if angle:
        g = g.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    g = ImageOps.autocontrast(g, cutoff=2)
    g = g.filter(ImageFilter.UnsharpMask(radius=2, percent=140, threshold=3))
    return g.convert("RGB")


# ───────────────────────── 3. verification helpers ─────────────────────────
SCRIPT_RANGES = [
    ("Latin", 0x41, 0x5A), ("Latin", 0x61, 0x7A), ("Latin", 0xC0, 0x24F),
    ("Devanagari", 0x0900, 0x097F), ("Bengali", 0x0980, 0x09FF), ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF), ("Odia", 0x0B00, 0x0B7F), ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F), ("Kannada", 0x0C80, 0x0CFF), ("Malayalam", 0x0D00, 0x0D7F),
    ("Arabic", 0x0600, 0x06FF), ("Hebrew", 0x0590, 0x05FF), ("Cyrillic", 0x0400, 0x04FF),
    ("Greek", 0x0370, 0x03FF), ("Thai", 0x0E00, 0x0E7F), ("Hangul", 0xAC00, 0xD7AF),
    ("Han", 0x4E00, 0x9FFF), ("Hiragana/Katakana", 0x3040, 0x30FF),
]
PUNCT = set(string.punctuation) | set("।॥“”‘’…—–·•")


def detect_script(text: str) -> tuple[str, float]:
    """Dominant Unicode script of the letters in `text`, and its share (0-1)."""
    counts: dict[str, int] = {}
    for ch in text:
        o = ord(ch)
        for name, lo, hi in SCRIPT_RANGES:
            if lo <= o <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    total = sum(counts.values())
    if not total:
        return "Unknown", 0.0
    top = max(counts, key=counts.get)
    return top, counts[top] / total


def normalize_words(text: str) -> list[str]:
    t = unicodedata.normalize("NFC", (text or "").lower()).replace("[?]", " unk ")
    return "".join(" " if ch in PUNCT else ch for ch in t).split()  # keeps Indic vowel signs intact


def compare_passes(a: str, b: str) -> tuple[float, list[dict]]:
    """Word-level agreement between two independent readings, plus the places they disagree."""
    wa, wb = normalize_words(a), normalize_words(b)
    if not wa or not wb:
        return 0.0, []
    sm = SequenceMatcher(None, wa, wb, autojunk=False)
    diffs = [{"pass1": " ".join(wa[i1:i2]), "pass2": " ".join(wb[j1:j2])}
             for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
    return round(sm.ratio(), 3), diffs[:20]


def has_unreadable_value(text: str) -> bool:
    """An unreadable mark sitting next to a digit: a date or an amount we cannot trust."""
    return bool(re.search(r"\d\D{0,12}\[\?\]|\[\?\]\D{0,12}\d", text or ""))


def decide_status(agreement: float | None, script_match: bool, legibility: str, n_uncertain: int, critical: bool) -> str:
    if agreement is None:  # second reading was switched off: a person must check
        return "needs_review"
    ok = (agreement >= settings.vision_min_agreement and script_match and legibility != "poor"
          and n_uncertain <= 3 and not critical)
    return "verified" if ok else "needs_review"


# ───────────────────────── 2 + 3. read the page ─────────────────────────
async def read_page(png: bytes) -> dict:
    """Read one page image twice and verify it. Returns {"ok": False} if the vision model gave nothing."""
    original = prepare_original(Image.open(io.BytesIO(png)))
    p1 = await llm.vision_json(HANDWRITING_TRANSCRIBE_PROMPT, _png(original))
    printed, handwritten = (p1.get("printed") or "").strip(), (p1.get("handwritten") or "").strip()
    if not (printed or handwritten):
        return {"ok": False}

    combined = f"{printed}\n{handwritten}".strip()
    agreement, diffs = None, []
    if settings.vision_verify_passes >= 2:
        p2 = await llm.vision_json(HANDWRITING_REREAD_PROMPT, _png(enhance(original)), temperature=0.2)
        second = (p2.get("text") or "").strip()
        agreement, diffs = compare_passes(combined, second) if second else (0.0, [])

    claimed = (p1.get("script") or "").strip()
    detected, share = detect_script(handwritten or printed)
    script_match = share < 0.5 or claimed.lower().replace(" ", "") == detected.lower().replace(" ", "")
    legibility = (p1.get("legibility") or "fair").lower()
    uncertain = [str(u) for u in (p1.get("uncertain") or [])][:12]
    critical = has_unreadable_value(combined)

    flagged = [{"kind": "disagreement", **d} for d in diffs[:10]]
    flagged += [{"kind": "uncertain", "text": u} for u in uncertain]
    if critical:
        flagged.append({"kind": "unreadable_value", "text": "A date or amount contains an unreadable character"})
    if not script_match:
        flagged.append({"kind": "script_mismatch", "text": f"Model said {claimed}, but the text is mostly {detected}"})
    if legibility == "poor":
        flagged.append({"kind": "poor_legibility", "text": "The handwriting is hard to read"})

    translation = p1.get("translation_en")
    return {
        "ok": True, "printed": printed, "handwritten": handwritten,
        "translation_en": translation.strip() if isinstance(translation, str) and translation.strip() else None,
        "language": p1.get("language") or "Unknown", "language_code": (p1.get("language_code") or "").lower(),
        "script": claimed or "Unknown", "script_detected": detected, "script_match": script_match,
        "legibility": legibility, "agreement": agreement, "flagged": flagged,
        "status": decide_status(agreement, script_match, legibility, len(uncertain), critical),
    }


# ───────────────────────── 4. text for Llama, search and storage ─────────────────────────
def _is_english(r: dict) -> bool:
    return r.get("language_code") in ("en", "", None)


def index_text(r: dict) -> str:
    """What gets searched and embedded: original text, the note flagged by status, and an English translation."""
    parts = [r["printed"]]
    if r["handwritten"]:
        tag = "" if r["status"] in ("verified", "confirmed") else ", UNVERIFIED"
        parts.append(f"[Handwritten note, {r['language']}{tag}]\n{r['handwritten']}")
    if r.get("translation_en") and not _is_english(r):
        parts.append(f"[English translation]\n{r['translation_en']}")
    return "\n\n".join(x for x in parts if x)


def extraction_text(r: dict) -> str:
    """What Llama may turn into obligations. Unverified handwriting is left out until a person confirms it."""
    if r["status"] not in ("verified", "confirmed"):
        return r["printed"]
    if not _is_english(r) and r.get("translation_en"):
        return r["translation_en"]
    return f"{r['printed']}\n{r['handwritten']}".strip()


async def analyse_note(r: dict) -> dict:
    """Llama reads the note (via the English translation when needed) and classifies it."""
    if not r.get("handwritten"):
        return {}
    user = (f"PRINTED CONTEXT:\n{r['printed'][:1500]}\n\nHANDWRITTEN NOTE ({r['language']}):\n{r['handwritten']}\n\n"
            f"ENGLISH TRANSLATION OF THE PAGE:\n{(r.get('translation_en') or '(the page is already in English)')[:2500]}")
    try:
        return await llm.chat_json(NOTE_ANALYSIS_PROMPT.format(today=date.today().isoformat()), user)
    except Exception as e:
        print(f"[note analysis] {e}")
        return {}


def save_note(document_id: str, page: int, r: dict, analysis: dict) -> None:
    dbx.execute(
        "INSERT OR REPLACE INTO handwriting_notes (document_id, page, printed, handwritten, translation_en, language,"
        " language_code, script, script_detected, script_match, legibility, agreement, flagged, status, analysis)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (document_id, page, r["printed"], r["handwritten"], r["translation_en"], r["language"], r["language_code"],
         r["script"], r["script_detected"], int(r["script_match"]), r["legibility"], r["agreement"],
         dbx.dumps(r["flagged"]), r["status"], dbx.dumps(analysis)))


def note_as_result(n: dict) -> dict:
    return {"printed": n["printed"] or "", "handwritten": n["handwritten"] or "", "translation_en": n["translation_en"],
            "language": n["language"], "language_code": n["language_code"], "status": n["status"]}


async def reindex_page(document_id: str, page: int, text: str) -> None:
    dbx.execute("DELETE FROM pages_fts WHERE document_id=? AND page=?", (document_id, page))
    dbx.execute("INSERT INTO pages_fts (text, document_id, page) VALUES (?,?,?)", (text, document_id, page))
    await vector_store.replace_page(document_id, page, dp.chunk_pages([{"page": page, "text": text}]))


async def upgrade_pages(document_id: str, path: Path, pages: list[dict]) -> list[dict]:
    """Re-read every scanned page with the vision model. Returns the pages Llama may extract obligations from."""
    safe = []
    for p in pages:
        if not p.get("scanned"):
            safe.append(p)
            continue
        try:
            png = dp.render_page_png(path, p["page"])
            r = await read_page(png)
        except Exception as e:  # vision model missing or Ollama down: fall back to classic OCR
            print(f"[vision] page {p['page']}: {e}")
            r = {"ok": False}
        if not r["ok"]:
            text = p["text"] or dp.ocr_png(dp.render_page_png(path, p["page"]))
            if text:
                await reindex_page(document_id, p["page"], text)
            safe.append({"page": p["page"], "text": text, "scanned": True})
            continue
        analysis = await analyse_note(r)
        save_note(document_id, p["page"], r, analysis)
        await reindex_page(document_id, p["page"], index_text(r))
        safe.append({"page": p["page"], "text": extraction_text(r), "scanned": True})
    return safe


async def finalize_note(note_id: int, retranslate: bool = False) -> None:
    """After a person confirms or corrects a note: re-index it, re-analyse it, and create its tasks (once)."""
    n = dbx.one("SELECT * FROM handwriting_notes WHERE id=?", (note_id,))
    if not n:
        return
    r = note_as_result(n)
    if retranslate and not _is_english(r):  # the text was edited, so the old translation is stale
        out = await llm.chat_json(TRANSLATE_PROMPT, f"{r['printed']}\n{r['handwritten']}".strip(), model=settings.vision_model)
        r["translation_en"] = out.get("translation_en") or r["translation_en"]
        dbx.execute("UPDATE handwriting_notes SET translation_en=? WHERE id=?", (r["translation_en"], note_id))
    await reindex_page(n["document_id"], n["page"], index_text(r))
    dbx.execute("UPDATE handwriting_notes SET analysis=? WHERE id=?", (dbx.dumps(await analyse_note(r)), note_id))
    if not n["actions_applied"]:
        doc = dbx.one("SELECT parties, effective_date, renewal_date FROM documents WHERE id=?", (n["document_id"],))
        try:
            parties = [str(x) for x in json.loads(doc["parties"] or "[]")]
        except ValueError:
            parties = []
        obligations = await ox.extract_obligations([{"page": n["page"], "text": extraction_text(r)}], parties)
        ox.save_obligations(n["document_id"], obligations, dates.parse_iso(doc["effective_date"]), dates.parse_iso(doc["renewal_date"]))
        dbx.execute("UPDATE handwriting_notes SET actions_applied=1 WHERE id=?", (note_id,))


async def health() -> dict:
    out = {"enabled": settings.vision_enabled, "model": settings.vision_model, "available": False}
    try:
        listing = await llm.client.list()
        models = getattr(listing, "models", None) or listing.get("models", [])
        names = [getattr(m, "model", None) or m.get("model") for m in models]
        want = settings.vision_model
        out["available"] = any(n == want or (":" not in want and str(n).startswith(want + ":")) for n in names)
    except Exception:
        pass
    return out