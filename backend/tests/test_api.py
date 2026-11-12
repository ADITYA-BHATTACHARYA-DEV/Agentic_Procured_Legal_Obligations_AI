from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import database as dbx
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def seeded(client):
    soon = (date.today() + timedelta(days=5)).isoformat()
    late = (date.today() - timedelta(days=3)).isoformat()
    dbx.execute("INSERT INTO documents (id, filename, doc_type, parties, renewal_date, notice_days, status) VALUES (?,?,?,?,?,?,?)",
                ("d1", "acme.pdf", "SaaS", '["Acme Ltd"]', (date.today() + timedelta(days=60)).isoformat(), 30, "ready"))
    for title, due, pri in (("Pay invoice", soon, "high"), ("Send report", late, "medium"), ("Undated duty", None, "low")):
        oid = dbx.execute("INSERT INTO obligations (document_id, description, owner, deadline, priority) VALUES (?,?,?,?,?)",
                          ("d1", title, "Acme Ltd", due, pri))
        dbx.execute("INSERT INTO tasks (obligation_id, document_id, title, assignee, due_date) VALUES (?,?,?,?,?)",
                    (oid, "d1", title, "Acme Ltd", due))
    dbx.execute("INSERT INTO pages_fts (text, document_id, page) VALUES (?,?,?)",
                ("The supplier shall indemnify the customer against third party claims.", "d1", 2))


def test_summary_counts(client, seeded):
    s = client.get("/api/dashboard/summary").json()
    assert s["total"] == 3 and s["overdue"] == 1 and s["due_7"] == 1 and s["completion_rate"] == 0


def test_task_filters(client, seeded):
    assert len(client.get("/api/dashboard/tasks?priority=high").json()) == 1
    assert [t["title"] for t in client.get("/api/dashboard/tasks?status=overdue").json()] == ["Send report"]


def test_renewal_window(client, seeded):
    r = client.get("/api/dashboard/renewals").json()[0]
    assert r["days_to_renewal"] == 60 and r["days_to_notice"] == 30


def test_calendar_export_only_dated_tasks(client, seeded):
    res = client.get("/api/calendar.ics")
    assert res.headers["content-type"].startswith("text/calendar")
    assert res.text.count("BEGIN:VEVENT") == 2 and "attachment" in res.headers["content-disposition"]


def test_reminders_created_once(client, seeded):
    first = client.post("/api/reminders/run").json()
    again = client.post("/api/reminders/run").json()
    assert first["created"] >= 2 and again["created"] == 0
    assert len(client.get("/api/notifications").json()) >= 2


def test_owner_auto_assignment(client, seeded):
    client.post("/api/owners", json={"name": "Asha Rao", "email": "asha@example.com", "party": "Acme"})
    tasks = client.get("/api/dashboard/tasks").json()
    assert {t["assignee"] for t in tasks} == {"Asha Rao"} and tasks[0]["assignee_email"] == "asha@example.com"


def test_task_status_updates_completion_rate(client, seeded):
    task_id = client.get("/api/dashboard/tasks?q=Pay").json()[0]["id"]
    client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    assert client.get("/api/dashboard/summary").json()["done"] == 1


def test_tags_and_library_filter(client, seeded):
    client.post("/api/library/documents/d1/tags", json={"tag": "Vendor"})
    docs = client.get("/api/library/documents?tag=vendor").json()
    assert docs["tags"] == ["vendor"] and docs["documents"][0]["open_tasks"] >= 1
    assert client.get("/api/library/documents?tag=hr").json()["documents"] == []


def test_full_text_search(client, seeded):
    hits = client.get("/api/library/search?q=indemnify claims").json()
    assert hits and hits[0]["filename"] == "acme.pdf" and hits[0]["page"] == 2


def test_compare_rejects_same_document(client, seeded):
    assert client.post("/api/library/compare", json={"document_a": "d1", "document_b": "d1"}).status_code == 400