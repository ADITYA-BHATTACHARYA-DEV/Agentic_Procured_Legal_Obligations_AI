"""Contract comparison: instant fact rows from the database, then one LLM-judged row per topic."""
import json
from typing import AsyncIterator

from .. import database as dbx
from ..prompts.chat_prompts import COMPARE_TOPIC_PROMPT
from . import llm, vector_store

TOPICS = [
    ("Payment terms", "payment invoices fees due late interest"),
    ("Termination", "termination notice cure period breach"),
    ("Liability and indemnity", "limitation of liability indemnify cap damages"),
    ("Confidentiality", "confidential information disclosure obligations"),
    ("Disputes", "dispute resolution arbitration jurisdiction courts"),
]


def _parties(raw) -> str:
    try:
        return ", ".join(json.loads(raw or "[]")) or "n/a"
    except (ValueError, TypeError):
        return "n/a"


def fact_rows(a: dict, b: dict) -> list[dict]:
    def count(doc_id: str, extra: str = "") -> str:
        return str(dbx.one(f"SELECT COUNT(*) AS n FROM obligations WHERE document_id=? {extra}", (doc_id,))["n"])

    pairs = [
        ("Contract type", a["doc_type"], b["doc_type"]),
        ("Parties", _parties(a["parties"]), _parties(b["parties"])),
        ("Effective date", a["effective_date"], b["effective_date"]),
        ("Initial term (months)", a["term_months"], b["term_months"]),
        ("Auto-renews", {1: "Yes", 0: "No"}.get(a["auto_renews"]), {1: "Yes", 0: "No"}.get(b["auto_renews"])),
        ("Notice period (days)", a["notice_days"], b["notice_days"]),
        ("Renewal / term end", a["renewal_date"], b["renewal_date"]),
        ("Governing law", a["governing_law"], b["governing_law"]),
        ("Tracked obligations", count(a["id"]), count(b["id"])),
        ("High-priority obligations", count(a["id"], "AND priority='high'"), count(b["id"], "AND priority='high'")),
    ]
    rows = []
    for topic, va, vb in pairs:
        va, vb = "n/a" if va in (None, "") else str(va), "n/a" if vb in (None, "") else str(vb)
        rows.append({"type": "row", "topic": topic, "a": va, "b": vb,
                     "difference": "Same" if va == vb else "Differs", "stricter": "", "source": "data"})
    return rows


async def compare_stream(a_id: str, b_id: str) -> AsyncIterator[str]:
    a, b = dbx.one("SELECT * FROM documents WHERE id=?", (a_id,)), dbx.one("SELECT * FROM documents WHERE id=?", (b_id,))
    yield json.dumps({"type": "meta", "a": a["filename"], "b": b["filename"]}) + "\n"
    for row in fact_rows(a, b):
        yield json.dumps(row) + "\n"
    for topic, query in TOPICS:
        try:
            ha, hb = await vector_store.search(a_id, query, 2), await vector_store.search(b_id, query, 2)
            ctx = lambda hits: "\n".join(f"[p.{h['page']}] {h['text'][:700]}" for h in hits) or "(nothing retrieved)"
            out = await llm.chat_json("You are a precise contract comparison engine.", COMPARE_TOPIC_PROMPT.format(
                topic=topic, name_a=a["filename"], name_b=b["filename"], ctx_a=ctx(ha), ctx_b=ctx(hb)))
            row = {"type": "row", "topic": topic, "a": out.get("a", "n/a"), "b": out.get("b", "n/a"),
                   "difference": out.get("difference", ""), "stricter": out.get("stricter", "unclear"), "source": "ai"}
        except Exception as e:
            row = {"type": "row", "topic": topic, "a": "n/a", "b": "n/a", "difference": f"Could not compare: {e}",
                   "stricter": "", "source": "ai"}
        yield json.dumps(row) + "\n"
    yield json.dumps({"type": "done"}) + "\n"