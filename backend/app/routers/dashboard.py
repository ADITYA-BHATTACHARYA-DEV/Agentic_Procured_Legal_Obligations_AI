"""Dashboard API: task roll-up across all contracts, renewals, calendar export, reminders, owners."""
import re
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import database as dbx
from ..services import calendar_ics, owners as owner_svc
from ..services import scheduler as sched

router = APIRouter(prefix="/api", tags=["dashboard"])
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TASK_SQL = (
    "SELECT t.id, t.title, t.assignee, t.assignee_email, t.due_date, t.status, t.document_id,"
    " o.priority, o.kind, o.clause_ref, o.recurrence, o.amount, o.deadline AS deadline_text, d.filename"
    " FROM tasks t JOIN obligations o ON o.id = t.obligation_id JOIN documents d ON d.id = t.document_id WHERE 1=1"
)


def query_tasks(status=None, owner=None, priority=None, document_id=None, q=None) -> list[dict]:
    sql, params = TASK_SQL, []
    for col, val in (("t.assignee", owner), ("o.priority", priority), ("t.document_id", document_id)):
        if val:
            sql += f" AND {col}=?"
            params.append(val)
    if q:
        sql += " AND (t.title LIKE ? OR d.filename LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if status in ("todo", "in_progress", "done"):
        sql += " AND t.status=?"
        params.append(status)
    elif status in ("open", "overdue"):
        sql += " AND t.status != 'done'"
    rows = dbx.rows(sql + " ORDER BY t.due_date IS NULL, t.due_date", tuple(params))
    if status == "overdue":
        today = date.today().isoformat()
        rows = [r for r in rows if r["due_date"] and ISO.match(r["due_date"]) and r["due_date"] < today]
    return rows


@router.get("/dashboard/tasks")
def tasks(status: str = "", owner: str = "", priority: str = "", document_id: str = "", q: str = ""):
    return query_tasks(status or None, owner or None, priority or None, document_id or None, q or None)


@router.get("/dashboard/summary")
def summary():
    all_tasks = dbx.rows("SELECT status, due_date, assignee FROM tasks")
    today = date.today()
    overdue = due_7 = due_30 = 0
    for t in all_tasks:
        d = t["due_date"]
        if t["status"] == "done" or not d or not ISO.match(d):
            continue
        if d < today.isoformat():
            overdue += 1
        elif d <= (today + timedelta(days=7)).isoformat():
            due_7 += 1
            due_30 += 1
        elif d <= (today + timedelta(days=30)).isoformat():
            due_30 += 1
    total, done = len(all_tasks), sum(1 for t in all_tasks if t["status"] == "done")
    return {
        "total": total, "done": done, "overdue": overdue, "due_7": due_7, "due_30": due_30,
        "completion_rate": round(100 * done / total, 1) if total else 0,
        "owners": sorted({t["assignee"] for t in all_tasks if t["assignee"]}),
        "documents": dbx.rows("SELECT id, filename FROM documents ORDER BY filename"),
    }


@router.get("/dashboard/renewals")
def renewals():
    """Renewal and notice windows: when the cancellation window closes for each contract."""
    today, out = date.today(), []
    for d in dbx.rows("SELECT id, filename, renewal_date, notice_days, auto_renews FROM documents WHERE renewal_date IS NOT NULL"):
        try:
            renewal = date.fromisoformat(d["renewal_date"])
        except ValueError:
            continue
        notice = renewal - timedelta(days=d["notice_days"] or 0)
        out.append({**d, "notice_date": notice.isoformat(), "days_to_notice": (notice - today).days,
                    "days_to_renewal": (renewal - today).days})
    return sorted(out, key=lambda r: r["notice_date"])


@router.get("/calendar.ics")
def calendar(status: str = "open", owner: str = "", priority: str = "", document_id: str = "", q: str = ""):
    rows = [t for t in query_tasks(status or None, owner or None, priority or None, document_id or None, q or None)
            if t["due_date"] and ISO.match(t["due_date"])]
    return Response(calendar_ics.build_ics(rows), media_type="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="obligo-deadlines.ics"'})


@router.post("/reminders/run")
async def run_reminders():
    return await sched.run_reminder_check()


@router.get("/notifications")
def notifications():
    return dbx.rows(
        "SELECT n.id, n.days_before, n.recipient, n.message, n.status, n.created_at, d.filename FROM notifications n"
        " LEFT JOIN tasks t ON t.id = n.task_id LEFT JOIN documents d ON d.id = t.document_id ORDER BY n.id DESC LIMIT 50")


class OwnerIn(BaseModel):
    name: str
    email: str = ""
    party: str


@router.get("/owners")
def list_owners():
    return dbx.rows("SELECT * FROM owners ORDER BY name")


@router.post("/owners")
def add_owner(body: OwnerIn):
    if not body.name.strip() or not body.party.strip():
        raise HTTPException(400, "Name and party are required")
    dbx.execute("INSERT INTO owners (name, email, party) VALUES (?,?,?)", (body.name.strip(), body.email.strip(), body.party.strip()))
    return {"updated": owner_svc.apply_mapping()}


@router.delete("/owners/{owner_id}")
def delete_owner(owner_id: int):
    dbx.execute("DELETE FROM owners WHERE id=?", (owner_id,))
    return {"updated": owner_svc.apply_mapping()}


@router.post("/owners/apply")
def apply_owners():
    return {"updated": owner_svc.apply_mapping()}