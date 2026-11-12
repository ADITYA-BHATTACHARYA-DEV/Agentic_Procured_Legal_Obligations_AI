import asyncio
import io

import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

from app import database as dbx
from app.main import app
from app.services import vision


def test_script_detection_across_languages():
    assert vision.detect_script("ಇದು ಒಂದು ಟಿಪ್ಪಣಿ")[0] == "Kannada"
    assert vision.detect_script("यह एक नोट है")[0] == "Devanagari"
    assert vision.detect_script("இது ஒரு குறிப்பு")[0] == "Tamil"
    assert vision.detect_script("مرحبا بالعالم")[0] == "Arabic"
    assert vision.detect_script("Pay by 30 June")[0] == "Latin"
    assert vision.detect_script("12345 ...")[0] == "Unknown"


def test_normalize_keeps_indic_vowel_signs():
    assert vision.normalize_words("नमस्ते, दुनिया।") == ["नमस्ते", "दुनिया"]


def test_agreement_and_disagreements():
    score, diffs = vision.compare_passes("Pay 5000 by June", "Pay 5000 by June")
    assert score == 1.0 and diffs == []
    score, diffs = vision.compare_passes("Pay 5000 by June", "Pay 6000 by June")
    assert score < 1.0 and diffs == [{"pass1": "5000", "pass2": "6000"}]


def test_unreadable_values_are_critical():
    assert vision.has_unreadable_value("Due on 1[?] March")
    assert vision.has_unreadable_value("Amount [?]500")
    assert not vision.has_unreadable_value("Initial here [?] thanks")


def test_status_rules():
    ok = dict(script_match=True, legibility="good", n_uncertain=0, critical=False)
    assert vision.decide_status(0.95, **ok) == "verified"
    assert vision.decide_status(0.60, **ok) == "needs_review"
    assert vision.decide_status(None, **ok) == "needs_review"          # no second reading -> a person must check
    assert vision.decide_status(0.95, **{**ok, "critical": True}) == "needs_review"
    assert vision.decide_status(0.95, **{**ok, "legibility": "poor"}) == "needs_review"


def test_unverified_handwriting_is_kept_out_of_extraction():
    r = {"printed": "Clause 3", "handwritten": "pay now", "translation_en": None, "language": "English", "language_code": "en", "status": "needs_review"}
    assert vision.extraction_text(r) == "Clause 3"
    assert "UNVERIFIED" in vision.index_text(r)
    r["status"] = "confirmed"
    assert "pay now" in vision.extraction_text(r) and "UNVERIFIED" not in vision.index_text(r)


def test_non_english_uses_translation_once_verified():
    r = {"printed": "", "handwritten": "ಹಣ ಪಾವತಿಸಿ", "translation_en": "Pay the money", "language": "Kannada", "language_code": "kn", "status": "verified"}
    assert vision.extraction_text(r) == "Pay the money"
    assert "[English translation]" in vision.index_text(r)


def test_deskew_finds_the_tilt():
    img = Image.new("L", (600, 400), 255)
    d = ImageDraw.Draw(img)
    for y in range(40, 380, 34):
        d.rectangle([40, y, 560, y + 8], fill=0)          # ruled "text lines"
    tilted = img.rotate(3, fillcolor=255)
    assert vision.deskew_angle(tilted) == pytest.approx(-3.0, abs=1.01)


def _png() -> bytes:
    buf = io.BytesIO(); Image.new("RGB", (300, 200), "white").save(buf, "PNG"); return buf.getvalue()


def _fake_vision(first: dict, second: dict):
    answers = iter([first, second])
    async def fake(prompt, image, temperature=0.0):
        return next(answers)
    return fake


def test_read_page_verified_when_both_readings_agree(monkeypatch):
    first = {"printed": "Clause 8", "handwritten": "ಹಣ ಪಾವತಿಸಿ", "language": "Kannada", "language_code": "kn",
             "script": "Kannada", "legibility": "good", "translation_en": "Pay the money", "uncertain": []}
    monkeypatch.setattr(vision.llm, "vision_json", _fake_vision(first, {"text": "Clause 8 ಹಣ ಪಾವತಿಸಿ"}))
    r = asyncio.run(vision.read_page(_png()))
    assert r["ok"] and r["status"] == "verified" and r["script_detected"] == "Kannada" and r["agreement"] == 1.0


def test_read_page_needs_review_when_readings_disagree_or_script_is_wrong(monkeypatch):
    first = {"printed": "", "handwritten": "Pay 5000 by 12 June", "language": "Kannada", "language_code": "kn",
             "script": "Kannada", "legibility": "fair", "uncertain": []}
    monkeypatch.setattr(vision.llm, "vision_json", _fake_vision(first, {"text": "Pay 6000 by 17 June"}))
    r = asyncio.run(vision.read_page(_png()))
    assert r["status"] == "needs_review" and not r["script_match"]
    kinds = {f["kind"] for f in r["flagged"]}
    assert {"disagreement", "script_mismatch"} <= kinds


def test_read_page_reports_failure_when_model_returns_nothing(monkeypatch):
    monkeypatch.setattr(vision.llm, "vision_json", _fake_vision({}, {}))
    assert asyncio.run(vision.read_page(_png())) == {"ok": False}


def test_notes_api_confirm_flow(monkeypatch):
    called = {}
    async def fake_finalize(note_id, retranslate=False):
        called["args"] = (note_id, retranslate)
    monkeypatch.setattr(vision, "finalize_note", fake_finalize)
    dbx.execute("INSERT INTO documents (id, filename, status) VALUES ('hw1','notes.png','ready')")
    r = {"printed": "", "handwritten": "ಹಣ ಪಾವತಿಸಿ", "translation_en": "Pay the money", "language": "Kannada", "language_code": "kn",
         "script": "Kannada", "script_detected": "Kannada", "script_match": True, "legibility": "good", "agreement": 0.7,
         "flagged": [{"kind": "disagreement", "pass1": "a", "pass2": "b"}], "status": "needs_review"}
    vision.save_note("hw1", 1, r, {"note_kind": "instruction"})
    with TestClient(app) as c:
        notes = c.get("/api/documents/hw1/notes").json()
        assert notes[0]["status"] == "needs_review" and notes[0]["flagged"][0]["kind"] == "disagreement" and notes[0]["analysis"]["note_kind"] == "instruction"
        assert c.patch(f"/api/notes/{notes[0]['id']}", json={"handwritten": "ಹಣ ಪಾವತಿಸಿ ದಯವಿಟ್ಟು"}).json() == {"ok": True}
        assert c.get("/api/documents/hw1/notes").json()[0]["status"] == "confirmed"
        assert called["args"] == (notes[0]["id"], True)            # edited non-English text -> re-translate
        assert c.patch("/api/notes/9999", json={}).status_code == 404