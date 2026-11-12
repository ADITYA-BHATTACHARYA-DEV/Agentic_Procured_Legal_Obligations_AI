"""Daily deadline reminders at 30 / 7 / 1 days and when overdue.
Every reminder is written to the notifications table (shown in the dashboard);
email (SMTP) and Slack (webhook) are used too when configured in .env."""
import asyncio
import smtplib
from datetime import date
from email.message import EmailMessage

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .. import database as dbx
from ..config import settings

scheduler = AsyncIOScheduler()
DUE_SQL = (
    "SELECT t.id, t.title, t.assignee, t.assignee_email, t.due_date, d.filename FROM tasks t"
    " JOIN documents d ON d.id = t.document_id"
    " WHERE t.status != 'done' AND t.due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
)


def bucket_for(days: int) -> int | None:
    """Which reminder a task is due for. Robust to downtime: a missed 30-day mark still fires later."""
    if days < -30:
        return None
    if days <= 0:
        return 0
    if days <= 1:
        return 1
    if days <= 7:
        return 7
    if days <= 30:
        return 30
    return None


def _send_email(to: str, subject: str, body: str) -> str:
    if not (settings.smtp_host and to):
        return "skipped"
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return "sent"
    except Exception:
        return "failed"


async def _send_slack(text: str) -> str:
    if not settings.slack_webhook_url:
        return "skipped"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            return "sent" if (await c.post(settings.slack_webhook_url, json={"text": text})).is_success else "failed"
    except Exception:
        return "failed"


async def run_reminder_check(today: date | None = None) -> dict:
    today = today or date.today()
    created = 0
    try:
        rows = dbx.rows(DUE_SQL)
        for t in rows:
            days = (date.fromisoformat(t["due_date"]) - today).days
            bucket = bucket_for(days)
            if bucket is None or dbx.one("SELECT 1 AS x FROM notifications WHERE task_id=? AND days_before=?", (t["id"], bucket)):
                continue
            when = "is overdue" if days < 0 else "is due today" if days == 0 else f"is due in {days} day{'s' if days != 1 else ''}"
            text = f"[Obligo] {t['title']} ({t['filename']}) {when} ({t['due_date']}). Owner: {t['assignee'] or 'Unassigned'}."
            recipient = t["assignee_email"] or settings.default_notify_email
            email = await asyncio.to_thread(_send_email, recipient, f"Contract deadline: {t['title'][:60]}", text)
            slack = await _send_slack(text)
            dbx.execute("INSERT INTO notifications (task_id, days_before, channel, recipient, message, status) VALUES (?,?,?,?,?,?)",
                        (t["id"], bucket, "in-app, email, slack", recipient, text, f"email:{email}, slack:{slack}"))
            created += 1
        return {"checked": len(rows), "created": created}
    except Exception as e:  # a reminder failure must never take the API down
        print(f"[reminders] {e}")
        return {"checked": 0, "created": created, "error": str(e)}