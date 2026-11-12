from datetime import date

from app.services import dates
from app.services.calendar_ics import build_ics
from app.services.scheduler import bucket_for

EFF = date(2026, 1, 1)


def test_iso_passthrough():
    assert dates.resolve_deadline("2026-03-15", EFF) == "2026-03-15"


def test_relative_days_after_effective_date():
    assert dates.resolve_deadline("30 days after the Effective Date", EFF) == "2026-01-31"


def test_relative_months_and_words():
    assert dates.resolve_deadline("within six months of the Effective Date", EFF) == "2026-07-01"


def test_before_term_end():
    assert dates.resolve_deadline("60 days before the end of the term", EFF, date(2027, 1, 1)) == "2026-11-02"


def test_business_days_skip_weekends():
    # 2026-01-01 is a Thursday: +3 business days = Tuesday the 6th
    assert dates.resolve_deadline("3 business days after the effective date", EFF) == "2026-01-06"


def test_unresolvable_anchor_returns_none():
    assert dates.resolve_deadline("within 10 days of termination", EFF) is None
    assert dates.resolve_deadline("30 days after the Effective Date", None) is None


def test_recurrence_detection_and_expansion():
    assert dates.normalize_recurrence("none", "Client shall pay fees quarterly") == "quarterly"
    out = dates.expand(date(2026, 1, 31), "monthly", date(2026, 4, 30))
    assert [d.isoformat() for d in out] == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]


def test_term_end():
    assert dates.term_end(EFF, 24) == date(2028, 1, 1)
    assert dates.term_end(None, 24) is None


def test_reminder_buckets():
    assert [bucket_for(d) for d in (45, 30, 20, 7, 3, 1, 0, -5, -90)] == [None, 30, 30, 7, 7, 1, 0, 0, None]


def test_ics_contains_event_and_alarms():
    ics = build_ics([{"id": 7, "title": "Pay, invoice; now", "due_date": "2026-03-15", "filename": "a.pdf",
                      "assignee": "Asha", "priority": "high"}])
    assert "BEGIN:VEVENT" in ics and "DTSTART;VALUE=DATE:20260315" in ics
    assert "Pay\\, invoice\\; now" in ics and ics.count("BEGIN:VALARM") == 2
    assert ics.endswith("\r\n")