"""Schedule recommender (F3c). Pure, deterministic.

v1 algorithm: exhaustive DFS with a node cap over eligible courses × their
sections in the target term, keeping the top-N conflict-free combinations by
score (critical path >> unlocked-next >> credits, difficulty penalty, full
sections penalty). Contract is stable; branch-and-bound is a future refinement.
"""
from __future__ import annotations

from .critical_path import compute_critical_path
from .graph import heights, internal_prereqs, topo_sort
from .schedule import _overlaps
from .terms import term_gt, term_lt
from .types import (
    APROVADA,
    CURSANDO,
    FALTA,
    KIND_OPTATIVA,
    CurriculumSnapshot,
    PlanSnapshot,
    SectionSnap,
    UserState,
)

_NODE_CAP = 200_000  # safety guard against combinatorial blow-up


def _prereqs_done_before(code, curriculum, user_state, term) -> bool:
    for p in curriculum.courses[code].prereqs:
        if p not in curriculum.courses:
            continue
        st = user_state.status_of(p)
        if st == APROVADA:
            ct = user_state.completed_term_of(p)
            if ct is None or term_lt(ct, term):
                continue
            return False
        if st == CURSANDO and term_gt(term, "0000/1"):  # cursando concludes before any future term
            # only counts if term is strictly after current_term; recommender terms are future
            continue
        return False
    return True


def _sections_conflict(a: SectionSnap, b: SectionSnap) -> bool:
    return bool(_overlaps(a, b))


def recommend_schedule(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    term: str,
    sections_of_term: list[SectionSnap],
    locked_choices: list[str],
    limits: dict,
    top_n: int = 3,
) -> dict:
    max_credits = limits.get("max_credits") or plan.max_credits_per_term
    max_diff = limits.get("max_difficulty")
    # item 1: don't recommend more electives than the optativa category still
    # needs. `remaining_elective_hours` (hours still missing, counting only
    # DONE electives) is passed by the service; None/absent => no cap (exact
    # prior behavior, suite stays green).
    remaining_elec = limits.get("remaining_elective_hours")

    by_course: dict[str, list[SectionSnap]] = {}
    for s in sections_of_term:
        by_course.setdefault(s.course_code, []).append(s)
    for lst in by_course.values():
        lst.sort(key=lambda s: s.id)

    # eligible: falta, prereqs done before the term, has >=1 section this term
    eligible = [
        c
        for c in sorted(by_course)
        if c in curriculum.courses
        and user_state.status_of(c) == FALTA
        and _prereqs_done_before(c, curriculum, user_state, term)
    ]

    # priority order (same spirit as autoplan): critical path, height, credits, difficulty
    critical, _ = compute_critical_path(curriculum, user_state, plan.current_term)
    cp_set = set(critical.course_codes)
    prereqs_of = {c: curriculum.courses[c].prereqs for c in eligible}
    order, _cycle = topo_sort(eligible, prereqs_of)
    height = heights(order, internal_prereqs(eligible, prereqs_of))
    eligible.sort(
        key=lambda c: (
            0 if c in cp_set else 1,
            -height.get(c, 1),
            -curriculum.courses[c].credits,
            user_state.difficulty_of(c),
            c,
        )
    )

    locked_secs = [s for s in sections_of_term if s.id in set(locked_choices)]
    diagnostics: list[dict] = []
    # detect conflicts among locked choices themselves
    for i in range(len(locked_secs)):
        for j in range(i + 1, len(locked_secs)):
            if _sections_conflict(locked_secs[i], locked_secs[j]):
                diagnostics.append(
                    {
                        "id": f"SCHEDULE_CONFLICT:{locked_secs[i].id}:{locked_secs[j].id}",
                        "type": "SCHEDULE_CONFLICT",
                        "severity": "warning",
                        "course_codes": [locked_secs[i].course_code, locked_secs[j].course_code],
                        "related_codes": [],
                        "term": term,
                        "message": "As turmas fixadas conflitam entre si — remova uma trava.",
                        "details": {"section_a": locked_secs[i].id, "section_b": locked_secs[j].id},
                    }
                )

    locked_by_course = {s.course_code: s for s in locked_secs}

    solutions: list[dict] = []
    nodes = [0]
    truncated = [False]

    def fits(chosen: list[SectionSnap], sec: SectionSnap) -> bool:
        for c in chosen:
            if _sections_conflict(c, sec):
                return False
        return True

    def score(chosen: list[SectionSnap]) -> tuple[float, dict]:
        codes = [s.course_code for s in chosen]
        credits = sum(curriculum.courses[c].credits for c in codes)
        diff = sum(user_state.difficulty_of(c) for c in codes)
        cp_count = sum(1 for c in codes if c in cp_set)
        unlocks = sum(height.get(c, 1) - 1 for c in codes)
        full = sum(1 for s in chosen if s.has_vacancy() is False)
        val = cp_count * 100 + unlocks * 10 + credits * 2 - diff * 1.5 - full * 5
        return val, {
            "critical_path_courses": cp_count,
            "credits": credits,
            "difficulty_sum": diff,
            "unlocks_next_term": unlocks,
            "full_sections": full,
        }

    def dfs(idx: int, chosen: list[SectionSnap], credits: int, diff: int):
        if nodes[0] > _NODE_CAP:
            truncated[0] = True
            return
        nodes[0] += 1
        if idx == len(eligible):
            if chosen:
                sc, breakdown = score(chosen)
                chosen_codes = {s.course_code for s in chosen}
                left = [
                    {"course_code": c, "reason": "não coube sem conflito/limite"}
                    for c in eligible
                    if c not in chosen_codes
                ]
                solutions.append(
                    {
                        "score": round(sc, 2),
                        "score_breakdown": breakdown,
                        "choices": [
                            {"course_code": s.course_code, "section_id": s.id} for s in chosen
                        ],
                        "left_out": left,
                        "signature": tuple(sorted(s.id for s in chosen)),
                    }
                )
            return
        course = eligible[idx]
        # forced locked choice
        if course in locked_by_course:
            sec = locked_by_course[course]
            if sec in chosen:
                # Already forced in at the DFS root. Bug fix (spec v5 section 8,
                # "dupla contagem"): a timeslot-less section passes `fits`
                # against itself and used to be re-added here, double-counting
                # its credits and duplicating the choice.
                dfs(idx + 1, chosen, credits, diff)
                return
            info = curriculum.courses[course]
            if fits(chosen, sec) and credits + info.credits <= max_credits and (
                max_diff is None or diff + user_state.difficulty_of(course) <= max_diff
            ):
                dfs(idx + 1, chosen + [sec], credits + info.credits, diff + user_state.difficulty_of(course))
            else:
                dfs(idx + 1, chosen, credits, diff)  # locked can't fit here; still explore rest
            return
        info = curriculum.courses[course]
        # item 1: cap electives at the remaining optativa hours — once the
        # electives already chosen cover what's missing, stop adding more (so a
        # single-optativa gap never yields a 2-optativa recommendation).
        if remaining_elec is not None and info.kind == KIND_OPTATIVA:
            chosen_elec_hours = sum(
                curriculum.courses[s.course_code].hours
                for s in chosen
                if curriculum.courses[s.course_code].kind == KIND_OPTATIVA
            )
            if chosen_elec_hours >= remaining_elec:
                dfs(idx + 1, chosen, credits, diff)  # enough electives already
                return
        # try each section of this course
        for sec in by_course[course]:
            if not fits(chosen, sec):
                continue
            if credits + info.credits > max_credits:
                continue
            if max_diff is not None and diff + user_state.difficulty_of(course) > max_diff:
                continue
            dfs(idx + 1, chosen + [sec], credits + info.credits, diff + user_state.difficulty_of(course))
        # or skip this course
        dfs(idx + 1, chosen, credits, diff)

    dfs(0, list(locked_secs), sum(curriculum.courses[s.course_code].credits for s in locked_secs),
        sum(user_state.difficulty_of(s.course_code) for s in locked_secs))

    # dedupe by signature, keep best score, deterministic tiebreak
    best: dict[tuple, dict] = {}
    for sol in solutions:
        sig = sol["signature"]
        if sig not in best or sol["score"] > best[sig]["score"]:
            best[sig] = sol
    ranked = sorted(best.values(), key=lambda s: (-s["score"], s["signature"]))[: max(1, top_n)]
    for i, sol in enumerate(ranked, 1):
        sol.pop("signature", None)
        sol["rank"] = i
        sol["diagnostics"] = []

    return {
        "term": term,
        "recommendations": ranked,
        "eligible_courses": eligible,
        "diagnostics": diagnostics,
        "truncated": truncated[0],
    }
