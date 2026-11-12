"""Minimal RFC 5545 iCalendar writer: one all-day event per dated task, with 7-day and 1-day alarms."""
from datetime import date, datetime, timedelta, timezone


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    data, parts, limit = line.encode("utf-8"), [], 74
    while len(data) > limit:
        cut = limit
        while (data[cut] & 0xC0) == 0x80:  # never split a multi-byte character
            cut -= 1
        parts.append(data[:cut].decode("utf-8"))
        data, limit = data[cut:], 73
    parts.append(data.decode("utf-8"))
    return "\r\n ".join(parts)


def build_ics(tasks: list[dict]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Obligo//Contract Obligations//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:Obligo deadlines"]
    for t in tasks:
        d = date.fromisoformat(t["due_date"])
        desc = f"Contract: {t.get('filename') or ''}\nOwner: {t.get('assignee') or 'Unassigned'}\nPriority: {t.get('priority') or ''}"
        lines += ["BEGIN:VEVENT", f"UID:obligo-task-{t['id']}@obligo.local", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{_esc(t['title'])}", f"DESCRIPTION:{_esc(desc)}"]
        for trigger in ("-P7D", "-P1D"):
            lines += ["BEGIN:VALARM", f"TRIGGER:{trigger}", "ACTION:DISPLAY", "DESCRIPTION:Contract deadline", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"