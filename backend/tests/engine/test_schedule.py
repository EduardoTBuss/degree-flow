"""Schedule conflict detection (F3b) — SCHEDULE_CONFLICT is a warning (A6)."""
from __future__ import annotations

from app.engine.schedule import check_schedule
from app.engine.types import SectionSnap, TimeslotSnap


def _sec(sid, code, slots):
    return SectionSnap(
        id=sid, course_code=code, term="2026/2",
        timeslots=tuple(TimeslotSnap(w, s, e) for w, s, e in slots),
    )


def test_overlap_same_weekday_conflicts():
    a = _sec("A", "111", [(1, 480, 600)])   # ter 08:00-10:00
    b = _sec("B", "222", [(1, 540, 660)])   # ter 09:00-11:00
    diags = check_schedule([a, b])
    assert len(diags) == 1
    assert diags[0].type == "SCHEDULE_CONFLICT"
    assert diags[0].severity == "warning"  # A6


def test_no_overlap_different_weekday():
    a = _sec("A", "111", [(1, 480, 600)])
    b = _sec("B", "222", [(2, 480, 600)])
    assert check_schedule([a, b]) == []


def test_adjacent_not_overlapping():
    a = _sec("A", "111", [(1, 480, 600)])   # ends 10:00
    b = _sec("B", "222", [(1, 600, 720)])   # starts 10:00
    assert check_schedule([a, b]) == []


def test_deterministic_pair_id():
    a = _sec("Z", "111", [(1, 480, 600)])
    b = _sec("A", "222", [(1, 480, 600)])
    diags = check_schedule([a, b])
    # id orders the pair by section id -> A before Z
    assert diags[0].id == "SCHEDULE_CONFLICT:A:Z"
