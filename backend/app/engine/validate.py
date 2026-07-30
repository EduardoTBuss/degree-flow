"""validate(): RN1 (prereqs, F1-refined), RN2 (offer), RN4 (load), RN6 (hours),
plus v2 schedule/offer-data checks. Pure, deterministic, no short-circuit."""
from __future__ import annotations

from collections import defaultdict

from .critical_path import compute_critical_path
from .progress import category_progress
from .schedule import check_schedule
from .terms import term_ge, term_gt, term_key, term_lt, term_parity
from .types import (
    APROVADA,
    CURSANDO,
    FALTA,
    KIND_OBRIGATORIA,
    CurriculumSnapshot,
    Diagnostic,
    PlanSnapshot,
    RequirementsSnapshot,
    SectionsSnapshot,
    TermSummary,
    UserState,
    ValidationResult,
)


def _course_name(curriculum: CurriculumSnapshot, code: str) -> str:
    info = curriculum.courses.get(code)
    return info.name if info else code


def _prereq_satisfied(
    prereq: str, at_term: str, plan: PlanSnapshot, user_state: UserState
) -> tuple[bool, str | None, str | None]:
    """RN1 (F1-refined). Satisfied at `at_term` iff:
      (a) aprovada and (completed_term is None or completed_term < at_term); or
      (b) cursando and at_term > current_term; or
      (c) allocated strictly before at_term.
    Returns (ok, prereq_alloc_term, prereq_completed_term)."""
    st = user_state.status_of(prereq)
    if st == APROVADA:
        ct = user_state.completed_term_of(prereq)
        if ct is None or term_lt(ct, at_term):
            return True, None, ct
        return False, None, ct  # inconsistent: completed at/after the dependent term
    if st == CURSANDO and term_gt(at_term, plan.current_term):
        return True, None, None
    item = plan.items.get(prereq)
    if item is not None and term_lt(item.term, at_term):
        return True, item.term, None
    return False, (item.term if item is not None else None), None


def _term_summaries(
    curriculum: CurriculumSnapshot, user_state: UserState, plan: PlanSnapshot
) -> list[TermSummary]:
    """Aggregate per term. Courses `cursando` implicitly belong to current_term
    (ADR-004) and are included in its summary (unless explicitly allocated)."""
    by_term: dict[str, list[str]] = defaultdict(list)
    for code, item in plan.items.items():
        if code in curriculum.courses:
            by_term[item.term].append(code)
    for code, info in curriculum.courses.items():
        if user_state.status_of(code) == CURSANDO and code not in plan.items:
            by_term[plan.current_term].append(code)

    summaries = []
    for term in sorted(by_term, key=term_key):
        codes = sorted(by_term[term])
        credits = sum(curriculum.courses[c].credits for c in codes)
        diff = sum(user_state.difficulty_of(c) for c in codes)
        summaries.append(TermSummary(term, credits, diff, tuple(codes)))
    return summaries


def _schedule_diagnostics(
    curriculum: CurriculumSnapshot,
    plan: PlanSnapshot,
    sections: SectionsSnapshot,
) -> list[Diagnostic]:
    """NOT_OFFERED, SECTION_STALE and SCHEDULE_CONFLICT for allocated items."""
    diags: list[Diagnostic] = []
    chosen_by_term: dict[str, list] = defaultdict(list)

    for code in sorted(plan.items):
        item = plan.items[code]
        if code not in curriculum.courses:
            continue
        term = item.term

        # NOT_OFFERED: term has offer data but this course has no section in it
        if term in sections.terms_with_data:
            if not sections.by_course_term(code, term):
                diags.append(
                    Diagnostic(
                        id=f"NOT_OFFERED:{code}:{term}",
                        type="NOT_OFFERED",
                        severity="warning",
                        course_codes=(code,),
                        related_codes=(),
                        term=term,
                        message=(
                            f"{_course_name(curriculum, code)} está alocada em {term}, "
                            f"mas não há turma dela ofertada nesse semestre (dados podem estar incompletos)."
                        ),
                        details={"term": term, "sections_available": 0},
                    )
                )

        # chosen section handling
        if item.section_id is not None:
            sec = sections.sections.get(item.section_id)
            if sec is None:
                diags.append(_stale(code, item.section_id, "removed", curriculum))
            elif sec.term != term:
                diags.append(_stale(code, item.section_id, "term_mismatch", curriculum))
            else:
                chosen_by_term[term].append(sec)

    # SCHEDULE_CONFLICT per term (warning — A6)
    for term in sorted(chosen_by_term):
        diags.extend(check_schedule(chosen_by_term[term]))
    return diags


def _stale(code: str, section_id: str, reason: str, curriculum: CurriculumSnapshot) -> Diagnostic:
    return Diagnostic(
        id=f"SECTION_STALE:{code}:{section_id}",
        type="SECTION_STALE",
        severity="warning",
        course_codes=(code,),
        related_codes=(),
        term=None,
        message=(
            f"A turma escolhida para {_course_name(curriculum, code)} não é mais válida "
            f"({'removida' if reason == 'removed' else 'de outro semestre'}) — escolha outra."
        ),
        details={"section_id": section_id, "reason": reason},
    )


def validate(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    requirements: RequirementsSnapshot,
    sections: SectionsSnapshot | None = None,
) -> ValidationResult:
    diagnostics: list[Diagnostic] = []
    terms_with_data = sections.terms_with_data if sections else frozenset()

    critical, cycle_diags = compute_critical_path(
        curriculum, user_state, plan.current_term
    )
    diagnostics.extend(cycle_diags)
    summaries = _term_summaries(curriculum, user_state, plan)
    if cycle_diags:
        # corrupted seed data: abort remaining checks (section 8, note 1)
        return ValidationResult(
            tuple(diagnostics), (), critical, tuple(summaries)
        )

    prereq_violation_codes: set[str] = set()

    # F1: INCONSISTENT_COMPLETION (info) — aprovada completed at/after current_term
    for code, info in curriculum.courses.items():
        if user_state.status_of(code) != APROVADA:
            continue
        ct = user_state.completed_term_of(code)
        if ct is not None and term_ge(ct, plan.current_term):
            diagnostics.append(
                Diagnostic(
                    id=f"INCONSISTENT_COMPLETION:{code}",
                    type="INCONSISTENT_COMPLETION",
                    severity="info",
                    course_codes=(code,),
                    related_codes=(),
                    term=ct,
                    message=(
                        f"{info.name} está aprovada mas com conclusão em {ct}, que não é "
                        f"anterior ao semestre atual ({plan.current_term}) — confira o dado."
                    ),
                    details={"completed_term": ct, "current_term": plan.current_term},
                )
            )

    # RN1 + RN2 per allocated item
    for code in sorted(plan.items, key=lambda c: (term_key(plan.items[c].term), c)):
        item = plan.items[code]
        info = curriculum.courses.get(code)
        if info is None:
            continue
        for prereq in info.prereqs:
            if prereq not in curriculum.courses:
                continue
            ok, prereq_term, prereq_ct = _prereq_satisfied(prereq, item.term, plan, user_state)
            if not ok:
                prereq_violation_codes.add(code)
                pname = _course_name(curriculum, prereq)
                if prereq_ct is not None:
                    tail = f"mas {pname} consta concluída só em {prereq_ct}."
                elif prereq_term is not None:
                    tail = f"mas {pname} está alocada em {prereq_term}."
                else:
                    tail = f"mas {pname} não está concluída nem alocada antes."
                diagnostics.append(
                    Diagnostic(
                        id=f"PREREQ_VIOLATION:{code}:{item.term}:{prereq}",
                        type="PREREQ_VIOLATION",
                        severity="error",
                        course_codes=(code,),
                        related_codes=(prereq,),
                        term=item.term,
                        message=(
                            f"{info.name} em {item.term} exige {pname} concluída antes, {tail}"
                        ),
                        details={
                            "missing_prereq": prereq,
                            "prereq_term": prereq_term,
                            "prereq_completed_term": prereq_ct,
                        },
                    )
                )
        # RN2 offer parity — suppressed for terms that have real section data (ADR-007)
        if (
            item.term not in terms_with_data
            and info.offer in ("/1", "/2")
            and term_parity(item.term) != info.offer
        ):
            diagnostics.append(
                Diagnostic(
                    id=f"OFFER_MISMATCH:{code}:{item.term}",
                    type="OFFER_MISMATCH",
                    severity="warning",
                    course_codes=(code,),
                    related_codes=(),
                    term=item.term,
                    message=(
                        f"{info.name} é ofertada apenas em semestres {info.offer}, "
                        f"mas está alocada em {item.term} (semestre {term_parity(item.term)})."
                    ),
                    details={"offer": info.offer, "term_parity": term_parity(item.term)},
                )
            )

    # RN4: independent limits — credits and difficulty (D4)
    for s in summaries:
        over_credits = s.credits > plan.max_credits_per_term
        over_diff = (
            plan.max_difficulty_per_term is not None
            and s.difficulty_sum > plan.max_difficulty_per_term
        )
        if over_credits or over_diff:
            parts = []
            if over_credits:
                parts.append(f"{s.credits} créditos (máx. {plan.max_credits_per_term})")
            if over_diff:
                parts.append(
                    f"dificuldade {s.difficulty_sum} (máx. {plan.max_difficulty_per_term})"
                )
            diagnostics.append(
                Diagnostic(
                    id=f"TERM_OVERLOADED:{s.term}",
                    type="TERM_OVERLOADED",
                    severity="warning",
                    course_codes=s.course_codes,
                    related_codes=(),
                    term=s.term,
                    message=f"Semestre {s.term} sobrecarregado: " + " e ".join(parts) + ".",
                    details={
                        "credits": s.credits,
                        "max_credits": plan.max_credits_per_term,
                        "difficulty_sum": s.difficulty_sum,
                        "max_difficulty": plan.max_difficulty_per_term,
                    },
                )
            )

    # UNALLOCATED_REQUIRED (only with a target set)
    unallocated = sorted(
        code
        for code, info in curriculum.courses.items()
        if info.kind == KIND_OBRIGATORIA
        and user_state.status_of(code) == FALTA
        and code not in plan.items
    )
    if plan.target_term and unallocated:
        diagnostics.append(
            Diagnostic(
                id="UNALLOCATED_REQUIRED",
                type="UNALLOCATED_REQUIRED",
                severity="warning",
                course_codes=tuple(unallocated),
                related_codes=(),
                term=None,
                message=(
                    f"{len(unallocated)} obrigatória(s) pendente(s) ainda sem alocação no plano."
                ),
                details={"count": len(unallocated)},
            )
        )

    # RN6: REQUIREMENT_SHORTFALL (only with a target set)
    if plan.target_term:
        for prog in category_progress(curriculum, user_state, plan, requirements):
            if not prog["satisfied"]:
                diagnostics.append(
                    Diagnostic(
                        id=f"REQUIREMENT_SHORTFALL:{prog['key']}",
                        type="REQUIREMENT_SHORTFALL",
                        severity="warning",
                        course_codes=(),
                        related_codes=(),
                        term=None,
                        message=(
                            f"Categoria '{prog['label']}' não fecha até {plan.target_term}: "
                            f"faltam {prog['remaining_hours']}h de {prog['min_hours']}h."
                        ),
                        details={
                            "category_key": prog["key"],
                            "remaining_hours": prog["remaining_hours"],
                        },
                    )
                )

    # F3: schedule / offer-data diagnostics (only when sections provided)
    if sections is not None:
        diagnostics.extend(_schedule_diagnostics(curriculum, plan, sections))

    # blocked (derived, never persisted)
    blocked: set[str] = set(prereq_violation_codes)
    for code, info in curriculum.courses.items():
        if user_state.status_of(code) != FALTA:
            continue
        for prereq in info.prereqs:
            if prereq not in curriculum.courses:
                continue
            if user_state.status_of(prereq) in (APROVADA, CURSANDO):
                continue
            if prereq in plan.items:
                continue
            blocked.add(code)
            break

    return ValidationResult(
        diagnostics=tuple(diagnostics),
        blocked_codes=tuple(sorted(blocked)),
        critical_path=critical,
        term_summaries=tuple(summaries),
    )
