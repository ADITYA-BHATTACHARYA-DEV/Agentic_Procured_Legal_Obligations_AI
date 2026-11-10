"""SQLite layer: documents, chats, messages, obligations, workflow tasks."""
import json
import sqlite3
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, filename TEXT, mime TEXT, path TEXT,
    doc_type TEXT, parties TEXT, summary TEXT, page_count INTEGER, status TEXT DEFAULT 'ready',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT, archived INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT, content TEXT, query_type TEXT, sources TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS obligations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    description TEXT, owner TEXT, deadline TEXT, clause_ref TEXT,
    category TEXT, priority TEXT, source_quote TEXT, status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obligation_id INTEGER REFERENCES obligations(id) ON DELETE CASCADE,
    document_id TEXT, title TEXT, assignee TEXT, due_date TEXT,
    status TEXT DEFAULT 'todo', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.executescript(SCHEMA)
        cols = [r[1] for r in c.execute("PRAGMA table_info(documents)")]
        if "status" not in cols:  # migrate databases created by the first version
            c.execute("ALTER TABLE documents ADD COLUMN status TEXT DEFAULT 'ready'")


def rows(query: str, params: tuple = ()) -> list[dict]:
    with db() as c:
        return [dict(r) for r in c.execute(query, params).fetchall()]


def one(query: str, params: tuple = ()) -> dict | None:
    r = rows(query, params)
    return r[0] if r else None


def execute(query: str, params: tuple = ()) -> int:
    with db() as c:
        return c.execute(query, params).lastrowid


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False)