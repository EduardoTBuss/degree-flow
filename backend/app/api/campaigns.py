"""Enrollment campaigns (v4 C1, ADR-017/018/019): CRUD, request lifecycle,
accept->plan_item sync and the criticality queue.

Thin router by design: it builds snapshots (`service.engine_inputs`), calls
the PURE `engine.campaign.criticality_queue`, and serializes. Domain rules:

- `plan_item` is the ONLY source of truth of allocation. A request is a log of
  the process; the sync happens exactly once, on accept, through the SAME code
  path as `PUT /items` (`plans.put_item` is called directly — never duplicated),
  so every existing validation/diagnostic applies (PREREQ_VIOLATION,
  TERM_OVERLOADED counting the special-round credits — D1/D8).
- Round rules are DATA (`grade_version.enrollment_rules`, ADR-018): no round
  name or limit is hard-coded here; everything is validated against the grade.
- `enrollment_request.section_id` has no FK: reads derive `section_alive` and
  `in_plan` so the UI can SHOW desync instead of hiding it (risk #1/#2 of the
  spec, section 11).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..domain.schemas import (
    CampaignCreate,
    CampaignPatch,
    EnrollmentRequestCreate,
    EnrollmentRequestPatch,
    ItemPut,
)
from ..engine.campaign import criticality_queue
from ..engine.terms import is_valid_term, next_term, term_lt
from ..persistence import repos
from ..persistence.models import (
    Course,
    EnrollmentCampaign,
    EnrollmentRequest,
    Plan,
    PlanItem,
    Section,
)
from . import service
from .deps import get_session
from .plans import _get_plan, put_item

router = APIRouter(prefix="/api/v1", tags=["campaigns"])

STATUS_ACEITA = "aceita"
STATUS_NEGADA = "negada"
TERMINAL_STATUSES = (STATUS_ACEITA, STATUS_NEGADA)
# transition graph of spec section 5.2 (terminal states have no exits)
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "desejada": ("pedida", "aceita", "negada"),
    "pedida": ("aceita", "negada"),
    "aceita": (),
    "negada": (),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_term(t: str) -> str:
    return t.replace("-", "/")


# ----- lookups ------------------------------------------------------------


def _get_campaign(session: Session, plan: Plan, campaign_id: str) -> EnrollmentCampaign:
    camp = session.get(EnrollmentCampaign, campaign_id)
    if camp is None or camp.plan_id != plan.id:
        raise errors.ApiError(
            404, "NOT_FOUND", f"Campanha '{campaign_id}' não encontrada neste plano."
        )
    return camp


def _get_request(
    session: Session, camp: EnrollmentCampaign, request_id: int
) -> EnrollmentRequest:
    req = session.get(EnrollmentRequest, request_id)
    if req is None or req.campaign_id != camp.id:
        raise errors.ApiError(
            404, "NOT_FOUND", f"Pedido '{request_id}' não encontrado nesta campanha."
        )
    return req


def _rounds(session: Session, plan: Plan) -> list[dict]:
    """Round rules of the plan's grade (ADR-018) — data, never code.

    `sync_grade_meta` fills enrollment_rules on EVERY startup, so NULL here
    means a broken deployment, not a missing feature -> 500 with a hint."""
    grade = repos.get_grade(session, plan.grade_version_id)
    if grade is None:
        raise errors.ApiError(500, "INTERNAL_ERROR", "Grade do plano não encontrada.")
    if not grade.enrollment_rules:
        raise errors.ApiError(
            500, "INTERNAL_ERROR",
            "Regras de matrícula ausentes na grade (reinicie o servidor para "
            "sincronizar o seed — sync_grade_meta).",
        )
    rules = json.loads(grade.enrollment_rules)
    rounds = list(rules.get("rodadas") or [])
    rounds.sort(key=lambda r: (r.get("order") or 0, r.get("key") or ""))
    return rounds


def _round_or_400(rounds: list[dict], key: str) -> dict:
    for r in rounds:
        if r.get("key") == key:
            return r
    raise errors.ApiError(
        400, "BAD_REQUEST",
        f"Rodada '{key}' não existe nas regras da grade.",
        details={"round": key, "known_rounds": [r.get("key") for r in rounds]},
    )


def _campaign_requests(session: Session, camp: EnrollmentCampaign) -> list[EnrollmentRequest]:
    return list(
        session.execute(
            select(EnrollmentRequest)
            .where(EnrollmentRequest.campaign_id == camp.id)
            .order_by(EnrollmentRequest.id)
        ).scalars()
    )


# ----- serialization -------------------------------------------------------


def _serialize_request(session: Session, plan: Plan, req: EnrollmentRequest) -> dict:
    course = session.get(Course, req.course_code)
    # derived reads (ADR-017): the UI shows desync instead of guessing
    section_alive: bool | None = None
    if req.section_id:
        section_alive = session.get(Section, req.section_id) is not None
    item = session.get(PlanItem, {"plan_id": plan.id, "course_code": req.course_code})
    in_plan = bool(req.section_id and item is not None and item.section_id == req.section_id)
    return {
        "id": req.id,
        "round": req.round,
        "kind": req.kind,
        "course_code": req.course_code,
        "course_name": course.name if course else None,
        "section_id": req.section_id,
        "section_label": req.section_label,
        "status": req.status,
        "note": req.note,
        "section_alive": section_alive,
        "in_plan": in_plan,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
    }


def _serialize_campaign(session: Session, plan: Plan, camp: EnrollmentCampaign) -> dict:
    requests = _campaign_requests(session, camp)
    accepted = sorted(
        {
            r.section_id
            for r in requests
            if r.kind == "add" and r.status == STATUS_ACEITA and r.section_id
        }
    )
    return {
        "id": camp.id,
        "plan_id": camp.plan_id,
        "term": camp.term,
        "status": camp.status,
        "note": camp.note,
        "rounds": _rounds(session, plan),
        "requests": [_serialize_request(session, plan, r) for r in requests],
        "accepted_section_ids": accepted,  # convenience: locked_choices (5.3)
        "created_at": camp.created_at,
        "updated_at": camp.updated_at,
    }


def _campaign_summary(session: Session, camp: EnrollmentCampaign) -> dict:
    requests = session.execute(
        select(EnrollmentRequest.round, EnrollmentRequest.status).where(
            EnrollmentRequest.campaign_id == camp.id
        )
    ).all()
    counts: dict[str, dict[str, int]] = {}
    for rnd, status in requests:
        by_status = counts.setdefault(rnd, {})
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "id": camp.id,
        "term": camp.term,
        "status": camp.status,
        "note": camp.note,
        "requests_total": len(requests),
        "counts": counts,  # round -> status -> n
        "created_at": camp.created_at,
        "updated_at": camp.updated_at,
    }


# ----- campaign CRUD (spec 6.3) --------------------------------------------


@router.post("/plans/{plan_id}/campaigns")
def create_campaign(
    plan_id: str,
    body: CampaignCreate | None = None,
    session: Session = Depends(get_session),
) -> JSONResponse:
    plan = _get_plan(session, plan_id)
    raw_term = body.term if body and body.term else None
    term = _norm_term(raw_term) if raw_term else next_term(plan.current_term)
    if not is_valid_term(term):
        raise errors.ApiError(
            400, "BAD_REQUEST", f"Termo inválido: '{raw_term}'. Formato: AAAA/S."
        )
    if term_lt(term, plan.current_term):
        raise errors.ApiError(
            400, "BAD_REQUEST",
            f"Termo {term} é anterior ao termo atual do plano ({plan.current_term}).",
        )
    existing = session.execute(
        select(EnrollmentCampaign).where(
            EnrollmentCampaign.plan_id == plan.id, EnrollmentCampaign.term == term
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise errors.ApiError(
            409, "CAMPAIGN_EXISTS",
            f"Já existe campanha para o plano neste termo ({term}).",
            details={"campaign_id": existing.id, "term": term},
        )
    now = _now()
    camp = EnrollmentCampaign(
        id=str(uuid.uuid4()), plan_id=plan.id, term=term,
        status="aberta", note=None, created_at=now, updated_at=now,
    )
    session.add(camp)
    session.commit()
    return JSONResponse(status_code=201, content=_serialize_campaign(session, plan, camp))


@router.get("/plans/{plan_id}/campaigns")
def list_campaigns(plan_id: str, session: Session = Depends(get_session)) -> dict:
    plan = _get_plan(session, plan_id)
    camps = session.execute(
        select(EnrollmentCampaign)
        .where(EnrollmentCampaign.plan_id == plan.id)
        .order_by(EnrollmentCampaign.term, EnrollmentCampaign.created_at)
    ).scalars().all()
    return {"campaigns": [_campaign_summary(session, c) for c in camps]}


@router.get("/plans/{plan_id}/campaigns/{campaign_id}")
def get_campaign(
    plan_id: str, campaign_id: str, session: Session = Depends(get_session)
) -> dict:
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    return _serialize_campaign(session, plan, camp)


@router.patch("/plans/{plan_id}/campaigns/{campaign_id}")
def patch_campaign(
    plan_id: str,
    campaign_id: str,
    body: CampaignPatch,
    session: Session = Depends(get_session),
) -> dict:
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    fields = body.model_fields_set
    if "status" in fields and body.status is not None:
        camp.status = body.status  # closing has NO side effect (ADR-017)
    if "note" in fields:
        camp.note = body.note  # explicit null clears
    camp.updated_at = _now()
    session.commit()
    return _serialize_campaign(session, plan, camp)


@router.delete("/plans/{plan_id}/campaigns/{campaign_id}")
def delete_campaign(
    plan_id: str,
    campaign_id: str,
    force: bool = False,
    session: Session = Depends(get_session),
) -> JSONResponse:
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    if not force:
        accepted_ids = [
            r.id for r in _campaign_requests(session, camp) if r.status == STATUS_ACEITA
        ]
        if accepted_ids:
            raise errors.ApiError(
                409, "CONFLICT",
                "Campanha tem pedidos aceitos (histórico com efeito no plano) — "
                "use ?force=true para apagar mesmo assim (o plano não é alterado).",
                details={"accepted_request_ids": accepted_ids},
            )
    # force=true drops the whole campaign log, accepted requests included.
    # In BOTH paths plan_item is NEVER touched (ADR-017): the allocation an
    # accept created stays in the plan — the campaign is a discardable log.
    session.delete(camp)  # requests cascade at the DB (FK ON DELETE CASCADE)
    session.commit()
    return JSONResponse(status_code=204, content=None)


# ----- requests (spec 6.4) --------------------------------------------------


def _validate_section(
    session: Session, camp: EnrollmentCampaign, course_code: str, section_id: str
) -> Section:
    sec = session.get(Section, section_id)
    if sec is None or sec.course_code != course_code or sec.term != camp.term:
        raise errors.ApiError(
            400, "BAD_REQUEST",
            f"section_id '{section_id}' inexistente ou de outra cadeira/termo "
            f"(esperado: cadeira {course_code}, termo {camp.term}).",
        )
    return sec


@router.post("/plans/{plan_id}/campaigns/{campaign_id}/requests")
def create_request(
    plan_id: str,
    campaign_id: str,
    body: EnrollmentRequestCreate,
    session: Session = Depends(get_session),
) -> JSONResponse:
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    rounds = _rounds(session, plan)
    rnd = _round_or_400(rounds, body.round)

    if body.kind == "drop" and not rnd.get("allows_drops"):
        raise errors.ApiError(
            400, "BAD_REQUEST",
            f"A rodada '{rnd.get('key')}' não aceita remoções (allows_drops=false).",
        )

    grade = repos.get_grade(session, plan.grade_version_id)
    # v5 F1b (ADR-028): effective grade — a materialized elective is a legit
    # request (e.g. matrícula especial of an adopted optativa).
    curric_codes = {e.course_code for e, _c in repos.effective_curriculum_rows(session, grade)}
    if body.course_code not in curric_codes:
        raise errors.ApiError(
            404, "NOT_FOUND",
            f"Cadeira '{body.course_code}' não pertence à grade do plano.",
        )

    section: Section | None = None
    if body.section_id:
        section = _validate_section(session, camp, body.course_code, body.section_id)

    dup = session.execute(
        select(EnrollmentRequest).where(
            EnrollmentRequest.campaign_id == camp.id,
            EnrollmentRequest.round == body.round,
            EnrollmentRequest.kind == body.kind,
            EnrollmentRequest.course_code == body.course_code,
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise errors.ApiError(
            409, "CONFLICT",
            "Pedido duplicado (mesma campanha, rodada, tipo e cadeira). "
            "Re-pedir turma negada = nova rodada (D7).",
            details={"request_id": dup.id},
        )

    # hard round limit (D1): read from the grade rules, never hard-coded
    max_adds = rnd.get("max_adds")
    if body.kind == "add" and max_adds is not None:
        current = len(
            session.execute(
                select(EnrollmentRequest.id).where(
                    EnrollmentRequest.campaign_id == camp.id,
                    EnrollmentRequest.round == body.round,
                    EnrollmentRequest.kind == "add",
                )
            ).all()
        )
        if current >= int(max_adds):
            raise errors.ApiError(
                400, "ROUND_LIMIT_EXCEEDED",
                f"A rodada '{rnd.get('key')}' permite no máximo {max_adds} "
                f"adições e já tem {current}.",
                details={"round": body.round, "max_adds": max_adds, "current": current},
            )

    # scope is a WARNING, never a block (spec 5.1 — source data is fuzzy)
    warnings: list[str] = []
    if section is not None and rnd.get("scope") == "proprio_curso" and section.offered_to:
        warnings.append(
            "turma ofertada para outro curso — rodada "
            f"'{rnd.get('key')}' aceita só cadeiras do próprio curso"
        )

    now = _now()
    req = EnrollmentRequest(
        campaign_id=camp.id,
        round=body.round,
        kind=body.kind,
        course_code=body.course_code,
        section_id=body.section_id,
        section_label=section.label if section is not None else None,
        status="desejada",
        note=None,
        created_at=now,
        updated_at=now,
    )
    session.add(req)
    camp.updated_at = now
    session.commit()
    return JSONResponse(
        status_code=201,
        content={"request": _serialize_request(session, plan, req), "warnings": warnings},
    )


@router.patch("/plans/{plan_id}/campaigns/{campaign_id}/requests/{request_id}")
def patch_request(
    plan_id: str,
    campaign_id: str,
    request_id: int,
    body: EnrollmentRequestPatch,
    session: Session = Depends(get_session),
) -> dict:
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    req = _get_request(session, camp, request_id)
    fields = body.model_fields_set

    # -- status transition (graph of section 5.2; terminal has no exits) -----
    new_status: str | None = None
    if "status" in fields and body.status is not None:
        allowed = ALLOWED_TRANSITIONS[req.status]
        if body.status not in allowed:
            raise errors.ApiError(
                409, "INVALID_TRANSITION",
                f"Transição de status inválida: {req.status} → {body.status}.",
                details={"from": req.status, "to": body.status, "allowed": list(allowed)},
            )
        new_status = body.status

    # -- section_id: editable while non-terminal only (accepted = immutable) --
    if "section_id" in fields and body.section_id != req.section_id:
        if req.status in TERMINAL_STATUSES:
            raise errors.ApiError(
                409, "CONFLICT",
                "section_id de pedido terminal é imutável (o fato já ocorreu); "
                "edite o plano diretamente se a realidade mudou.",
            )
        if body.section_id is None:
            req.section_id = None
            req.section_label = None
        else:
            sec = _validate_section(session, camp, req.course_code, body.section_id)
            req.section_id = sec.id
            req.section_label = sec.label

    if "note" in fields:
        req.note = body.note  # note is editable in ANY state (D4)

    plan_synced = False
    if new_status is not None:
        req.status = new_status
        # ADR-017: sync on ACCEPT only, through the SAME code path as PUT /items
        if new_status == STATUS_ACEITA:
            if req.kind == "add":
                if not req.section_id:
                    raise errors.ApiError(
                        400, "BAD_REQUEST",
                        "Aceite de adição exige section_id (qual turma foi aceita?).",
                    )
                # put_item revalidates everything (section x course/term, term >=
                # current) and commits; on failure nothing is committed, so the
                # status change above is rolled back with it.
                put_item(
                    plan.id, req.course_code,
                    ItemPut(term=camp.term, section_id=req.section_id), session,
                )
                plan_synced = True
            else:  # drop: clear the chosen section, KEEP the allocation
                item = session.get(
                    PlanItem, {"plan_id": plan.id, "course_code": req.course_code}
                )
                if item is not None:
                    put_item(
                        plan.id, req.course_code,
                        ItemPut(term=item.term, section_id=None), session,
                    )
                    plan_synced = True
        # negada: no effect on plan_item, by design

    now = _now()
    req.updated_at = now
    camp.updated_at = now
    session.commit()

    response: dict = {
        "request": _serialize_request(session, plan, req),
        "plan_synced": plan_synced,
    }
    if plan_synced:
        # full revalidated plan: new diagnostics (TERM_OVERLOADED counting the
        # special round's credits — D1/D8 —, PREREQ_VIOLATION on authorized
        # breaks, etc.) surface here for the UI
        response["plan"] = service.serialize_plan(session, plan)
    return response


@router.delete("/plans/{plan_id}/campaigns/{campaign_id}/requests/{request_id}")
def delete_request(
    plan_id: str,
    campaign_id: str,
    request_id: int,
    session: Session = Depends(get_session),
) -> JSONResponse:
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    req = _get_request(session, camp, request_id)
    if req.status in TERMINAL_STATUSES:
        raise errors.ApiError(
            409, "CONFLICT",
            f"Pedido em estado terminal ('{req.status}') é histórico e não pode "
            "ser apagado.",
        )
    session.delete(req)
    camp.updated_at = _now()
    session.commit()
    return JSONResponse(status_code=204, content=None)


# ----- criticality queue (spec 6.5) -----------------------------------------


@router.get("/plans/{plan_id}/campaigns/{campaign_id}/queue")
def campaign_queue(
    plan_id: str,
    campaign_id: str,
    round: str = Query(min_length=1, max_length=50),
    session: Session = Depends(get_session),
) -> dict:
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    rounds = _rounds(session, plan)
    _round_or_400(rounds, round)

    _g, curriculum, user_state, plan_snap, _req, sections = service.engine_inputs(
        session, plan
    )
    of_term = [s for s in sections.sections.values() if s.term == camp.term]
    entries = criticality_queue(curriculum, user_state, plan_snap, camp.term, of_term)

    requests = _campaign_requests(session, camp)
    accepted_codes = {
        r.course_code for r in requests if r.kind == "add" and r.status == STATUS_ACEITA
    }
    requested_in_round = {r.course_code for r in requests if r.round == round}

    queue = [
        {
            "rank": i,
            "course_code": e.course_code,
            "name": (
                curriculum.courses[e.course_code].name
                if e.course_code in curriculum.courses
                else e.course_code
            ),
            "critical": e.critical,
            "height": e.height,
            "unlocks": e.unlocks,
            "credits": e.credits,
            "difficulty": e.difficulty,
            "blocked": e.blocked,
            "missing_prereqs": [
                {
                    "code": p,
                    "name": curriculum.courses[p].name if p in curriculum.courses else p,
                    "status": user_state.status_of(p),
                }
                for p in e.missing_prereqs
            ],
            "sections": list(e.section_ids),
            "already_accepted": e.course_code in accepted_codes,
            "requested_in_round": e.course_code in requested_in_round,
        }
        for i, e in enumerate(entries, 1)
    ]
    return {"term": camp.term, "round": round, "queue": queue}


# ===== C2: swap-suggestions + what-if (spec 6.6, ADR-019/024) ===============
# Block kept self-contained (imports included): V4.6 (C3) appends its own
# block to this file in parallel — nothing above this line is touched by C2.

from ..domain.schemas import SwapSuggestionsRequest, WhatIfRequest  # noqa: E402
from ..engine.campaign import suggest_swaps  # noqa: E402
from ..engine.schedule import hhmm  # noqa: E402
from ..engine.types import FALTA, KIND_OBRIGATORIA, KIND_OPTATIVA  # noqa: E402
from ..engine.validate import validate  # noqa: E402

_WHATIF_OUTCOMES = ("aceita", "negada")


@router.post("/plans/{plan_id}/campaigns/{campaign_id}/swap-suggestions")
def swap_suggestions(
    plan_id: str,
    campaign_id: str,
    body: SwapSuggestionsRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    """Correction-round assistant (C2, spec 6.6). Thin by design: builds the
    held/pool section snapshots and calls the PURE engine.suggest_swaps.

    held = accepted 'add' sections of the campaign UNION plan items of the
    campaign term with a chosen section; pool = sections of the term whose
    course is a non-held 'falta' of the grade (same universe as the queue).
    No beneficial swap is a 200 with suggestions=[] — never an error."""
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    top_k = body.max_suggestions if body is not None else 5

    _g, curriculum, user_state, plan_snap, _req, sections = service.engine_inputs(
        session, plan
    )

    accepted_ids = {
        r.section_id
        for r in _campaign_requests(session, camp)
        if r.kind == "add" and r.status == STATUS_ACEITA and r.section_id
    }
    item_ids = {
        i.section_id
        for i in plan_snap.items.values()
        if i.term == camp.term and i.section_id
    }
    # dead ids (section removed by a re-scrape) resolve to nothing and are
    # skipped: without timeslots there is no schedule to reason about
    held = [
        sections.sections[sid]
        for sid in sorted(accepted_ids | item_ids)
        if sid in sections.sections and sections.sections[sid].term == camp.term
    ]
    held_codes = {s.course_code for s in held}
    pool = [
        s
        for s in sections.sections.values()
        if s.term == camp.term
        and s.course_code not in held_codes
        and s.course_code in curriculum.courses
        and curriculum.courses[s.course_code].kind in (KIND_OBRIGATORIA, KIND_OPTATIVA)
        and user_state.status_of(s.course_code) == FALTA
    ]

    result = suggest_swaps(
        curriculum, user_state, plan_snap, camp.term, held, pool, top_k=top_k
    )

    suggestions = []
    for i, sug in enumerate(result["suggestions"], 1):
        freed = sorted(
            (
                {"weekday": t.weekday, "start": hhmm(t.start_min), "end": hhmm(t.end_min)}
                for s in sug["drop"]
                for t in s.timeslots
            ),
            key=lambda x: (x["weekday"], x["start"], x["end"]),
        )
        suggestions.append(
            {
                "rank": i,
                "drop": [
                    {"course_code": s.course_code, "section_id": s.id} for s in sug["drop"]
                ],
                "add": [
                    {"course_code": s.course_code, "section_id": s.id} for s in sug["add"]
                ],
                "score_delta": sug["score_delta"],
                "score_breakdown": sug["score_breakdown"],
                "freed_slots": freed,
                "conflicts_after": [],  # contract guarantee (6.6): always clean
            }
        )
    return {
        "term": camp.term,
        "held_sections": [s.id for s in held],
        "suggestions": suggestions,
        "truncated": result["truncated"],
    }


@router.post("/plans/{plan_id}/campaigns/{campaign_id}/what-if")
def what_if(
    plan_id: str,
    campaign_id: str,
    body: WhatIfRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Stateless what-if simulator (ADR-024): assumed outcomes are applied to
    an IN-MEMORY PlanSnapshot and the EXISTING validate/critical_path run on
    it. Nothing is ever persisted (`persisted: false` always) — there is no
    session.add/commit in this handler, by design. Real terminal requests
    (aceita/negada) can be re-assumed with any outcome (free simulation)."""
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    requests = {r.id: r for r in _campaign_requests(session, camp)}

    assumed: dict[int, str] = {}
    for a in body.assume:
        if a.outcome not in _WHATIF_OUTCOMES:
            raise errors.ApiError(
                400, "BAD_REQUEST",
                f"Outcome inválido: '{a.outcome}'.",
                details={"outcome": a.outcome, "allowed": list(_WHATIF_OUTCOMES)},
            )
        if a.request_id not in requests:
            raise errors.ApiError(
                400, "BAD_REQUEST",
                f"Pedido '{a.request_id}' não pertence a esta campanha.",
                details={"request_id": a.request_id},
            )
        assumed[a.request_id] = a.outcome  # last assumption for an id wins

    adds: dict[str, str | None] = {}
    drops: list[str] = []
    for rid in sorted(assumed):
        if assumed[rid] != "aceita":
            continue  # negada => no effect, by definition (ADR-024)
        req = requests[rid]
        if req.kind == "add":
            adds[req.course_code] = req.section_id
        else:
            drops.append(req.course_code)

    _g, curriculum, user_state, plan_snap, requirements, sections = (
        service.engine_inputs(session, plan)
    )
    hyp_snap = service.whatif_plan_snapshot(plan_snap, camp.term, adds, drops)

    baseline = validate(curriculum, user_state, plan_snap, requirements, sections)
    projection = validate(curriculum, user_state, hyp_snap, requirements, sections)

    def _side(vres) -> dict:
        return {
            "earliest_completion_term": vres.critical_path.earliest_completion_term,
            "critical_path": vres.critical_path.to_dict(),
            "term_summaries": [s.to_dict() for s in vres.term_summaries],
            "diagnostics": [d.to_dict() for d in vres.diagnostics],
        }

    return {
        "persisted": False,
        "baseline": _side(baseline),
        "projection": _side(projection),
    }


# ===== C3: special-candidates (spec 6.7, ADR-019/020) =======================
# Block kept self-contained (imports included), appended after C2 — nothing
# above this line is touched by C3.

from ..engine.campaign import rank_special_candidates  # noqa: E402

# The advisor is BY DEFINITION about the special round; its key is the only
# round name this module knows (the endpoint's own name carries it). The
# limit itself (max_choices) is ALWAYS read from the grade's rules (ADR-018),
# never hard-coded — a grade without an 'especial' round yields null.
_SPECIAL_ROUND_KEY = "especial"


@router.get("/plans/{plan_id}/campaigns/{campaign_id}/special-candidates")
def special_candidates(
    plan_id: str,
    campaign_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Special-enrollment advisor (C3, spec 6.7). Thin by design: builds the
    snapshots, calls the PURE engine.rank_special_candidates and serializes.

    `accepted` = accepted 'add' sections of THIS campaign (the fixed blocks);
    `offered_to` is read from the structured DB column (ADR-020) — NEVER
    parsed back from `note`. No offer in the term is a 200 with
    candidates=[], never an error; `rescrape_hint` derives from the OLDEST
    fetched_at among the term's sections (freshness nudge, spec 6.7)."""
    plan = _get_plan(session, plan_id)
    camp = _get_campaign(session, plan, campaign_id)
    rounds = _rounds(session, plan)
    special = next((r for r in rounds if r.get("key") == _SPECIAL_ROUND_KEY), None)
    max_choices = special.get("max_adds") if special is not None else None

    _g, curriculum, user_state, plan_snap, _req, sections = service.engine_inputs(
        session, plan
    )
    of_term = [s for s in sections.sections.values() if s.term == camp.term]

    accepted_ids = {
        r.section_id
        for r in _campaign_requests(session, camp)
        if r.kind == "add" and r.status == STATUS_ACEITA and r.section_id
    }
    # dead ids (section removed by a re-scrape) resolve to nothing: without
    # timeslots there is no conflict to reason about (same rule as C2)
    accepted = [
        sections.sections[sid]
        for sid in sorted(accepted_ids)
        if sid in sections.sections and sections.sections[sid].term == camp.term
    ]

    # offered_to from the structured column (ADR-020), plus the freshness scan
    rows = session.execute(
        select(Section.id, Section.offered_to, Section.fetched_at).where(
            Section.term == camp.term
        )
    ).all()
    offered_map: dict[str, list[str] | None] = {
        sid: (json.loads(raw) if raw else None) for sid, raw, _f in rows
    }
    fetched = sorted(f for _sid, _raw, f in rows if f)
    rescrape_hint = (
        f"oferta coletada em {fetched[0][:10]} — atualize antes de decidir"
        if fetched
        else None
    )

    ranked = rank_special_candidates(
        curriculum, user_state, plan_snap, camp.term, of_term, accepted, offered_map
    )

    candidates = []
    for i, c in enumerate(ranked, 1):
        best = sections.sections.get(c.section_id)
        candidates.append(
            {
                "rank": i,
                "course_code": c.course_code,
                "name": (
                    curriculum.courses[c.course_code].name
                    if c.course_code in curriculum.courses
                    else c.course_code
                ),
                "section_id": c.section_id,
                "section_label": best.label if best is not None else None,
                "score": c.score,
                "score_breakdown": c.score_breakdown,
                "blocked": c.blocked,
                "missing_prereqs": [
                    {
                        "code": p,
                        "name": (
                            curriculum.courses[p].name
                            if p in curriculum.courses
                            else p
                        ),
                        "status": user_state.status_of(p),
                    }
                    for p in c.missing_prereqs
                ],
                "capacity": best.capacity if best is not None else None,
                "enrolled": best.enrolled if best is not None else None,
                "full": c.full,
                "foreign_offer": offered_map.get(c.section_id) is not None,
                "offered_to": offered_map.get(c.section_id),
                "conflicts_with": list(c.conflicts_with),
                "alternatives": list(c.alternatives),
            }
        )
    return {
        "term": camp.term,
        "max_choices": max_choices,
        "candidates": candidates,
        "rescrape_hint": rescrape_hint,
    }
