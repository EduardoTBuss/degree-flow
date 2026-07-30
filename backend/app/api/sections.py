"""Offer data: sections/timeslots per term (F3a). Manual import is primary.

v3 (F5): the scraper writes with SCOPE (`scope_codes`, ADR-013) so it never
destroys manually imported sections of other courses, and the scrape endpoint
is per-plan (target term derived from the plan — ADR-011).

v3 (ADR-016): the same scrape also DISCOVERS electives — offered disciplines
outside the plan's grade that the user has not done — and stores them as
read-only offers (their Course is materialized with unknown credits/hours and
is never added to any curriculum_entry, so the flowchart/engine never see them).

v5 (ADR-025/026/027): the same course page also carries the CURRICULUM MATRIX
(`div#curriculo`) — the catalog of what exists per grade with real kind/
credits/hours/prereqs. The scrape persists it in `elective_catalog` (replace
per grade, only when the parse produced rows), fixes orphan Courses with the
real data (seed-owned Courses are never overwritten) and reports the seed ×
portal divergence (`catalog_diff` — a report, never a Diagnostic)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi import Path as PathParam
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import errors
from ..domain.schemas import SectionsPayload
from ..engine.campaign import eligibility_at_term
from ..engine.terms import next_term
from ..engine.types import (
    APROVADA,
    CURSANDO,
    FALTA,
    KIND_ATIVIDADE,
    KIND_OBRIGATORIA,
    KIND_OPTATIVA,
)
from ..persistence import repos
from ..persistence.models import (
    Course,
    CurriculumEntry,
    ElectiveCatalog,
    GradeVersion,
    Meta,
    Plan,
    PlanItem,
    Section,
    Timeslot,
)
from ..scrapers.ufpel import MatrixRow, scrape_offers
from . import service
from .deps import get_session

_PORTAL_CODE_RE = re.compile(r"\d+")
SOURCE_OPTATIVA = "scraper:ufpel-optativa"
SOURCE_CATALOGO = "scraper:ufpel-curriculo"  # v5 (ADR-025/027)

router = APIRouter(prefix="/api/v1", tags=["sections"])


def _norm_term(t: str) -> str:
    return t.replace("-", "/")


def _hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _min_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _serialize_section(session: Session, sec: Section) -> dict:
    slots = session.execute(
        select(Timeslot).where(Timeslot.section_id == sec.id).order_by(
            Timeslot.weekday, Timeslot.start_min
        )
    ).scalars().all()
    course = session.get(Course, sec.course_code)
    offered_to = json.loads(sec.offered_to) if sec.offered_to else None  # v3 (ADR-020)
    return {
        "id": sec.id,
        "course_code": sec.course_code,
        "course_name": course.name if course else None,
        "term": sec.term,
        "label": sec.label,
        "professor": sec.professor,
        "capacity": sec.capacity,
        "enrolled": sec.enrolled,
        "source": sec.source,
        "fetched_at": sec.fetched_at,
        "note": sec.note,
        "offered_to": offered_to,
        "foreign_offer": offered_to is not None,
        "timeslots": [
            {"weekday": s.weekday, "start": _min_to_hhmm(s.start_min), "end": _min_to_hhmm(s.end_min)}
            for s in slots
        ],
    }


@router.get("/terms/{term}/sections")
def list_sections(
    term: str, course_code: str | None = None, session: Session = Depends(get_session)
) -> dict:
    t = _norm_term(term)
    q = select(Section).where(Section.term == t)
    if course_code:
        q = q.where(Section.course_code == course_code)
    sections = session.execute(q.order_by(Section.course_code, Section.label)).scalars().all()
    has_data = session.execute(
        select(Section.id).where(Section.term == t).limit(1)
    ).first() is not None
    return {
        "term": t,
        "has_data": has_data,
        "sections": [_serialize_section(session, s) for s in sections],
    }


def _import_payload(
    session: Session,
    term: str,
    payload: SectionsPayload,
    source: str,
    scope_codes: set[str] | None = None,
) -> dict:
    """Replace the offer of `term` with the payload (upsert by id, remove missing).

    ADR-013: `scope_codes=None` -> full-term replace (manual PUT, unchanged).
    With a set, delete/insert are restricted to those course codes; payload
    sections outside the scope are rejected ("fora do escopo") and
    upserted/removed/orphaned_choices are relative to the scope.
    """
    old_q = select(Section).where(Section.term == term)
    if scope_codes is not None:
        old_q = old_q.where(Section.course_code.in_(scope_codes))
    old_sections = session.execute(old_q).scalars().all()
    old_ids = {s.id for s in old_sections}
    rejected: list[dict] = []
    new_ids: set[str] = set()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # wipe old sections of this term/scope (timeslots cascade)
    for sec in old_sections:
        session.delete(sec)
    session.flush()

    for sin in payload.sections:
        if scope_codes is not None and sin.course_code not in scope_codes:
            rejected.append({"course_code": sin.course_code, "reason": "fora do escopo"})
            continue
        if session.get(Course, sin.course_code) is None:
            rejected.append({"course_code": sin.course_code, "reason": "código inexistente"})
            continue
        label = sin.label or "T"
        sid = sin.id or f"{term.replace('/', '-')}:{sin.course_code}:{label}"
        if sid in new_ids:
            continue
        sec = Section(
            id=sid,
            course_code=sin.course_code,
            term=term,
            label=sin.label,
            professor=sin.professor,
            capacity=sin.capacity,
            enrolled=sin.enrolled,
            source=source,
            note=sin.note,
            # v3 (ADR-020): JSON dump of the list; None/empty -> NULL
            offered_to=(
                json.dumps(sin.offered_to, ensure_ascii=False) if sin.offered_to else None
            ),
            fetched_at=now,
        )
        session.add(sec)
        session.flush()
        new_ids.add(sid)
        for ts in sin.timeslots:
            start, end = _hhmm_to_min(ts.start), _hhmm_to_min(ts.end)
            if end <= start:
                continue
            session.add(
                Timeslot(section_id=sid, weekday=ts.weekday, start_min=start, end_min=end)
            )

    # orphan plan_item choices that pointed to removed sections
    orphaned = 0
    removed = old_ids - new_ids
    if removed:
        for pi in session.execute(
            select(PlanItem).where(PlanItem.section_id.in_(removed))
        ).scalars().all():
            pi.section_id = None
            orphaned += 1

    session.commit()
    return {
        "term": term,
        "upserted": len(new_ids),
        "removed": len(removed),
        "orphaned_choices": orphaned,
        "rejected": rejected,
        "source": source,
    }


@router.put("/admin/terms/{term}/sections")
def import_sections(
    term: str, payload: SectionsPayload, session: Session = Depends(get_session)
) -> dict:
    t = _norm_term(term)
    source = payload.source or "manual"
    return _import_payload(session, t, payload, source)


def _materialize_optativas(
    session: Session,
    codes: set[str],
    names: dict[str, str],
    matrix: dict[str, MatrixRow] | None = None,
) -> None:
    """Create minimal Course rows for discovered electives (ADR-016).

    v5 (ADR-026): when the curriculum matrix knows the code, the REAL
    name/credits/hours are used (fixes the credits=0 of Option A); codes the
    matrix does not know keep the honest 0. Existing Courses are never touched
    here. The course is NEVER added to a curriculum_entry, so the engine and
    the flowchart never consider it. It survives reimport (seed upserts by
    code and never deletes extras)."""
    matrix = matrix or {}
    for code in codes:
        if session.get(Course, code) is None:
            row = matrix.get(code)
            session.add(
                Course(
                    code=code,
                    name=row.name if row is not None else names.get(code, code),
                    short=None,
                    credits=row.credits if row is not None else 0,
                    hours=row.hours if row is not None else 0.0,
                    default_offer="both",
                )
            )
    session.flush()


def _write_elective_catalog(
    session: Session, grade: GradeVersion, rows: list[MatrixRow], fetched_at: str
) -> dict:
    """Replace the grade's elective_catalog with the parsed matrix (ADR-027).

    The caller guarantees ``rows`` is non-empty — ``rows_parsed == 0`` never
    erases a previously stored catalog. Course materialization (ADR-026):
    a code WITHOUT any curriculum_entry in any grade (orphan) is created or
    refreshed with the real name/credits/hours from the matrix; a Course owned
    by the seed (e.g. CLP 22000188, FIA 22000301) is NEVER overwritten — the
    seed keeps its curation. Returns {created_courses, updated_courses}."""
    seed_owned = set(
        session.execute(select(CurriculumEntry.course_code).distinct()).scalars()
    )
    created = updated = 0
    for row in rows:
        course = session.get(Course, row.code)
        if course is None:
            session.add(
                Course(
                    code=row.code,
                    name=row.name,
                    short=None,
                    credits=row.credits,
                    hours=row.hours,
                    default_offer="both",
                )
            )
            created += 1
        elif row.code not in seed_owned and (
            course.name != row.name
            or course.credits != row.credits
            or course.hours != row.hours
        ):
            # orphan materialized by a previous scrape — fix the 0s (ADR-026)
            course.name = row.name
            course.credits = row.credits
            course.hours = row.hours
            updated += 1
    session.flush()

    session.execute(
        delete(ElectiveCatalog).where(ElectiveCatalog.grade_version_id == grade.id)
    )
    session.flush()
    for row in rows:
        session.add(
            ElectiveCatalog(
                grade_version_id=grade.id,
                course_code=row.code,
                kind=row.kind,
                credits=row.credits,
                hours=row.hours,
                prereqs=json.dumps(list(row.prereqs)),
                portal_kind=row.portal_kind,
                suggested_term_index=row.suggested_term_index,
                source=SOURCE_CATALOGO,
                fetched_at=fetched_at,
            )
        )
    session.flush()
    return {"created_courses": created, "updated_courses": updated}


def _catalog_diff(session: Session, grade: GradeVersion) -> dict:
    """seed × portal divergence report (ADR-026), from the PERSISTED catalog.

    Reads the CANONICAL grade (`repos.curriculum_rows`) on purpose: the diff
    compares what the seed declares with what the portal publishes — never the
    user-effective view, and never a Diagnostic (the fix is a human editing
    the seed). Empty catalog (never scraped / failed parse) -> all-empty
    report, which is also the healthy state when everything matches."""
    diff: dict = {
        "missing_in_matrix": [],
        "kind_changed": [],
        "hours_changed": [],
        "new_obligatory": [],
    }
    matrix = {
        cat.course_code: (cat, course)
        for cat, course in repos.elective_catalog_rows(session, grade.id)
    }
    if not matrix:
        return diff

    grade_codes: set[str] = set()
    for entry, course in repos.curriculum_rows(session, grade.id):
        grade_codes.add(course.code)
        hit = matrix.get(course.code)
        if hit is None:
            if entry.kind == KIND_OBRIGATORIA:
                diff["missing_in_matrix"].append(
                    {"code": course.code, "name": course.name}
                )
            continue
        cat, _ = hit
        if cat.kind != entry.kind:
            diff["kind_changed"].append(
                {"code": course.code, "seed": entry.kind, "matrix": cat.kind}
            )
        if float(cat.hours) != float(course.hours):
            diff["hours_changed"].append(
                {"code": course.code, "seed": course.hours, "matrix": cat.hours}
            )
    diff["new_obligatory"] = [
        {"code": code, "name": course.name}
        for code, (cat, course) in sorted(matrix.items())
        if cat.kind == KIND_OBRIGATORIA and code not in grade_codes
    ]
    return diff


@router.post("/admin/plans/{plan_id}/sections/scrape")
def scrape_plan_sections(plan_id: str, session: Session = Depends(get_session)) -> dict:
    """F5 (ADR-011): 2-level scrape for the plan's target term = next_term(current_term).

    Scope = courses of the plan's grade still 'falta' (kind obrigatoria|optativa).
    Also discovers electives outside the grade the user has not done (ADR-016).
    Synthetic codes (MERGE:…) never hit the portal and are reported in
    `missing_without_offer`. No DB side effect on scraper failure (502)."""
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Plano '{plan_id}' não encontrado.")

    grade = repos.get_grade(session, plan.grade_version_id)
    if grade is None:
        raise errors.ApiError(500, "INTERNAL_ERROR", "Grade do plano não encontrada.")

    # ADR-018: the portal code lives on the plan's grade; the meta k/v of
    # ADR-012 stays as fallback for v3 dbs that never re-imported the seed.
    portal_course_code = grade.portal_course_code
    if not portal_course_code:
        meta_row = session.get(Meta, "portal_course_code")
        portal_course_code = meta_row.v if meta_row is not None and meta_row.v else None
    if not portal_course_code:
        raise errors.ApiError(
            400, "PORTAL_CODE_MISSING",
            "Código do curso no portal ausente: preencha meta.codigo_curso no seed "
            "(e reinicie/reimporte) ou use o import manual "
            "(PUT /admin/terms/{term}/sections).",
        )

    term = next_term(plan.current_term)

    # scope: grade courses still 'falta' (effective status), with a name map
    states = repos.user_state_rows(session)
    groups, policies = repos.merge_groups(session, grade)
    wanted: set[str] = set()
    names: dict[str, str] = {}
    grade_codes: set[str] = set()
    for entry, course in repos.curriculum_rows(session, grade.id):
        grade_codes.add(course.code)
        if entry.kind not in (KIND_OBRIGATORIA, KIND_OPTATIVA):
            continue
        if repos.effective_status(course.code, states, groups, policies) != FALTA:
            continue
        wanted.add(course.code)
        names[course.code] = course.name

    # ADR-016: discovery is "outside the grade" by definition — exclude what the
    # user already did AND every grade code regardless of status/kind (a course
    # of the curriculum is never a "discovered" elective, even if pendente).
    done_codes = {
        c for c, r in states.items() if r.status in (APROVADA, CURSANDO)
    } | grade_codes

    try:
        result = scrape_offers(
            portal_course_code, term, wanted, done_codes=done_codes, discover=True
        )
    except Exception as exc:  # noqa: BLE001 - adapter isolation (ADR-008)
        raise errors.ApiError(
            502, "SCRAPER_FAILED",
            f"Falha ao coletar turmas do portal: {exc}. Use o import manual.",
        )

    # write grade offers with scope (ADR-013): only portal-coded wanted courses
    scope = {c for c in wanted if _PORTAL_CODE_RE.fullmatch(c)}
    summary = _import_payload(
        session, term, result.payload,
        result.payload.source or "scraper:ufpel", scope_codes=scope,
    )

    # v5 (ADR-025/027): persist the curriculum-matrix CATALOG (replace per
    # grade) — only when the parse produced rows; a failed/empty parse never
    # erases the stored catalog and never blocks the offer written above.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    matrix_by_code = {r.code: r for r in result.catalog}
    catalog_counts = {"created_courses": 0, "updated_courses": 0}
    if result.catalog:
        catalog_counts = _write_elective_catalog(session, grade, result.catalog, now)
        session.commit()
    catalog_report = {
        "source": SOURCE_CATALOGO,
        "rows_parsed": len(result.catalog),
        "obrigatorias": sum(1 for r in result.catalog if r.kind == KIND_OBRIGATORIA),
        "optativas": sum(1 for r in result.catalog if r.kind == KIND_OPTATIVA),
        "atividades": sum(1 for r in result.catalog if r.kind == KIND_ATIVIDADE),
        **catalog_counts,
        "unknown_kind": result.catalog_unknown,
        "fetched_at": now if result.catalog else None,
        "error": result.catalog_error,
    }

    # ADR-016: materialize + store discovered electives as their own scope
    discovered_codes = {s.course_code for s in result.discovered_sections}
    discovered: list[dict] = []
    if discovered_codes:
        _materialize_optativas(
            session, discovered_codes, result.discovered_names, matrix_by_code
        )
        _import_payload(
            session, term,
            SectionsPayload(term=term, sections=result.discovered_sections, source=SOURCE_OPTATIVA),
            SOURCE_OPTATIVA, scope_codes=discovered_codes,
        )
        counts: dict[str, int] = {}
        for s in result.discovered_sections:
            counts[s.course_code] = counts.get(s.course_code, 0) + 1
        discovered = [
            {"code": c, "name": result.discovered_names.get(c, c), "sections": counts[c]}
            for c in sorted(discovered_codes)
        ]

    return {
        "term": term,
        "source": result.payload.source,
        "level_used": result.level_used,
        "upserted": summary["upserted"],
        "removed": summary["removed"],
        "orphaned_choices": summary["orphaned_choices"],
        "rejected": summary["rejected"],
        "missing_without_offer": [
            {"code": m["code"], "name": names.get(m["code"], m["code"]), "reason": m["reason"]}
            for m in result.missing
        ],
        "discovered_optativas": discovered,
        "scanned_codes": sorted(scope),
        "pages_fetched": result.pages_fetched,
        "pages_failed": result.pages_failed,
        # v5 (ADR-025/026) — additive: catalog write report + seed × portal diff
        # (a report, never a Diagnostic; computed from the PERSISTED catalog)
        "catalog": catalog_report,
        "catalog_diff": _catalog_diff(session, grade),
    }


@router.get("/admin/plans/{plan_id}/catalog-diff")
def plan_catalog_diff(plan_id: str, session: Session = Depends(get_session)) -> dict:
    """v5 (ADR-026, spec §4.4): recompute the seed × portal report from the
    PERSISTED elective_catalog — no network, no writes. Reads the CANONICAL
    grade of the plan on purpose (the diff is about what the seed declares).
    Never-scraped catalog -> all-empty lists + fetched_at null (not an error)."""
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Plano '{plan_id}' não encontrado.")
    grade = repos.get_grade(session, plan.grade_version_id)
    if grade is None:
        raise errors.ApiError(500, "INTERNAL_ERROR", "Grade do plano não encontrada.")
    rows = repos.elective_catalog_rows(session, grade.id)
    fetched_at = rows[0][0].fetched_at if rows else None
    return {**_catalog_diff(session, grade), "fetched_at": fetched_at}


@router.get("/plans/{plan_id}/terms/{term}/eligibility")
def course_eligibility(
    plan_id: str,
    term: str = PathParam(pattern=r"^\d{4}[-/][12]$"),
    session: Session = Depends(get_session),
) -> dict:
    """A2 (ADR-021): per-(plan, term) eligibility of the grade's 'falta' courses.

    Thin router over the pure `engine.campaign.eligibility_at_term`: builds the
    snapshots, calls the engine, joins name/status from the catalog. The front
    only renders badge + tooltip; picking a blocked section stays allowed (the
    existing PUT /items already answers with PREREQ_VIOLATION — D6)."""
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise errors.ApiError(404, "NOT_FOUND", f"Plano '{plan_id}' não encontrado.")
    t = _norm_term(term)
    _grade, curriculum, user_state, plan_snap, _req, _sec = service.engine_inputs(session, plan)
    eligibility = eligibility_at_term(curriculum, user_state, plan_snap, t)
    courses = [
        {
            "code": code,
            "eligible": e.eligible,
            "missing_prereqs": [
                {
                    "code": p,
                    "name": curriculum.courses[p].name if p in curriculum.courses else p,
                    "status": user_state.status_of(p),
                }
                for p in e.missing_prereqs
            ],
        }
        for code, e in sorted(eligibility.items())
    ]
    return {"term": t, "courses": courses}
