"""Requirement-category progress (RN6). Pure; shared by validate() and the API."""
from __future__ import annotations

from .terms import term_le
from .types import (
    APROVADA,
    CURSANDO,
    FALTA,
    KIND_OPTATIVA,
    CurriculumSnapshot,
    PlanSnapshot,
    RequirementsSnapshot,
    UserState,
)


def category_progress(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot | None,
    requirements: RequirementsSnapshot,
) -> list[dict]:
    """Progress per category. When `counts_courses`, hours of `optativa` courses
    with status aprovada/cursando count; if a plan is given, allocated optativas
    (status falta) also count — filtered by target_term when set."""
    results = []
    for cat in requirements.categories:
        course_hours = 0.0
        if cat.counts_courses:
            for code, info in curriculum.courses.items():
                if info.kind != KIND_OPTATIVA:
                    continue
                st = user_state.status_of(code)
                if st in (APROVADA, CURSANDO):
                    course_hours += info.hours
                elif st == FALTA and plan is not None and code in plan.items:
                    item = plan.items[code]
                    if plan.target_term is None or term_le(item.term, plan.target_term):
                        course_hours += info.hours
        total = cat.logged_hours + course_hours
        remaining = max(0.0, round(cat.min_hours - total, 2))
        results.append(
            {
                "key": cat.key,
                "label": cat.label,
                "min_hours": cat.min_hours,
                "rule_note": cat.rule_note,
                "counts_courses": cat.counts_courses,
                "logged_hours": round(cat.logged_hours, 2),
                "course_hours": round(course_hours, 2),
                "total_hours": round(total, 2),
                "remaining_hours": remaining,
                "satisfied": remaining <= 0,
            }
        )
    return results
