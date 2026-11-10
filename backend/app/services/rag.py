"""RAG pipeline: classify -> retrieve -> pick chat prompt -> stream -> persist."""
import json
from datetime import date
from typing import AsyncIterator

from .. import database as dbx
from ..prompts.chat_prompts import CHAT_PROMPTS, CHAT_SETTINGS
from . import llm, query_router, vector_store


def _format_context(hits: list[dict]) -> str:
    return "\n\n".join(f"[p.{h['page']}] {h['text']}" for h in hits) or "(no relevant text found)"


def _format_obligations(document_id: str) -> str:
    obs = dbx.rows(
        "SELECT description, owner, deadline, clause_ref, priority, status FROM obligations"
        " WHERE document_id=? ORDER BY deadline IS NULL, deadline", (document_id,))
    if not obs:
        return "(none extracted)"
    return "\n".join(
        f"- {o['description']} | owner: {o['owner']} | deadline: {o['deadline'] or 'n/a'} | "
        f"clause: {o['clause_ref'] or 'n/a'} | priority: {o['priority']} | status: {o['status']}" for o in obs)


async def answer_stream(chat_id: str, question: str) -> AsyncIterator[str]:
    """Yields newline-delimited JSON events: meta, token..., done."""
    chat = dbx.one("SELECT * FROM chats WHERE id=?", (chat_id,))
    doc = dbx.one("SELECT * FROM documents WHERE id=?", (chat["document_id"],))

    qtype = await query_router.classify(question)
    cfg = CHAT_SETTINGS[qtype]
    hits = await vector_store.search(doc["id"], question, cfg["top_k"])
    sources = [{"page": h["page"], "score": h["score"], "snippet": h["text"][:160]} for h in hits]
    yield json.dumps({"type": "meta", "query_type": qtype, "sources": sources}) + "\n"

    system = CHAT_PROMPTS[qtype].format(
        today=date.today().isoformat(), doc_name=doc["filename"], doc_type=doc["doc_type"] or "Document",
        context=_format_context(hits), obligations=_format_obligations(doc["id"]))

    history = dbx.rows("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 6", (chat_id,))[::-1]
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": question}]

    dbx.execute("INSERT INTO messages (chat_id, role, content) VALUES (?,?,?)", (chat_id, "user", question))
    full = []
    async for token in llm.chat_stream(messages, cfg["temperature"]):
        full.append(token)
        yield json.dumps({"type": "token", "text": token}) + "\n"

    dbx.execute("INSERT INTO messages (chat_id, role, content, query_type, sources) VALUES (?,?,?,?,?)",
                (chat_id, "assistant", "".join(full), qtype, dbx.dumps(sources)))
    dbx.execute("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (chat_id,))
    yield json.dumps({"type": "done"}) + "\n"
