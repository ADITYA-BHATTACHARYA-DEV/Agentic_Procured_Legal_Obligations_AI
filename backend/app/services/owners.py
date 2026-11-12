"""Auto-assignment: map a party name from the contract ("Acme Ltd", "Vendor") to a real person."""
from difflib import SequenceMatcher

from .. import database as dbx


def find_owner(party: str | None) -> dict | None:
    if not party:
        return None
    p = party.lower().strip()
    for o in dbx.rows("SELECT * FROM owners"):
        key = (o["party"] or "").lower().strip()
        if len(key) >= 3 and (key == p or key in p or p in key or SequenceMatcher(None, key, p).ratio() >= 0.8):
            return o
    return None


def apply_mapping() -> int:
    """Re-route every task using the CURRENT owners table. Idempotent: matches on the contract's party name."""
    n = 0
    for t in dbx.rows("SELECT t.id, o.owner FROM tasks t JOIN obligations o ON o.id = t.obligation_id"):
        person = find_owner(t["owner"])
        dbx.execute("UPDATE tasks SET assignee=?, assignee_email=? WHERE id=?",
                    (person["name"] if person else t["owner"], person["email"] if person else None, t["id"]))
        n += 1 if person else 0
    return n