"""Tiny builders for engine snapshots — keep the tests declarative."""
from __future__ import annotations

from app.engine.types import (
    CourseInfo,
    CurriculumSnapshot,
    PlanItemSnap,
    PlanSnapshot,
    RequirementCategorySnap,
    RequirementsSnapshot,
    UserState,
)


def course(code, prereqs=(), *, credits=4, hours=60, kind="obrigatoria", offer="both", name=None):
    return CourseInfo(
        code=code,
        name=name or code,
        credits=credits,
        hours=hours,
        kind=kind,
        offer=offer,
        prereqs=tuple(prereqs),
    )


def curriculum(*courses) -> CurriculumSnapshot:
    return CurriculumSnapshot("gv-test", {c.code: c for c in courses})


def state(status=None, difficulty=None) -> UserState:
    return UserState(status=dict(status or {}), difficulty=dict(difficulty or {}))


def plan(items=None, *, current="2026/2", target=None, max_credits=28, max_diff=None) -> PlanSnapshot:
    snap_items = {
        code: (v if isinstance(v, PlanItemSnap) else PlanItemSnap(term=v))
        for code, v in (items or {}).items()
    }
    return PlanSnapshot(
        current_term=current,
        target_term=target,
        items=snap_items,
        max_credits_per_term=max_credits,
        max_difficulty_per_term=max_diff,
    )


def reqs(*cats) -> RequirementsSnapshot:
    return RequirementsSnapshot(tuple(cats))


def category(key, min_hours, logged=0.0, counts_courses=False):
    return RequirementCategorySnap(
        key=key, label=key, min_hours=min_hours, counts_courses=counts_courses, logged_hours=logged
    )


def types_of(diagnostics):
    return sorted(d.type for d in diagnostics)
