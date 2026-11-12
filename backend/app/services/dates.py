"""Deterministic date maths. The LLM extracts phrases; code (never the model) computes the dates."""
import re
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45,
         "sixty": 60, "ninety": 90}
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REL = re.compile(r"(\d+|[a-z-]+)\s*(?:\(\d+\)\s*)?(business\s+days?|working\s+days?|days?|weeks?|months?|years?)"
                 r"\s+(after|from|following|of|before|prior\s+to)\s+(?:the\s+)?(.+)", re.I)
EFFECTIVE = re.compile(r"effective date|commencement|start date|signing|execution|date of this agreement|agreement date", re.I)
TERM_END = re.compile(r"expir|end of (the )?(initial )?term|term end|end date|renewal date", re.I)

PERIODS = {"weekly": relativedelta(weeks=1), "monthly": relativedelta(months=1),
           "quarterly": relativedelta(months=3), "semi-annual": relativedelta(months=6),
           "annual": relativedelta(years=1)}
MAX_INSTANCES = {"weekly": 12, "monthly": 12, "quarterly": 8, "semi-annual": 4, "annual": 3}
RECURRENCE_HINTS = [
    ("semi-annual", r"semi-?annual|twice a year|every six months|every 6 months|biannual"),
    ("quarterly", r"quarterly|each quarter|every quarter|every three months|every 3 months"),
    ("monthly", r"monthly|each month|every month|per month"),
    ("weekly", r"weekly|each week|every week"),
    ("annual", r"annual|yearly|each year|every year|per year"),
]


def parse_iso(value) -> date | None:
    if not value or not ISO.match(str(value).strip()):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def add_business_days(d: date, n: int) -> date:
    step, left = (1 if n >= 0 else -1), abs(n)
    while left:
        d += timedelta(days=step)
        if d.weekday() < 5:
            left -= 1
    return d


def term_end(effective: date | None, months: int | None) -> date | None:
    return effective + relativedelta(months=months) if effective and months else None


def resolve_deadline(text: str | None, effective: date | None, end: date | None = None) -> str | None:
    """'2026-03-15' -> same; '30 days after the Effective Date' -> computed; unknown anchor -> None."""
    if not text:
        return None
    t = text.strip()
    if ISO.match(t):
        return t if parse_iso(t) else None
    m = REL.search(t)
    if not m:
        return None
    num, unit, direction, anchor_text = m.group(1).lower(), m.group(2).lower(), m.group(3).lower(), m.group(4)
    n = int(num) if num.isdigit() else WORDS.get(num)
    if n is None:
        return None
    anchor = effective if EFFECTIVE.search(anchor_text) else (end if TERM_END.search(anchor_text) else None)
    if anchor is None:
        return None
    sign = -1 if direction in ("before", "prior to") or direction.startswith("prior") else 1
    if "business" in unit or "working" in unit:
        return add_business_days(anchor, sign * n).isoformat()
    key = "days" if unit.startswith("day") else "weeks" if unit.startswith("week") else \
          "months" if unit.startswith("month") else "years"
    return (anchor + relativedelta(**{key: sign * n})).isoformat()


def normalize_recurrence(value: str | None, text: str = "") -> str | None:
    v = (value or "").strip().lower()
    if v in PERIODS:
        return v
    for name, pattern in RECURRENCE_HINTS:
        if re.search(pattern, f"{text}".lower()):
            return name
    return None


def expand(start: date, recurrence: str, end: date | None = None) -> list[date]:
    out, step = [], PERIODS[recurrence]
    for i in range(MAX_INSTANCES[recurrence]):
        d = start + step * i
        if end and d > end:
            break
        out.append(d)
    return out