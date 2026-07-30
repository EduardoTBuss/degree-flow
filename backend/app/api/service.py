"""Serialization + grade-switch translation shared by the routers."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..engine.progress import category_progress
from ..engine.terms import add_terms, term_key, term_lt, terms_until
from ..engine.types import APROVADA, FALTA, KIND_OBRIGATORIA
from ..engine.validate import validate
from ..persistence import repos
from ..persistence.models import CourseMapping, ElectiveAdoption, GradeVersion, Plan, PlanItem


# ----- engine inputs -----------------------------------------------------


def engine_inputs(session: Session, plan: Plan):
    grade = repos.get_grade(session, plan.grade_version_id)
    if grade is None:
        raise errors.ApiError(500, "INTERNAL_ERROR", "Grade do plano não encontrada.")
    curriculum = repos.curriculum_snapshot(session, grade)
    user_state = repos.user_state_snapshot(session, grade)
    plan_snap = repos.plan_snapshot(session, plan)
    requirements = repos.requirements_snapshot(session)
    sections = repos.sections_snapshot(session)
    return grade, curriculum, user_state, plan_snap, requirements, sections


# ----- serialization -----------------------------------------------------


def serialize_item(item: PlanItem) -> dict:
    return {
        "course_code": item.course_code,
        "term": item.term,
        "locked": bool(item.locked),
        "section_id": item.section_id,
    }


def build_timeline(plan: Plan, curriculum, user_state, horizon: str) -> dict:
    """F1: contiguous past columns from start_term (with gaps) + undated completed."""
    undated = sorted(
        c for c in curriculum.courses
        if user_state.status_of(c) == APROVADA and user_state.completed_term_of(c) is None
    )
    if not plan.start_term:
        return {"terms": [], "past_terms": [], "undated_completed": undated}

    n = terms_until(plan.start_term, horizon)
    terms = [add_terms(plan.start_term, i) for i in range(max(0, n) + 1)]

    completed_by_term: dict[str, list[str]] = {}
    for code in curriculum.courses:
        if user_state.status_of(code) != APROVADA:
            continue
        ct = user_state.completed_term_of(code)
        if ct is not None and term_lt(ct, plan.current_term):
            completed_by_term.setdefault(ct, []).append(code)

    past_terms = []
    for t in terms:
        if not term_lt(t, plan.current_term):
            break
        codes = sorted(completed_by_term.get(t, []))
        credits = sum(curriculum.courses[c].credits for c in codes if c in curriculum.courses)
        past_terms.append({"term": t, "course_codes": codes, "credits": credits})

    return {"terms": terms, "past_terms": past_terms, "undated_completed": undated}


def serialize_plan(session: Session, plan: Plan, extra_diagnostics: list[dict] | None = None) -> dict:
    _grade, curriculum, user_state, plan_snap, requirements, sections = engine_inputs(session, plan)
    vres = validate(curriculum, user_state, plan_snap, requirements, sections)

    unallocated = sorted(
        code
        for code, info in curriculum.courses.items()
        if info.kind == KIND_OBRIGATORIA
        and user_state.status_of(code) == FALTA
        and code not in plan_snap.items
    )
    diagnostics = list(extra_diagnostics or []) + [d.to_dict() for d in vres.diagnostics]
    progress = category_progress(curriculum, user_state, plan_snap, requirements)

    horizon = plan.current_term
    if plan.target_term and term_key(plan.target_term) > term_key(horizon):
        horizon = plan.target_term
    for i in plan_snap.items.values():
        if term_key(i.term) > term_key(horizon):
            horizon = i.term
    timeline = build_timeline(plan, curriculum, user_state, horizon)

    return {
        "id": plan.id,
        "name": plan.name,
        "grade_version_id": plan.grade_version_id,
        "current_term": plan.current_term,
        "target_term": plan.target_term,
        "start_term": plan.start_term,
        "max_credits_per_term": plan.max_credits_per_term,
        "max_difficulty_per_term": plan.max_difficulty_per_term,
        "items": [serialize_item(i) for i in repos.plan_items(session, plan.id)],
        "unallocated": unallocated,
        "term_summaries": [s.to_dict() for s in vres.term_summaries],
        "diagnostics": diagnostics,
        "blocked_codes": list(vres.blocked_codes),
        "critical_path": vres.critical_path.to_dict(),
        "requirements_progress": progress,
        "timeline": timeline,
    }


def serialize_plan_summary(plan: Plan) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "grade_version_id": plan.grade_version_id,
        "current_term": plan.current_term,
        "target_term": plan.target_term,
        "start_term": plan.start_term,
        "max_credits_per_term": plan.max_credits_per_term,
        "max_difficulty_per_term": plan.max_difficulty_per_term,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


# ----- grade switch translation -----------------------------------------


def _reform_of(from_grade: GradeVersion, to_grade: GradeVersion) -> str | None:
    return to_grade.reform_id or from_grade.reform_id


def translate_items(
    session: Session, plan: Plan, from_grade: GradeVersion, to_grade: GradeVersion
) -> list[dict]:
    target_codes = {
        e.course_code for e, _c in repos.curriculum_rows(session, to_grade.id)
    }
    reform_id = _reform_of(from_grade, to_grade)
    notes: list[dict] = []

    forward: dict[str, tuple[str, str]] = {}
    merged_origins: dict[str, list[str]] = {}
    if reform_id:
        for m in session.execute(
            select(CourseMapping).where(CourseMapping.reform_id == reform_id)
        ).scalars():
            forward[m.from_code] = (m.to_code, m.relation)
            if m.relation == "merged_into":
                merged_origins.setdefault(m.to_code, []).append(m.from_code)

    going_to_derived = bool(to_grade.reform_id) and not to_grade.is_base
    # ADR-028b: adoptions are per grade; migrate them across the switch.
    adopted_from = repos.adopted_elective_codes(session, from_grade.id)
    to_catalog = {
        cat.course_code: cat
        for cat, _c in repos.elective_catalog_rows(session, to_grade.id)
    }
    to_catalog_exists = bool(to_catalog)
    items = repos.plan_items(session, plan.id)
    new_alloc: dict[str, tuple[str, bool]] = {}

    def place(code: str, term: str, locked: bool) -> None:
        cur = new_alloc.get(code)
        if cur is None or term_key(term) < term_key(cur[0]):
            new_alloc[code] = (term, locked or (cur[1] if cur else False))

    for it in items:
        code = it.course_code
        if code in target_codes:
            place(code, it.term, bool(it.locked))
            continue
        if going_to_derived and code in forward:
            to_code, relation = forward[code]
            if to_code in target_codes:
                place(to_code, it.term, bool(it.locked))
                if relation == "merged_into":
                    notes.append(_note("merged_into", [code], to_code))
                continue
        if not going_to_derived and code in merged_origins:
            expanded = [o for o in merged_origins[code] if o in target_codes]
            for o in expanded:
                place(o, it.term, bool(it.locked))
            if expanded:
                notes.append(_note("split_from_merge", expanded, code))
                continue
        # ADR-028b: a materialized elective is user state (per grade). Migrate it
        # when the target grade's catalog also lists it as optativa; else drop it
        # with an ACTIONABLE reason (never the generic "dropped", which would lie
        # about an elective that exists in the portal, just not adopted here).
        if code in adopted_from:
            cat = to_catalog.get(code)
            if cat is not None and cat.kind == "optativa":
                if session.get(ElectiveAdoption, {
                    "grade_version_id": to_grade.id, "course_code": code
                }) is None:
                    session.add(ElectiveAdoption(
                        grade_version_id=to_grade.id, course_code=code,
                        adopted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        note=None,
                    ))
                place(code, it.term, bool(it.locked))
                notes.append(_elective_note("elective_migrated", code, None))
                continue
            reason = "not_in_catalog" if to_catalog_exists else "no_catalog"
            notes.append(_elective_note("elective_dropped", code, reason))
            continue
        notes.append(_note("dropped", [code], code))

    session.query(PlanItem).filter(PlanItem.plan_id == plan.id).delete()
    session.flush()
    for code, (term, locked) in new_alloc.items():
        session.add(PlanItem(plan_id=plan.id, course_code=code, term=term, locked=locked))
    session.flush()
    return notes


def _note(relation: str, from_codes: list[str], to_code: str) -> dict:
    labels = {
        "merged_into": "cadeiras fundidas na grade nova",
        "split_from_merge": "cadeira unificada reexpandida na grade base",
        "dropped": "cadeira inexistente na grade selecionada foi removida do plano",
    }
    return {
        "id": f"TRANSITION_NOTE:{relation}:{to_code}",
        "type": "TRANSITION_NOTE",
        "severity": "info",
        "course_codes": [to_code] if relation != "dropped" else from_codes,
        "related_codes": from_codes,
        "term": None,
        "message": labels.get(relation, "item traduzido entre grades"),
        "details": {"relation": relation, "from": from_codes, "to": to_code},
    }


def _elective_note(relation: str, code: str, reason: str | None) -> dict:
    """v5 F1b (ADR-028b): elective migrated/dropped across a grade switch —
    same TRANSITION_NOTE channel, never a new Diagnostic type."""
    labels = {
        "elective_migrated": "optativa materializada migrada para a grade nova",
        "elective_dropped": "optativa materializada não existe na grade selecionada",
    }
    return {
        "id": f"TRANSITION_NOTE:{relation}:{code}",
        "type": "TRANSITION_NOTE",
        "severity": "info",
        "course_codes": [code],
        "related_codes": [code],
        "term": None,
        "message": labels.get(relation, "optativa traduzida entre grades"),
        "details": {"relation": relation, "from": [code], "to": code, "reason": reason},
    }


# ===== C2: stateless what-if snapshot (ADR-024) ===========================

from dataclasses import replace as _dc_replace  # noqa: E402  (C2 block-local)

from ..engine.types import PlanItemSnap, PlanSnapshot  # noqa: E402


def whatif_plan_snapshot(
    base: PlanSnapshot,
    campaign_term: str,
    accepted_adds: dict[str, str | None],
    accepted_drops: list[str],
) -> PlanSnapshot:
    """Derived in-memory PlanSnapshot for the what-if simulator (ADR-024).

    Assumed-accepted requests are applied to a COPY of the plan snapshot —
    nothing is ever written to the database:
    - accepted drop  -> the item keeps its allocation (term/locked) but loses
      the chosen section (same effect as the real accept, ADR-017);
    - accepted add   -> item placed at the campaign term with the request's
      section (overriding a previous allocation of the course, exactly like
      the real accept via PUT /items);
    - denied         -> no effect at all.
    Drops are applied BEFORE adds (correction's two waves), so a course with
    both assumed accepted ends allocated with the add's section."""
    items = dict(base.items)
    for code in sorted(set(accepted_drops)):
        cur = items.get(code)
        if cur is not None:
            items[code] = PlanItemSnap(term=cur.term, locked=cur.locked, section_id=None)
    for code in sorted(accepted_adds):
        cur = items.get(code)
        items[code] = PlanItemSnap(
            term=campaign_term,
            locked=cur.locked if cur is not None else False,
            section_id=accepted_adds[code],
        )
    return _dc_replace(base, items=items)
