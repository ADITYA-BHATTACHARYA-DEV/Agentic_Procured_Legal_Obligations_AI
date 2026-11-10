"""Ingestion pipeline driven by the SYSTEM prompts: profile the doc, extract obligations,
store structured rows in SQLite and auto-create workflow tasks."""
from datetime import date

from .. import database as dbx
from ..config import settings
from ..prompts.system_prompts import DOCUMENT_PROFILE_PROMPT, OBLIGATION_EXTRACTION_PROMPT
from . import llm
from .document_processor import windows


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())[:120]


async def profile_document(pages: list[dict]) -> dict:
    head = "\n".join(p["text"] for p in pages[:3])[:6000]
    prompt = DOCUMENT_PROFILE_PROMPT.format(today=date.today().isoformat())
    return await llm.chat_json(prompt, head)


async def extract_obligations(pages: list[dict], parties: list[str]) -> list[dict]:
    system = OBLIGATION_EXTRACTION_PROMPT.format(today=date.today().isoformat(), parties=", ".join(parties) or "unknown")
    found, seen = [], set()
    for win in windows(pages, settings.extract_window):
        out = await llm.chat_json(system, win)
        for ob in out.get("obligations", []):
            if not isinstance(ob, dict) or not ob.get("description"):
                continue
            key = _norm(ob["description"])
            if key in seen:
                continue
            seen.add(key)
            found.append(ob)
    return found


def save_obligations(document_id: str, obligations: list[dict]) -> None:
    for ob in obligations:
        ob_id = dbx.execute(
            "INSERT INTO obligations (document_id, description, owner, deadline, clause_ref, category, priority, source_quote)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (document_id, ob.get("description"), ob.get("owner") or "Unspecified", ob.get("deadline"),
             ob.get("clause_ref"), ob.get("category") or "other", ob.get("priority") or "medium", ob.get("source_quote")),
        )
        # Workflow automation: every obligation becomes a trackable task
        dbx.execute(
            "INSERT INTO tasks (obligation_id, document_id, title, assignee, due_date) VALUES (?,?,?,?,?)",
            (ob_id, document_id, ob.get("description"), ob.get("owner") or "Unspecified", ob.get("deadline")),
        )