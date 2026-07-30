"""autoplan(): greedy level-based distribution towards a graduation target.

Respects locks (inviolable), offer parity, credit/difficulty limits and never
allocates before current_term + 1 (section 8, note 4). Pure, no I/O.
"""
from __future__ import annotations

from .critical_path import compute_critical_path, remaining_required
from .graph import heights, internal_prereqs, topo_sort
from .terms import add_terms, next_term, term_gt, term_key, term_lt, term_parity
from .types import (
    APROVADA,
    CURSANDO,
    FALTA,
    AutoplanResult,
    CurriculumSnapshot,
    Diagnostic,
    PlanItemSnap,
    PlanSnapshot,
    ProposedItem,
    RequirementsSnapshot,
    UserState,
)
from .validate import validate

_HORIZON_EXTRA = 40  # safety guard: never plan more than this past the target


def _offer_ok(offer: str, term: str) -> bool:
    return offer == "both" or offer == term_parity(term)


def autoplan(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    requirements: RequirementsSnapshot,
    target_term: str,
    respect_existing: bool = False,
) -> AutoplanResult:
    critical, cycle_diags = compute_critical_path(
        curriculum, user_state, plan.current_term
    )
    if cycle_diags:
        return AutoplanResult(False, (), tuple(cycle_diags), critical)

    diagnostics: list[Diagnostic] = []
    status = user_state.status_of
    done = {c for c in curriculum.courses if status(c) == APROVADA}
    cursando = {c for c in curriculum.courses if status(c) == CURSANDO}

    # fixed items: locks always; every existing item when respect_existing
    fixed: dict[str, PlanItemSnap] = {
        code: item
        for code, item in plan.items.items()
        if (item.locked or respect_existing) and code in curriculum.courses
    }

    # scheduling set: remaining mandatory courses + user-allocated non-mandatory
    to_schedule: set[str] = set()
    for code, info in curriculum.courses.items():
        if code in fixed or status(code) != FALTA:
            continue
        if code in remaining_required(curriculum, user_state) or code in plan.items:
            to_schedule.add(code)

    # courses whose prereq chain can never be satisfied inside this plan
    # (prereq is falta but outside the scheduling universe, e.g. an optional)
    satisfiable = done | cursando | set(fixed)
    unschedulable: set[str] = set()
    changed = True
    while changed:
        changed = False
        for c in to_schedule:
            if c in unschedulable:
                continue
            for p in curriculum.courses[c].prereqs:
                if p not in curriculum.courses:
                    continue
                ok = (
                    p in satisfiable
                    or (p in to_schedule and p not in unschedulable)
                )
                if not ok:
                    unschedulable.add(c)
                    changed = True
                    break

    remaining = to_schedule - unschedulable

    # priorities: critical-path membership, height (dependents below), difficulty
    prereqs_of = {c: curriculum.courses[c].prereqs for c in remaining}
    order, _ = topo_sort(remaining, prereqs_of)
    pre = internal_prereqs(remaining, prereqs_of)
    height = heights(order, pre)
    cp_set = set(critical.course_codes)

    scheduled: dict[str, str] = {}  # code -> term
    max_fixed_term = max(
        (item.term for item in fixed.values()), key=term_key, default=None
    )
    horizon = add_terms(
        max(target_term, plan.current_term, key=term_key), _HORIZON_EXTRA
    )

    t = next_term(plan.current_term)
    empty_streak = 0
    while remaining:
        if term_gt(t, horizon):
            break
        pending_fixed_ahead = max_fixed_term is not None and not term_gt(
            t, max_fixed_term
        )
        if empty_streak >= 2 and not pending_fixed_ahead:
            break  # both parities tried with no progress: nothing will unblock

        completed = set(done)
        completed |= cursando  # t > current_term always holds here
        completed |= {c for c, tt in scheduled.items() if term_lt(tt, t)}
        completed |= {c for c, it in fixed.items() if term_lt(it.term, t)}

        credits_used = sum(
            curriculum.courses[c].credits
            for c, it in fixed.items()
            if it.term == t
        )
        diff_used = sum(
            user_state.difficulty_of(c) for c, it in fixed.items() if it.term == t
        )

        candidates = [
            c
            for c in remaining
            if _offer_ok(curriculum.courses[c].offer, t)
            and all(
                p in completed
                for p in curriculum.courses[c].prereqs
                if p in curriculum.courses
            )
        ]
        candidates.sort(
            key=lambda c: (
                0 if c in cp_set else 1,
                -height.get(c, 1),
                user_state.difficulty_of(c),
                c,
            )
        )

        allocated_any = False
        for c in candidates:
            info = curriculum.courses[c]
            if credits_used + info.credits > plan.max_credits_per_term:
                continue
            if (
                plan.max_difficulty_per_term is not None
                and diff_used + user_state.difficulty_of(c)
                > plan.max_difficulty_per_term
            ):
                continue
            scheduled[c] = t
            remaining.discard(c)
            credits_used += info.credits
            diff_used += user_state.difficulty_of(c)
            allocated_any = True

        empty_streak = 0 if allocated_any else empty_streak + 1
        t = next_term(t)

    # ---- proposal ----
    proposal_items: dict[str, ProposedItem] = {}
    for code, item in fixed.items():
        proposal_items[code] = ProposedItem(code, item.term, item.locked)
    for code, term in scheduled.items():
        proposal_items[code] = ProposedItem(code, term, False)
    proposal = tuple(
        sorted(proposal_items.values(), key=lambda i: (term_key(i.term), i.course_code))
    )

    completion_term = max(
        (i.term for i in proposal), key=term_key, default=plan.current_term
    )

    feasible = True

    # locks are inviolable: detect locks that break the plan
    for code, item in fixed.items():
        if not item.locked:
            continue
        info = curriculum.courses[code]
        completed_before = set(done)
        if term_gt(item.term, plan.current_term):
            completed_before |= cursando
        completed_before |= {
            c for c, tt in scheduled.items() if term_lt(tt, item.term)
        }
        completed_before |= {
            c
            for c, it in fixed.items()
            if c != code and term_lt(it.term, item.term)
        }
        for p in info.prereqs:
            if p in curriculum.courses and p not in completed_before:
                feasible = False
                diagnostics.append(
                    Diagnostic(
                        id=f"LOCK_CONFLICT:{code}:{item.term}",
                        type="LOCK_CONFLICT",
                        severity="error",
                        course_codes=(code,),
                        related_codes=(p,),
                        term=item.term,
                        message=(
                            f"Trava em {info.name} ({item.term}) inviabiliza o plano: "
                            f"pré-requisito {curriculum.courses[p].name} não conclui antes de {item.term}."
                        ),
                        details={
                            "locked_code": code,
                            "locked_term": item.term,
                            "required_by_term": add_terms(item.term, -1),
                        },
                    )
                )
                break
        else:
            if term_gt(item.term, target_term):
                feasible = False
                diagnostics.append(
                    Diagnostic(
                        id=f"LOCK_CONFLICT:{code}:{item.term}",
                        type="LOCK_CONFLICT",
                        severity="error",
                        course_codes=(code,),
                        related_codes=(),
                        term=item.term,
                        message=(
                            f"Trava em {info.name} fixa a cadeira em {item.term}, "
                            f"depois do alvo de formatura {target_term}."
                        ),
                        details={
                            "locked_code": code,
                            "locked_term": item.term,
                            "required_by_term": target_term,
                        },
                    )
                )

    # target unreachable: leftovers, unschedulable chains or completion past target
    if remaining or unschedulable or term_gt(completion_term, target_term):
        feasible = False
        bottleneck = list(critical.course_codes)
        earliest = completion_term
        if remaining or unschedulable:
            leftovers = sorted(remaining | unschedulable)
            bottleneck = leftovers
            earliest = None
            msg = (
                f"Alvo {target_term} inviável: {len(leftovers)} cadeira(s) não "
                f"pôde(ram) ser alocada(s) — pré-requisitos fora do plano ou oferta incompatível."
            )
        else:
            msg = (
                f"Alvo {target_term} inviável: conclusão mais cedo possível com as "
                f"restrições atuais é {completion_term}."
            )
        diagnostics.append(
            Diagnostic(
                id=f"TARGET_UNREACHABLE:{target_term}",
                type="TARGET_UNREACHABLE",
                severity="error",
                course_codes=tuple(bottleneck),
                related_codes=(),
                term=target_term,
                message=msg,
                details={
                    "target_term": target_term,
                    "earliest_completion_term": earliest,
                    "bottleneck_chain": bottleneck,
                },
            )
        )

    # run full validation on the proposal for offer/load/requirement diagnostics
    proposal_plan = PlanSnapshot(
        current_term=plan.current_term,
        target_term=target_term,
        items={i.course_code: PlanItemSnap(i.term, i.locked) for i in proposal},
        max_credits_per_term=plan.max_credits_per_term,
        max_difficulty_per_term=plan.max_difficulty_per_term,
    )
    vres = validate(curriculum, user_state, proposal_plan, requirements)
    seen_ids = {d.id for d in diagnostics}
    for d in vres.diagnostics:
        if d.id not in seen_ids:
            diagnostics.append(d)
            seen_ids.add(d.id)

    return AutoplanResult(
        feasible=feasible,
        proposal=proposal,
        diagnostics=tuple(diagnostics),
        critical_path=critical,
    )
