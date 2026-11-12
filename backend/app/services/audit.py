"""Append-only audit trail: who did what, to what, from where."""
from .. import database as dbx


def log(action: str, target: str = "", detail: str = "", request=None, actor: str = "user") -> None:
    ip = request.client.host if request is not None and request.client else ""
    try:
        dbx.execute("INSERT INTO audit_log (actor, action, target, detail, ip) VALUES (?,?,?,?,?)",
                    (actor, action, target[:200], detail[:300], ip))
    except Exception as e:  # auditing must never break the request
        print(f"[audit] {e}")