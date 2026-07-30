"""Plans, allocations, locks, engine endpoints + v2 schedule/recommend."""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path as PathParam
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..domain.schemas import (
    AutoplanRequest,
    ItemPut,
    PlanCreate,
    PlanPatch,
    RecommendRequest,
    ScheduleCheckRequest,
    TermCommit,
)
from ..engine.autoplan import autoplan
from ..engine.pinned import recommend_with_pinned_courses
from ..engine.progress import category_progress
from ..engine.recommend import recommend_schedule
from ..engine.schedule import check_schedule
from ..engine.terms import term_lt
from ..engine.validate import validate
from ..persistence import repos
from ..persistence.models import (
    ElectiveAdoption,
    EnrollmentCampaign,
    EnrollmentRequest,
    GradeVersion,
    Plan,
    PlanItem,
    Section,
)
from . import service
from .deps import get_session

router = APIRouter(prefix="/api/v1", tags=["plans"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_plan(session: Session, plan_id: str) -> Plan:
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Plano '{plan_id}' não encontrado.")
    return plan


def _require_grade(session: Session, gv_id: str) -> GradeVersion:
    grade = repos.get_grade(session, gv_id)
    if grade is None:
        raise errors.ApiError(400, "BAD_REQUEST", f"Grade '{gv_id}' inexistente.")
    return grade


def _check_start_term(start: str | None, current: str) -> None:
    if start and term_lt(current, start):
        raise errors.ApiError(
            400, "BAD_REQUEST",
            f"start_term ({start}) não pode ser posterior ao termo atual ({current}).",
        )


def _check_current_term_items(
    session: Session, plan_id: str, new_current: str
) -> list[dict]:
    """ADR-010: items allocated before the new current_term (would break the
    invariant plan_item.term >= plan.current_term). Never auto-fixed."""
    items = session.execute(
        select(PlanItem).where(PlanItem.plan_id == plan_id)
    ).scalars().all()
    return sorted(
        (
            {"course_code": i.course_code, "term": i.term}
            for i in items
            if term_lt(i.term, new_current)
        ),
        key=lambda x: x["course_code"],
    )


# ----- CRUD -------------------------------------------------------------


@router.get("/plans")
def list_plans(session: Session = Depends(get_session)) -> dict:
    plans = session.execute(select(Plan).order_by(Plan.created_at)).scalars().all()
    return {"plans": [service.serialize_plan_summary(p) for p in plans]}


@router.post("/plans")
def create_plan(body: PlanCreate, session: Session = Depends(get_session)) -> JSONResponse:
    if body.grade_version_id:
        grade = _require_grade(session, body.grade_version_id)
    else:
        grade = repos.get_default_grade(session)
        if grade is None:
            raise errors.ApiError(500, "INTERNAL_ERROR", "Nenhuma grade default configurada.")
    _check_start_term(body.start_term, body.current_term)
    now = _now()
    plan = Plan(
        id=str(uuid.uuid4()),
        name=body.name,
        grade_version_id=grade.id,
        current_term=body.current_term,
        target_term=body.target_term,
        start_term=body.start_term,
        max_credits_per_term=body.max_credits_per_term,
        max_difficulty_per_term=body.max_difficulty_per_term,
        created_at=now,
        updated_at=now,
    )
    session.add(plan)
    session.commit()
    return JSONResponse(status_code=201, content=service.serialize_plan(session, plan))


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, session: Session = Depends(get_session)) -> dict:
    plan = _get_plan(session, plan_id)
    return service.serialize_plan(session, plan)


@router.patch("/plans/{plan_id}")
def patch_plan(
    plan_id: str, body: PlanPatch, session: Session = Depends(get_session)
) -> dict:
    plan = _get_plan(session, plan_id)
    fields = body.model_fields_set
    notes: list[dict] = []

    # ADR-010: validate EVERYTHING with effective post-patch values BEFORE any
    # write — the patch is all-or-nothing, never "fixed" silently.
    eff_current = (
        body.current_term
        if ("current_term" in fields and body.current_term is not None)
        else plan.current_term
    )
    eff_start = body.start_term if "start_term" in fields else plan.start_term
    if "current_term" in fields or "start_term" in fields:
        _check_start_term(eff_start, eff_current)
    if "current_term" in fields and body.current_term is not None and body.current_term != plan.current_term:
        offending = _check_current_term_items(session, plan.id, eff_current)
        if offending:
            raise errors.ApiError(
                400, "BAD_REQUEST",
                f"Não dá para definir 'Estou em' = {eff_current}: {len(offending)} "
                "cadeira(s) estão alocadas antes disso. Mova ou desaloque essas "
                "cadeiras primeiro.",
                details={"current_term": eff_current, "offending_items": offending},
            )

    if "grade_version_id" in fields and body.grade_version_id and body.grade_version_id != plan.grade_version_id:
        from_grade = repos.get_grade(session, plan.grade_version_id)
        to_grade = _require_grade(session, body.grade_version_id)
        notes = service.translate_items(session, plan, from_grade, to_grade)
        plan.grade_version_id = to_grade.id

    if "name" in fields and body.name is not None:
        plan.name = body.name
    if "current_term" in fields and body.current_term is not None:
        plan.current_term = body.current_term
    if "target_term" in fields:
        plan.target_term = body.target_term
    if "start_term" in fields:
        plan.start_term = body.start_term  # validated above (effective values)
    if "max_credits_per_term" in fields and body.max_credits_per_term is not None:
        plan.max_credits_per_term = body.max_credits_per_term
    if "max_difficulty_per_term" in fields:
        plan.max_difficulty_per_term = body.max_difficulty_per_term

    plan.updated_at = _now()
    session.commit()
    return service.serialize_plan(session, plan, extra_diagnostics=notes)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, session: Session = Depends(get_session)) -> JSONResponse:
    plan = _get_plan(session, plan_id)
    session.query(PlanItem).filter(PlanItem.plan_id == plan.id).delete()
    session.delete(plan)
    session.commit()
    return JSONResponse(status_code=204, content=None)


# ----- allocations & locks ----------------------------------------------


def _item_response(session: Session, plan: Plan, item: PlanItem) -> dict:
    _g, curriculum, user_state, plan_snap, requirements, sections = service.engine_inputs(session, plan)
    vres = validate(curriculum, user_state, plan_snap, requirements, sections)
    return {
        "item": service.serialize_item(item),
        "diagnostics": [d.to_dict() for d in vres.diagnostics],
        "blocked_codes": list(vres.blocked_codes),
        "term_summaries": [s.to_dict() for s in vres.term_summaries],
    }


@router.put("/plans/{plan_id}/items/{code}")
def put_item(
    plan_id: str, code: str, body: ItemPut, session: Session = Depends(get_session)
) -> dict:
    plan = _get_plan(session, plan_id)
    grade = repos.get_grade(session, plan.grade_version_id)
    # v5 F1b (ADR-028): EFFECTIVE grade so a materialized elective can be moved
    # (dragged) instead of 404-ing; message unchanged for real out-of-grade codes.
    curric_codes = {e.course_code for e, _c in repos.effective_curriculum_rows(session, grade)}
    if code not in curric_codes:
        raise errors.ApiError(404, "NOT_FOUND", f"Cadeira '{code}' não pertence à grade do plano.")
    if term_lt(body.term, plan.current_term):
        raise errors.ApiError(
            400, "BAD_REQUEST",
            f"Termo {body.term} é anterior ao termo atual do plano ({plan.current_term}).",
        )
    fields = body.model_fields_set
    if "section_id" in fields and body.section_id is not None:
        sec = session.get(Section, body.section_id)
        if sec is None or sec.course_code != code or sec.term != body.term:
            raise errors.ApiError(
                400, "BAD_REQUEST",
                "section_id inválido para esta cadeira/termo.",
            )

    item = session.get(PlanItem, {"plan_id": plan.id, "course_code": code})
    if item is None:
        item = PlanItem(
            plan_id=plan.id, course_code=code, term=body.term,
            locked=bool(body.locked), section_id=body.section_id,
        )
        session.add(item)
    else:
        item.term = body.term
        if body.locked is not None:
            item.locked = body.locked
        if "section_id" in fields:
            item.section_id = body.section_id
    plan.updated_at = _now()
    session.commit()
    return _item_response(session, plan, item)


def _autofill_rematricula(session: Session, plan: Plan, term: str, choices, now: str) -> dict:
    """item 5: on "selecionar semestre", pre-fill the enrollment campaign of the
    term with REMATRÍCULA requests for the chosen OWN-COURSE turmas.

    Business rule: rematrícula is own-course only — a
    foreign-course turma (`section.offered_to` set) is MATRÍCULA ESPECIAL and is
    NEVER auto-requested here (reported in `special_needed` so the user handles
    it via the especial round). Correção/especial stay manual. Runs inside the
    commit transaction (no commit here); idempotent (skips or updates a still
    "desejada" request). Round is the grade's FIRST round (data, not hard-coded:
    order 1 == rematrícula, scope proprio_curso)."""
    grade = repos.get_grade(session, plan.grade_version_id)
    rules = json.loads(grade.enrollment_rules) if grade and grade.enrollment_rules else {}
    rounds = sorted(
        (rules.get("rodadas") or []),
        key=lambda r: (r.get("order") or 0, r.get("key") or ""),
    )
    picks = [ch for ch in choices if ch.section_id]
    if not rounds or not picks:
        return {"campaign_id": None, "campaign_created": False, "round": None,
                "created": [], "updated": [], "skipped": [], "special_needed": []}
    round_key = rounds[0]["key"]

    camp = session.execute(
        select(EnrollmentCampaign).where(
            EnrollmentCampaign.plan_id == plan.id, EnrollmentCampaign.term == term
        )
    ).scalar_one_or_none()
    campaign_created = False
    if camp is None:
        camp = EnrollmentCampaign(
            id=str(uuid.uuid4()), plan_id=plan.id, term=term,
            status="aberta", note=None, created_at=now, updated_at=now,
        )
        session.add(camp)
        session.flush()
        campaign_created = True

    existing = {
        r.course_code: r
        for r in session.execute(
            select(EnrollmentRequest).where(
                EnrollmentRequest.campaign_id == camp.id,
                EnrollmentRequest.round == round_key,
                EnrollmentRequest.kind == "add",
            )
        ).scalars()
    }

    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    special_needed: list[str] = []
    for ch in picks:
        sec = session.get(Section, ch.section_id)
        if sec is None:
            continue
        if sec.offered_to:  # foreign course -> matrícula especial, not rematrícula
            special_needed.append(ch.course_code)
            continue
        prev = existing.get(ch.course_code)
        if prev is not None:
            if prev.status == "desejada" and prev.section_id != sec.id:
                prev.section_id = sec.id
                prev.section_label = sec.label
                prev.updated_at = now
                updated.append(ch.course_code)
            else:
                skipped.append(ch.course_code)
            continue
        req = EnrollmentRequest(
            campaign_id=camp.id, round=round_key, kind="add",
            course_code=ch.course_code, section_id=sec.id, section_label=sec.label,
            status="desejada", note="criado ao selecionar o semestre",
            created_at=now, updated_at=now,
        )
        session.add(req)
        # index the REQUEST (never a sentinel): a repeated course_code in the same
        # batch must hit the idempotent branch above, not crash on a bool.
        existing[ch.course_code] = req
        created.append(ch.course_code)

    camp.updated_at = now
    return {
        "campaign_id": camp.id,
        "campaign_created": campaign_created,
        "round": round_key,
        "created": sorted(created),
        "updated": sorted(updated),
        "skipped": sorted(skipped),
        "special_needed": sorted(special_needed),
    }


@router.post("/plans/{plan_id}/terms/{term}/commit")
def commit_term(
    plan_id: str,
    body: TermCommit,
    term: str = PathParam(pattern=r"^\d{4}[-/][12]$"),
    session: Session = Depends(get_session),
) -> dict:
    """v5 F1b (ADR-028): "selecionar semestre {termo}". Closes the semester:
    materializes chosen catalog electives (elective_adoption) and upserts the
    plan_item of every choice — the same code path as PUT /items, so all
    existing validation/diagnostics apply. plan_item stays the single source of
    truth (ADR-017). Schedule conflicts do NOT block: the plan is revalidated
    and returned with SCHEDULE_CONFLICT/TERM_OVERLOADED as diagnostics."""
    plan = _get_plan(session, plan_id)
    grade = repos.get_grade(session, plan.grade_version_id)
    t = term.replace("-", "/")
    if term_lt(t, plan.current_term):
        raise errors.ApiError(
            400, "BAD_REQUEST",
            f"Termo {t} é anterior ao termo atual do plano ({plan.current_term}).",
        )

    eff_codes = {c.code for _e, c in repos.effective_curriculum_rows(session, grade)}
    catalog = {
        cat.course_code: cat
        for cat, _c in repos.elective_catalog_rows(session, grade.id)
    }

    # a course_code repeated in the payload is ONE choice (last wins): without
    # this the same code would be adopted/allocated twice in the loops below.
    choices = list({ch.course_code: ch for ch in body.choices}.values())

    # structural validation up front (all-or-nothing)
    unknown: list[str] = []
    for ch in choices:
        if ch.course_code in eff_codes:
            continue
        cat = catalog.get(ch.course_code)
        if cat is not None and cat.kind == "optativa":
            continue
        unknown.append(ch.course_code)
    if unknown:
        raise errors.ApiError(
            400, "BAD_REQUEST",
            "Cadeiras fora da grade e do catálogo de optativas.",
            details={"unknown": sorted(set(unknown))},
        )
    for ch in choices:
        if ch.section_id is not None:
            sec = session.get(Section, ch.section_id)
            if sec is None or sec.course_code != ch.course_code or sec.term != t:
                # details let the client point at (and clear) the offending pick
                # instead of failing the whole commit with an opaque message.
                raise errors.ApiError(
                    400, "BAD_REQUEST",
                    f"section_id inválido para {ch.course_code}/{t}.",
                    details={
                        "course_code": ch.course_code,
                        "section_id": ch.section_id,
                        "reason": "not_found" if sec is None else "wrong_course_or_term",
                    },
                )

    adopted: list[str] = []
    allocated: list[str] = []
    unchanged: list[str] = []
    now = _now()
    for ch in choices:
        code = ch.course_code
        if code not in eff_codes:  # catalog optativa -> materialize
            session.add(ElectiveAdoption(
                grade_version_id=grade.id, course_code=code, adopted_at=now, note=None,
            ))
            adopted.append(code)
            eff_codes.add(code)
        item = session.get(PlanItem, {"plan_id": plan.id, "course_code": code})
        if item is None:
            session.add(PlanItem(
                plan_id=plan.id, course_code=code, term=t,
                locked=False, section_id=ch.section_id,
            ))
            allocated.append(code)
        elif item.term != t or item.section_id != ch.section_id:
            item.term = t
            item.section_id = ch.section_id
            allocated.append(code)
        else:
            unchanged.append(code)

    # item 5: mirror the chosen own-course turmas into the term's enrollment
    # campaign as rematrícula requests (foreign-course turmas -> especial only).
    campaign = _autofill_rematricula(session, plan, t, choices, now)

    plan.updated_at = now
    session.commit()
    return {
        "term": t,
        "committed": {
            "adopted": sorted(adopted),
            "allocated": sorted(allocated),
            "unchanged": sorted(unchanged),
        },
        "campaign": campaign,
        "plan": service.serialize_plan(session, plan),
    }


@router.delete("/plans/{plan_id}/items/{code}")
def delete_item(plan_id: str, code: str, session: Session = Depends(get_session)) -> JSONResponse:
    plan = _get_plan(session, plan_id)
    item = session.get(PlanItem, {"plan_id": plan.id, "course_code": code})
    if item is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Cadeira '{code}' não está alocada.")
    if item.locked:
        raise errors.ApiError(409, "CONFLICT", "Cadeira travada; destrave antes de desalocar.")
    session.delete(item)
    plan.updated_at = _now()
    session.commit()
    return JSONResponse(status_code=204, content=None)


def _set_lock(session: Session, plan_id: str, code: str, locked: bool) -> dict:
    plan = _get_plan(session, plan_id)
    item = session.get(PlanItem, {"plan_id": plan.id, "course_code": code})
    if item is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Cadeira '{code}' não está alocada.")
    item.locked = locked
    plan.updated_at = _now()
    session.commit()
    return _item_response(session, plan, item)


@router.post("/plans/{plan_id}/items/{code}/lock")
def lock_item(plan_id: str, code: str, session: Session = Depends(get_session)) -> dict:
    return _set_lock(session, plan_id, code, True)


@router.post("/plans/{plan_id}/items/{code}/unlock")
def unlock_item(plan_id: str, code: str, session: Session = Depends(get_session)) -> dict:
    return _set_lock(session, plan_id, code, False)


# ----- engine -----------------------------------------------------------


@router.post("/plans/{plan_id}/validate")
def validate_plan(plan_id: str, session: Session = Depends(get_session)) -> dict:
    plan = _get_plan(session, plan_id)
    _g, curriculum, user_state, plan_snap, requirements, sections = service.engine_inputs(session, plan)
    vres = validate(curriculum, user_state, plan_snap, requirements, sections)
    return {
        "valid": vres.valid,
        "diagnostics": [d.to_dict() for d in vres.diagnostics],
        "blocked_codes": list(vres.blocked_codes),
        "critical_path": vres.critical_path.to_dict(),
        "term_summaries": [s.to_dict() for s in vres.term_summaries],
    }


@router.post("/plans/{plan_id}/autoplan")
def autoplan_plan(
    plan_id: str, body: AutoplanRequest, session: Session = Depends(get_session)
) -> dict:
    plan = _get_plan(session, plan_id)
    target = body.target_term or plan.target_term
    if not target:
        raise errors.ApiError(
            400, "BAD_REQUEST",
            "Alvo de formatura ausente: informe target_term no corpo ou no plano.",
        )
    _g, curriculum, user_state, plan_snap, requirements, _sec = service.engine_inputs(session, plan)
    result = autoplan(
        curriculum, user_state, plan_snap, requirements, target, body.respect_existing
    )

    applied = False
    if body.mode == "apply":
        session.query(PlanItem).filter(PlanItem.plan_id == plan.id).delete()
        session.flush()
        for p in result.proposal:
            session.add(
                PlanItem(plan_id=plan.id, course_code=p.course_code, term=p.term, locked=p.locked)
            )
        plan.target_term = target
        plan.updated_at = _now()
        session.commit()
        applied = True

    return {
        "feasible": result.feasible,
        "proposal": [p.to_dict() for p in result.proposal],
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "critical_path": result.critical_path.to_dict(),
        "applied": applied,
    }


# ----- v2: schedule -----------------------------------------------------


@router.post("/plans/{plan_id}/schedule-check")
def schedule_check(
    plan_id: str,
    body: ScheduleCheckRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    plan = _get_plan(session, plan_id)
    _g, curriculum, _us, plan_snap, _req, sections = service.engine_inputs(session, plan)

    item_terms = {i.term for i in plan_snap.items.values()}
    terms_checked = sorted(item_terms & sections.terms_with_data)
    terms_without = sorted(item_terms - sections.terms_with_data)

    chosen_by_term: dict[str, list] = defaultdict(list)
    seen_ids: set[str] = set()
    items_without_section: list[str] = []
    diagnostics: list[dict] = []
    for code, item in plan_snap.items.items():
        if item.term not in sections.terms_with_data:
            continue
        if item.section_id and item.section_id in sections.sections:
            chosen_by_term[item.term].append(sections.sections[item.section_id])
            seen_ids.add(item.section_id)
        elif code in curriculum.courses:
            items_without_section.append(code)

    # item 4: pending elective picks (not yet plan_items) are conflict-checked
    # alongside the plan's sections, in their own term — same pure engine, so
    # the server (never the front) decides the conflict (principle 3).
    for sid in (body.extra_section_ids if body else []):
        snap = sections.sections.get(sid)
        if snap is None or snap.id in seen_ids or snap.term not in sections.terms_with_data:
            continue
        chosen_by_term[snap.term].append(snap)
        seen_ids.add(snap.id)

    for term in sorted(chosen_by_term):
        diagnostics.extend(d.to_dict() for d in check_schedule(chosen_by_term[term]))

    return {
        "terms_checked": terms_checked,
        "terms_without_data": terms_without,
        "items_without_section": sorted(items_without_section),
        "diagnostics": diagnostics,
    }


_MAX_PINNED = 6  # v5 F2a (spec 4.6): more than this is a 400, not a slow request


@router.post("/plans/{plan_id}/recommend-schedule")
def recommend(
    plan_id: str, body: RecommendRequest, session: Session = Depends(get_session)
) -> dict:
    plan = _get_plan(session, plan_id)
    _g, curriculum, user_state, plan_snap, requirements, sections = service.engine_inputs(session, plan)
    if body.term not in sections.terms_with_data:
        raise errors.ApiError(
            400, "BAD_REQUEST",
            f"O termo {body.term} não tem dados de turmas. Importe a oferta primeiro.",
        )
    of_term = [s for s in sections.sections.values() if s.term == body.term]
    # item 1: cap electives at what the optativa category still needs, counting
    # only DONE electives (plan=None). None when the grade has no such category.
    remaining_elective_hours = next(
        (
            p["remaining_hours"]
            for p in category_progress(curriculum, user_state, None, requirements)
            if p["counts_courses"]
        ),
        None,
    )
    limits = {
        "max_credits": body.max_credits,
        "max_difficulty": body.max_difficulty,
        "remaining_elective_hours": remaining_elective_hours,
    }
    if body.pinned_courses:
        # v5 F2a (ADR-029): pin by COURSE; validation against the plan's
        # curriculum snapshot (becomes the effective grade when F1b lands).
        pinned = sorted(set(body.pinned_courses))
        if len(pinned) > _MAX_PINNED:
            raise errors.ApiError(
                400,
                "BAD_REQUEST",
                f"No máximo {_MAX_PINNED} cadeiras podem ser fixadas por recomendação.",
                details={"max_pinned": _MAX_PINNED, "given": len(pinned)},
            )
        unknown = sorted(set(pinned) - set(curriculum.courses))
        if unknown:
            raise errors.ApiError(
                400,
                "BAD_REQUEST",
                "Cadeiras fixadas não pertencem à grade do plano.",
                details={"unknown": unknown},
            )
        return recommend_with_pinned_courses(
            curriculum,
            user_state,
            plan_snap,
            body.term,
            of_term,
            body.locked_choices,
            pinned,
            limits,
            body.top_n,
        )
    # no pins: the exact pre-v5 path, byte for byte (anti-regression, ADR-029)
    return recommend_schedule(
        curriculum,
        user_state,
        plan_snap,
        body.term,
        of_term,
        body.locked_choices,
        limits,
        body.top_n,
    )
