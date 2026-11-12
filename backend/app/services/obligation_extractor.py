"""Ingestion pipeline driven by the SYSTEM prompts: profile the doc, extract obligations,
resolve relative dates in code, expand recurring duties, auto-assign owners, create workflow tasks."""
from datetime import date, timedelta

from .. import database as dbx
from ..config import settings
from ..prompts.system_prompts import DOCUMENT_PROFILE_PROMPT, OBLIGATION_EXTRACTION_PROMPT
from . import dates, llm, owners
from .document_processor import windows


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())[:120]


def _pos_int(v) -> int | None:
    try:
        n = int(float(v))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


async def profile_document(pages: list[dict]) -> dict:
    head = "\n".join(p["text"] for p in pages[:3])[:6000]
    prompt = DOCUMENT_PROFILE_PROMPT.format(today=date.today().isoformat())
    return await llm.chat_json(prompt, head)


def normalise_profile(profile: dict) -> dict:
    """Clean the model's profile JSON and compute the renewal date in code."""
    effective = dates.parse_iso(profile.get("effective_date"))
    term_months = _pos_int(profile.get("term_months"))
    auto = profile.get("auto_renews")
    if isinstance(auto, str):
        auto = auto.strip().lower() in ("true", "yes")
    end = dates.term_end(effective, term_months)
    return {
        "doc_type": profile.get("doc_type"),
        "parties": [str(p) for p in (profile.get("parties") or [])],
        "summary": profile.get("summary"),
        "effective_date": effective.isoformat() if effective else None,
        "term_months": term_months,
        "auto_renews": None if auto is None else int(bool(auto)),
        "notice_days": _pos_int(profile.get("notice_period_days")),
        "renewal_date": end.isoformat() if end else None,
        "governing_law": profile.get("governing_law"),
    }


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


def save_obligations(document_id: str, obligations: list[dict], effective: date | None = None,
                     end: date | None = None) -> None:
    for ob in obligations:
        raw = ob.get("deadline")
        due = dates.resolve_deadline(raw, effective, end)
        rec = dates.normalize_recurrence(ob.get("recurrence"), ob.get("description") or "")
        party = ob.get("owner") or "Unspecified"
        person = owners.find_owner(party)
        ob_id = dbx.execute(
            "INSERT INTO obligations (document_id, description, owner, deadline, clause_ref, category, priority,"
            " source_quote, kind, due_date, recurrence, amount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (document_id, ob.get("description"), party, raw, ob.get("clause_ref"), ob.get("category") or "other",
             ob.get("priority") or "medium", ob.get("source_quote"), "obligation", due, rec, ob.get("amount")))

        schedule: list[date | None] = [dates.parse_iso(due)]
        if rec:  # split "pay quarterly" into repeating tasks
            start = dates.parse_iso(due) or (effective + dates.PERIODS[rec] if effective else None)
            schedule = dates.expand(start, rec, end) if start else [None]
        for d in schedule:
            title = ob.get("description") if not rec or d is None else f"{ob.get('description')} ({d.isoformat()})"
            dbx.execute(
                "INSERT INTO tasks (obligation_id, document_id, title, assignee, assignee_email, due_date) VALUES (?,?,?,?,?,?)",
                (ob_id, document_id, title, person["name"] if person else party,
                 person["email"] if person else None, d.isoformat() if d else None))


def save_renewal_notice(document_id: str, renewal_date: str | None, notice_days: int | None, auto_renews) -> None:
    """The costliest missed deadline: the last day to stop an automatic renewal."""
    if not (renewal_date and notice_days) or auto_renews == 0:
        return
    due = (date.fromisoformat(renewal_date) - timedelta(days=notice_days)).isoformat()
    desc = f"Last day to give {notice_days} days' notice to terminate or stop renewal (term ends {renewal_date})"
    ob_id = dbx.execute(
        "INSERT INTO obligations (document_id, description, owner, deadline, category, priority, kind, due_date)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (document_id, desc, "Unspecified", due, "renewal_termination", "high", "renewal_notice", due))
    dbx.execute("INSERT INTO tasks (obligation_id, document_id, title, assignee, due_date) VALUES (?,?,?,?,?)",
                (ob_id, document_id, desc, "Unspecified", due))