"""Read helpers: effective course views (offer override + merge-status
translation via course_mapping, ADR-003) and engine snapshot builders."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.types import (
    APROVADA,
    CourseInfo,
    CurriculumSnapshot,
    PlanItemSnap,
    PlanSnapshot,
    RequirementCategorySnap,
    RequirementsSnapshot,
    SectionSnap,
    SectionsSnapshot,
    TimeslotSnap,
    UserState,
)
from .models import (
    Course,
    CourseMapping,
    CourseUserState,
    CurriculumEntry,
    ElectiveAdoption,
    ElectiveCatalog,
    GradeVersion,
    Plan,
    PlanItem,
    Prereq,
    RequirementCategory,
    RequirementEntry,
    Section,
    Timeslot,
    TransitionRule,
)


def get_default_grade(session: Session) -> GradeVersion | None:
    return session.execute(
        select(GradeVersion).where(GradeVersion.is_default.is_(True))
    ).scalar_one_or_none()


def get_grade(session: Session, gv_id: str) -> GradeVersion | None:
    return session.get(GradeVersion, gv_id)


def user_state_rows(session: Session) -> dict[str, CourseUserState]:
    rows = session.execute(select(CourseUserState)).scalars().all()
    return {r.course_code: r for r in rows}


def merge_groups(
    session: Session, grade: GradeVersion
) -> tuple[dict[str, list[str]], dict[str, str]]:
    if not grade.reform_id:
        return {}, {}
    groups: dict[str, list[str]] = {}
    for m in session.execute(
        select(CourseMapping).where(
            CourseMapping.reform_id == grade.reform_id,
            CourseMapping.relation == "merged_into",
        )
    ).scalars():
        groups.setdefault(m.to_code, []).append(m.from_code)

    policies: dict[str, str] = {}
    for rule in session.execute(
        select(TransitionRule).where(
            TransitionRule.reform_id == grade.reform_id,
            TransitionRule.type == "merge",
        )
    ).scalars():
        payload = json.loads(rule.payload)
        policies[payload["to"]["code"]] = payload.get("completion", "all_or_retake")
    return groups, policies


def derive_merged_status(
    origins: list[str], policy: str, states: dict[str, CourseUserState]
) -> str:
    origin_statuses = [
        states[o].status if o in states else "falta" for o in origins
    ]
    if policy == "any_counts":
        return APROVADA if any(s == APROVADA for s in origin_statuses) else "falta"
    return APROVADA if all(s == APROVADA for s in origin_statuses) else "falta"


def effective_status(
    code: str,
    states: dict[str, CourseUserState],
    groups: dict[str, list[str]],
    policies: dict[str, str],
) -> str:
    row = states.get(code)
    if row is not None:
        return row.status
    if code in groups:
        return derive_merged_status(
            groups[code], policies.get(code, "all_or_retake"), states
        )
    return "falta"


def curriculum_rows(session: Session, gv_id: str) -> list[tuple[CurriculumEntry, Course]]:
    return list(
        session.execute(
            select(CurriculumEntry, Course)
            .join(Course, Course.code == CurriculumEntry.course_code)
            .where(CurriculumEntry.grade_version_id == gv_id)
            .order_by(CurriculumEntry.course_code)
        ).all()
    )


def elective_catalog_rows(
    session: Session, gv_id: str
) -> list[tuple[ElectiveCatalog, Course]]:
    """Portal-derived curriculum-matrix catalog of a grade (v5 F1a, ADR-027),
    joined to the global Course (the name lives there). Ordered by course_code
    for determinism. Empty when the grade was never scraped."""
    return list(
        session.execute(
            select(ElectiveCatalog, Course)
            .join(Course, Course.code == ElectiveCatalog.course_code)
            .where(ElectiveCatalog.grade_version_id == gv_id)
            .order_by(ElectiveCatalog.course_code)
        ).all()
    )


def adopted_elective_codes(session: Session, gv_id: str) -> set[str]:
    """Codes the user materialized into this grade's flowchart (v5 F1b)."""
    return set(
        session.execute(
            select(ElectiveAdoption.course_code).where(
                ElectiveAdoption.grade_version_id == gv_id
            )
        ).scalars()
    )


def effective_curriculum_rows(
    session: Session, grade: GradeVersion
) -> list[tuple[CurriculumEntry, Course]]:
    """Canonical grade rows UNION the user's adopted electives (v5 F1b, ADR-028).

    An adopted elective has no curriculum_entry (it is user state, not seed) —
    a TRANSIENT CurriculumEntry is synthesized (kind='optativa',
    suggested_term_index=None) so every consumer treats it as a normal course.
    This is the "effective grade": what the user can OPERATE on (flowchart,
    engine, allocation). The canonical `curriculum_rows` stays untouched for
    the places whose semantics is literally "what the seed declares" (D3b).
    Deterministic: canonical order, then adopted sorted by code."""
    rows = curriculum_rows(session, grade.id)
    present = {c.code for _e, c in rows}
    adopted = adopted_elective_codes(session, grade.id) - present
    if not adopted:
        return rows
    catalog = {
        cat.course_code: (cat, course)
        for cat, course in elective_catalog_rows(session, grade.id)
    }
    extra: list[tuple[CurriculumEntry, Course]] = []
    for code in sorted(adopted):
        hit = catalog.get(code)
        course = hit[1] if hit else session.get(Course, code)
        if course is None:
            continue  # catalog gone (re-scrape) and no Course — skip, never crash
        entry = CurriculumEntry(
            grade_version_id=grade.id,
            course_code=code,
            kind="optativa",
            suggested_term_index=None,
        )
        extra.append((entry, course))
    return rows + extra


def effective_prereqs_by_course(
    session: Session, grade: GradeVersion
) -> dict[str, list[str]]:
    """Prereq map for the effective grade: the Prereq table UNION each adopted
    elective's catalog prereqs, filtered to codes present in the effective
    grade (a prereq the user doesn't have in the grade is ignored — the engine
    already skips unknown prereqs). v5 F1b."""
    base = prereqs_by_course(session, grade.id)
    adopted = adopted_elective_codes(session, grade.id)
    if not adopted:
        return base
    present = {c.code for _e, c in curriculum_rows(session, grade.id)} | adopted
    for cat, _course in elective_catalog_rows(session, grade.id):
        if cat.course_code in adopted and cat.course_code not in base:
            pres = [p for p in json.loads(cat.prereqs or "[]") if p in present]
            if pres:
                base[cat.course_code] = pres
    return base


def prereqs_by_course(session: Session, gv_id: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for p in session.execute(
        select(Prereq).where(Prereq.grade_version_id == gv_id).order_by(
            Prereq.course_code, Prereq.prereq_code
        )
    ).scalars():
        result.setdefault(p.course_code, []).append(p.prereq_code)
    return result


def course_view(
    entry: CurriculumEntry,
    course: Course,
    prereqs: list[str],
    states: dict[str, CourseUserState],
    groups: dict[str, list[str]],
    policies: dict[str, str],
) -> dict:
    """Merged course object — the API shape of a `courses[]` item (section 6.1)."""
    row = states.get(course.code)
    offer = (row.offer_override if row and row.offer_override else course.default_offer)
    return {
        "code": course.code,
        "name": course.name,
        "short": course.short,
        "credits": course.credits,
        "hours": course.hours,
        "kind": entry.kind,
        "suggested_term_index": entry.suggested_term_index,
        "prereqs": prereqs,
        "offer": offer,
        "offer_source": "user" if (row and row.offer_override) else "seed",
        "status": effective_status(course.code, states, groups, policies),
        "difficulty": row.difficulty if row else 3,
        "note": row.note if row else None,
        "completed_term": row.completed_term if row else None,  # v2 (F1)
    }


def curriculum_views(session: Session, grade: GradeVersion) -> list[dict]:
    states = user_state_rows(session)
    groups, policies = merge_groups(session, grade)
    prereq_map = effective_prereqs_by_course(session, grade)
    return [
        course_view(entry, course, prereq_map.get(course.code, []), states, groups, policies)
        for entry, course in effective_curriculum_rows(session, grade)
    ]


# ===== engine snapshot builders =====


def curriculum_snapshot(session: Session, grade: GradeVersion) -> CurriculumSnapshot:
    states = user_state_rows(session)
    prereq_map = effective_prereqs_by_course(session, grade)
    courses: dict[str, CourseInfo] = {}
    for entry, course in effective_curriculum_rows(session, grade):
        row = states.get(course.code)
        offer = row.offer_override if row and row.offer_override else course.default_offer
        courses[course.code] = CourseInfo(
            code=course.code,
            name=course.name,
            credits=course.credits,
            hours=course.hours,
            kind=entry.kind,
            offer=offer,
            prereqs=tuple(prereq_map.get(course.code, ())),
        )
    return CurriculumSnapshot(grade_version_id=grade.id, courses=courses)


def user_state_snapshot(session: Session, grade: GradeVersion) -> UserState:
    states = user_state_rows(session)
    groups, policies = merge_groups(session, grade)
    status: dict[str, str] = {}
    difficulty: dict[str, int] = {}
    completed: dict[str, str] = {}
    for entry, course in effective_curriculum_rows(session, grade):
        code = course.code
        status[code] = effective_status(code, states, groups, policies)
        row = states.get(code)
        difficulty[code] = row.difficulty if row else 3
        if row and row.completed_term and status[code] == APROVADA:
            completed[code] = row.completed_term
    return UserState(status=status, difficulty=difficulty, completed_term=completed)


def plan_items(session: Session, plan_id: str) -> list[PlanItem]:
    return list(
        session.execute(
            select(PlanItem).where(PlanItem.plan_id == plan_id).order_by(
                PlanItem.term, PlanItem.course_code
            )
        ).scalars()
    )


def plan_snapshot(session: Session, plan: Plan) -> PlanSnapshot:
    items = {
        i.course_code: PlanItemSnap(term=i.term, locked=bool(i.locked), section_id=i.section_id)
        for i in plan_items(session, plan.id)
    }
    return PlanSnapshot(
        current_term=plan.current_term,
        target_term=plan.target_term,
        items=items,
        max_credits_per_term=plan.max_credits_per_term,
        max_difficulty_per_term=plan.max_difficulty_per_term,
        start_term=plan.start_term,
    )


def sections_snapshot(session: Session) -> SectionsSnapshot:
    slots_by_section: dict[str, list[TimeslotSnap]] = {}
    for t in session.execute(select(Timeslot)).scalars():
        slots_by_section.setdefault(t.section_id, []).append(
            TimeslotSnap(weekday=t.weekday, start_min=t.start_min, end_min=t.end_min)
        )
    sections: dict[str, SectionSnap] = {}
    terms: set[str] = set()
    for s in session.execute(select(Section)).scalars():
        terms.add(s.term)
        sections[s.id] = SectionSnap(
            id=s.id,
            course_code=s.course_code,
            term=s.term,
            timeslots=tuple(sorted(slots_by_section.get(s.id, []), key=lambda x: (x.weekday, x.start_min))),
            label=s.label,
            professor=s.professor,
            capacity=s.capacity,
            enrolled=s.enrolled,
        )
    return SectionsSnapshot(sections=sections, terms_with_data=frozenset(terms))


def sections_of_term(session: Session, term: str) -> list[SectionSnap]:
    snap = sections_snapshot(session)
    return [s for s in snap.sections.values() if s.term == term]


def requirements_snapshot(session: Session) -> RequirementsSnapshot:
    cats = []
    categories = session.execute(
        select(RequirementCategory).order_by(RequirementCategory.key)
    ).scalars().all()
    for cat in categories:
        logged = sum(
            e.hours
            for e in session.execute(
                select(RequirementEntry).where(
                    RequirementEntry.category_key == cat.key
                )
            ).scalars()
        )
        cats.append(
            RequirementCategorySnap(
                key=cat.key,
                label=cat.label,
                min_hours=cat.min_hours,
                counts_courses=bool(cat.counts_courses),
                logged_hours=logged,
                rule_note=cat.rule_note,
            )
        )
    return RequirementsSnapshot(categories=tuple(cats))
