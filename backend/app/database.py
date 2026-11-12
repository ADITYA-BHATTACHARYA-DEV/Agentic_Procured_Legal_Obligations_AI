"""SQLite layer: documents, chats, messages, obligations, tasks, owners, tags, notifications, full-text pages."""
import json
import sqlite3
from contextlib import contextmanager

from .config import settings

HAS_FTS = False

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
CREATE TABLE IF NOT EXISTS owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, party TEXT
);
CREATE TABLE IF NOT EXISTS tags (
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE, tag TEXT,
    PRIMARY KEY (document_id, tag)
);
CREATE TABLE IF NOT EXISTS handwriting_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE, page INTEGER,
    printed TEXT, handwritten TEXT, translation_en TEXT,
    language TEXT, language_code TEXT, script TEXT, script_detected TEXT, script_match INTEGER,
    legibility TEXT, agreement REAL, flagged TEXT, status TEXT, analysis TEXT,
    actions_applied INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, page)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
    actor TEXT, action TEXT, target TEXT, detail TEXT, ip TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, days_before INTEGER,
    channel TEXT, recipient TEXT, message TEXT, status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (task_id, days_before)
);
"""

# Columns added after the first release. Applied to old AND new databases.
MIGRATIONS = {
    "documents": [("effective_date", "TEXT"), ("term_months", "INTEGER"), ("auto_renews", "INTEGER"),
                  ("notice_days", "INTEGER"), ("renewal_date", "TEXT"), ("governing_law", "TEXT"),
                  ("encrypted", "INTEGER DEFAULT 0"), ("wrapped_key", "TEXT")],
    "obligations": [("kind", "TEXT DEFAULT 'obligation'"), ("due_date", "TEXT"),
                    ("recurrence", "TEXT"), ("amount", "TEXT")],
    "tasks": [("assignee_email", "TEXT")],
}


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
    global HAS_FTS
    with db() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='pages_fts'").fetchone():
            try:
                c.execute("CREATE VIRTUAL TABLE pages_fts USING fts5(text, document_id UNINDEXED, page UNINDEXED)")
            except sqlite3.OperationalError:  # SQLite built without FTS5: fall back to LIKE search
                c.execute("CREATE TABLE pages_fts (text TEXT, document_id TEXT, page INTEGER)")
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name='pages_fts'").fetchone()[0] or ""
        HAS_FTS = "VIRTUAL" in sql.upper()


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