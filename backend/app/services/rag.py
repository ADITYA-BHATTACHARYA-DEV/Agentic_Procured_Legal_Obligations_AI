"""RAG pipeline: classify -> retrieve -> pick chat prompt -> stream -> persist.
Also answers questions across the whole contract library."""
import json
from datetime import date
from typing import AsyncIterator

from .. import database as dbx
from ..prompts.chat_prompts import CHAT_PROMPTS, CHAT_SETTINGS, LIBRARY_PROMPT
from . import llm, query_router, vector_store


def _format_context(hits: list[dict]) -> str:
    return "\n\n".join(f"[p.{h['page']}] {h['text']}" for h in hits) or "(no relevant text found)"


def _format_obligations(document_id: str) -> str:
    obs = dbx.rows(
        "SELECT o.description, o.owner, COALESCE(t.due_date, o.deadline) AS deadline, o.clause_ref, o.priority, t.status"
        " FROM obligations o JOIN tasks t ON t.obligation_id = o.id WHERE o.document_id=?"
        " ORDER BY t.due_date IS NULL, t.due_date", (document_id,))
    if not obs:
        return "(none extracted yet)"
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


def _parties(raw: str | None) -> str:
    try:
        return ", ".join(json.loads(raw or "[]")) or "unknown"
    except (ValueError, TypeError):
        return "unknown"


async def library_stream(question: str, document_ids: list[str] | None = None) -> AsyncIterator[str]:
    """Cross-contract chat: 'which contracts renew this quarter?', 'what do we owe Acme?'"""
    docs = dbx.rows("SELECT id, filename, doc_type, parties, effective_date, renewal_date, notice_days FROM documents")
    if document_ids:
        docs = [d for d in docs if d["id"] in document_ids]
    if not docs:
        yield json.dumps({"type": "token", "text": "There are no contracts in the library yet. Upload one first."}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"
        return

    names = {d["id"]: d["filename"] for d in docs}
    ids = list(names)
    hits = await vector_store.search(None, question, 6, ids)
    sources = [{"file": names.get(h["document_id"]), "page": h["page"]} for h in hits]
    yield json.dumps({"type": "meta", "sources": sources}) + "\n"

    portfolio = "\n".join(
        f"- {d['filename']} | {d['doc_type'] or 'Document'} | parties: {_parties(d['parties'])} | effective: "
        f"{d['effective_date'] or 'n/a'} | renewal/term end: {d['renewal_date'] or 'n/a'} | notice days: {d['notice_days'] or 'n/a'}"
        for d in docs[:30])
    marks = ",".join("?" * len(ids))
    obs = dbx.rows(
        "SELECT d.filename, t.title, t.assignee, t.due_date, o.priority, o.amount FROM tasks t"
        " JOIN obligations o ON o.id = t.obligation_id JOIN documents d ON d.id = t.document_id"
        f" WHERE t.status != 'done' AND t.document_id IN ({marks}) ORDER BY t.due_date IS NULL, t.due_date LIMIT 40", tuple(ids))
    obligations = "\n".join(
        f"- {o['filename']} | {o['title']} | owner: {o['assignee']} | due: {o['due_date'] or 'n/a'} | "
        f"priority: {o['priority']} | amount: {o['amount'] or 'n/a'}" for o in obs) or "(none)"
    context = "\n\n".join(f"[{names.get(h['document_id'])} p.{h['page']}] {h['text'][:600]}" for h in hits) or "(none)"

    system = LIBRARY_PROMPT.format(today=date.today().isoformat(), portfolio=portfolio, obligations=obligations, context=context)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    async for token in llm.chat_stream(messages, 0.1, num_ctx=8192):
        yield json.dumps({"type": "token", "text": token}) + "\n"
    yield json.dumps({"type": "done"}) + "\n"