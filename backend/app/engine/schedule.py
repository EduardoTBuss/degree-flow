"""Schedule conflict detection (F3b). Pure, deterministic. No I/O.

Two chosen sections conflict when they share a weekday and their minute
intervals overlap. SCHEDULE_CONFLICT is a WARNING (decision A6) — the app
informs; the user decides which course to keep.
"""
from __future__ import annotations

from itertools import combinations

from .types import Diagnostic, SectionSnap


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _overlaps(a: SectionSnap, b: SectionSnap) -> list[dict]:
    out: list[dict] = []
    for ta in a.timeslots:
        for tb in b.timeslots:
            if ta.weekday != tb.weekday:
                continue
            start = max(ta.start_min, tb.start_min)
            end = min(ta.end_min, tb.end_min)
            if start < end:
                out.append({"weekday": ta.weekday, "start": hhmm(start), "end": hhmm(end)})
    return out


def check_schedule(sections: list[SectionSnap]) -> list[Diagnostic]:
    """One SCHEDULE_CONFLICT (warning) per overlapping pair of chosen sections."""
    diags: list[Diagnostic] = []
    for a, b in combinations(sorted(sections, key=lambda s: s.id), 2):
        overlaps = _overlaps(a, b)
        if not overlaps:
            continue
        first = overlaps[0]
        diags.append(
            Diagnostic(
                id=f"SCHEDULE_CONFLICT:{a.id}:{b.id}",
                type="SCHEDULE_CONFLICT",
                severity="warning",
                course_codes=(a.course_code, b.course_code),
                related_codes=(),
                term=a.term,
                message=(
                    f"Conflito de horário entre {a.course_code} ({a.label or a.id}) e "
                    f"{b.course_code} ({b.label or b.id}): sobrepõem "
                    f"{_weekday_name(first['weekday'])} {first['start']}–{first['end']}."
                ),
                details={"section_a": a.id, "section_b": b.id, "overlaps": overlaps},
            )
        )
    return diags


_WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _weekday_name(w: int) -> str:
    return _WEEKDAYS[w] if 0 <= w < len(_WEEKDAYS) else f"dia {w}"
