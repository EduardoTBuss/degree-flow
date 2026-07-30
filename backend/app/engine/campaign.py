"""Pure campaign-domain helpers (ADR-019). V4.1 shipped `eligibility_at_term`
(ADR-021) — the term-sensitive RN1 rule behind the "blocked" badge in the
Horários view; V4.3 adds `criticality_queue` (C1, spec v4 7.2) composed from
it plus the existing public engine functions. C3's special-enrollment ranking
will reuse both in later phases.

No I/O, no imports outside the engine (v4 principle 1); the existing engine
modules keep their signatures untouched.

RN1 at `term` — the same rule as ``validate._prereq_satisfied`` (F1-refined),
reimplemented here on purpose because that helper is private. The two
implementations are cross-checked by tests/engine/test_campaign.py so the
badge and the PREREQ_VIOLATION diagnostic can never contradict each other
(risk listed in ARCHITECTURE-v4 section 11). A prereq is satisfied at `term`
iff:
  (a) aprovada with completed_term < term (None = ok); or
  (b) cursando and term > plan.current_term; or
  (c) allocated in plan.items with item.term STRICTLY < term.
"""
from __future__ import annotations

from dataclasses import dataclass

from .critical_path import compute_critical_path
from .graph import heights, internal_prereqs, topo_sort
from .terms import term_gt, term_lt
from .types import (
    APROVADA,
    CURSANDO,
    FALTA,
    KIND_OBRIGATORIA,
    KIND_OPTATIVA,
    CurriculumSnapshot,
    PlanSnapshot,
    SectionSnap,
    UserState,
)


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    missing_prereqs: tuple[str, ...]  # unsatisfied prereq codes, sorted


def _prereq_satisfied_at(
    prereq: str,
    term: str,
    user_state: UserState,
    plan: PlanSnapshot,
) -> bool:
    st = user_state.status_of(prereq)
    if st == APROVADA:
        ct = user_state.completed_term_of(prereq)
        return ct is None or term_lt(ct, term)
    if st == CURSANDO:
        return term_gt(term, plan.current_term)
    item = plan.items.get(prereq)
    return item is not None and term_lt(item.term, term)


def eligibility_at_term(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    term: str,
) -> dict[str, Eligibility]:
    """Eligibility of every 'falta' course of the grade at `term` (spec v4 7.1).

    Universe: courses of the curriculum with effective status 'falta' and kind
    obrigatoria|optativa (atividades and fora_curriculo never appear). Prereqs
    outside the curriculum are ignored (same convention as validate/recommend).
    O(V+E); deterministic (missing_prereqs sorted by code)."""
    result: dict[str, Eligibility] = {}
    for code, info in curriculum.courses.items():
        if info.kind not in (KIND_OBRIGATORIA, KIND_OPTATIVA):
            continue
        if user_state.status_of(code) != FALTA:
            continue
        missing = tuple(
            sorted(
                p
                for p in info.prereqs
                if p in curriculum.courses
                and not _prereq_satisfied_at(p, term, user_state, plan)
            )
        )
        result[code] = Eligibility(eligible=not missing, missing_prereqs=missing)
    return result


# ===== C1 (spec v4 7.2): criticality queue ================================


@dataclass(frozen=True)
class QueueEntry:
    course_code: str
    critical: bool  # belongs to the critical path
    height: int  # longest dependent chain among remaining 'falta' courses
    unlocks: int  # height - 1 (courses it eventually unblocks)
    credits: int
    difficulty: int
    blocked: bool  # not eligible at the campaign term (D6: still requestable)
    missing_prereqs: tuple[str, ...]  # same source as eligibility_at_term
    section_ids: tuple[str, ...]  # offered sections of the term, sorted


def criticality_queue(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    term: str,
    sections_of_term: list[SectionSnap],
) -> list[QueueEntry]:
    """Priority queue "what should I fight for this term" (C1, spec v4 7.2).

    Universe: 'falta' courses of the grade (kind obrigatoria|optativa) with at
    least one section in `term` — no offer, nothing to request. Composes only
    public engine functions: `compute_critical_path`, `graph.topo_sort/heights/
    internal_prereqs` (heights over the subgraph of ALL remaining 'falta'
    courses, so unlocking value counts dependents even without offer this
    term) and `eligibility_at_term` (blocked + reasons — same source as A2).

    EXACT deterministic sort key (smaller = more critical):
      (blocked ? 1 : 0,   # D6: a blocked course never outranks a released one
       critical ? 0 : 1,  # critical path first
       -height,           # unlocks more prereq chain
       -credits,
       difficulty,
       course_code)       # total tiebreak
    O(V+E + n log n); pure, no I/O.
    """
    elig = eligibility_at_term(curriculum, user_state, plan, term)

    by_course: dict[str, list[str]] = {}
    for s in sections_of_term:
        if s.term == term and s.course_code in elig:
            by_course.setdefault(s.course_code, []).append(s.id)

    falta = set(elig)
    prereqs_of = {c: curriculum.courses[c].prereqs for c in falta}
    order, _cycle = topo_sort(falta, prereqs_of)
    height_of = heights(order, internal_prereqs(falta, prereqs_of))

    critical, _diags = compute_critical_path(curriculum, user_state, plan.current_term)
    cp_set = set(critical.course_codes)

    entries: list[QueueEntry] = []
    for code in sorted(by_course):
        e = elig[code]
        info = curriculum.courses[code]
        h = height_of.get(code, 1)
        entries.append(
            QueueEntry(
                course_code=code,
                critical=code in cp_set,
                height=h,
                unlocks=h - 1,
                credits=info.credits,
                difficulty=user_state.difficulty_of(code),
                blocked=not e.eligible,
                missing_prereqs=e.missing_prereqs,
                section_ids=tuple(sorted(by_course[code])),
            )
        )
    entries.sort(
        key=lambda q: (
            1 if q.blocked else 0,
            0 if q.critical else 1,
            -q.height,
            -q.credits,
            q.difficulty,
            q.course_code,
        )
    )
    return entries


# ===== C2: suggest_swaps (spec v4 7.4) =====================================
# Kept self-contained at the end of the file: V4.6 (C3) appends its own block
# to this module in parallel — nothing above this line is touched by C2.

from itertools import combinations  # noqa: E402  (C2 block-local import)

from .schedule import check_schedule  # noqa: E402  (public pair-conflict check)

# Same guard pattern as recommend._NODE_CAP (recommend.py:24): every (D, A)
# candidate evaluated counts as one node; on overflow we return the best
# suggestions found so far with truncated=True (same contract as recommend).
_SWAP_NODE_CAP = 50_000


def _course_value_parts(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    codes: set[str],
) -> dict[str, dict[str, int]]:
    """valor(cadeira) inputs — the `impact` of spec 7.3 WITHOUT section factors:
    +100 if on the critical path, +10 x (height - 1), +2 x credits.

    Height is computed over the subgraph of ALL remaining 'falta' courses of
    the grade (same recipe as criticality_queue), so unlocking value counts
    dependents even without offer this term. Courses outside the curriculum
    contribute credits=0, height=1 (defensive; router never sends them)."""
    falta = {
        code
        for code, info in curriculum.courses.items()
        if info.kind in (KIND_OBRIGATORIA, KIND_OPTATIVA)
        and user_state.status_of(code) == FALTA
    }
    prereqs_of = {c: curriculum.courses[c].prereqs for c in falta}
    order, _cycle = topo_sort(falta, prereqs_of)
    height_of = heights(order, internal_prereqs(falta, prereqs_of))
    critical, _diags = compute_critical_path(curriculum, user_state, plan.current_term)
    cp_set = set(critical.course_codes)

    parts: dict[str, dict[str, int]] = {}
    for code in codes:
        info = curriculum.courses.get(code)
        parts[code] = {
            "critical": 1 if code in cp_set else 0,
            "unlocks": height_of.get(code, 1) - 1,
            "credits": info.credits if info is not None else 0,
        }
    return parts


def suggest_swaps(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    term: str,
    held: list[SectionSnap],
    pool: list[SectionSnap],
    max_drops: int = 2,
    max_adds: int = 2,
    top_k: int = 5,
) -> dict:
    """Correction-round swap search (C2, spec v4 7.4). Pure, deterministic.

    Exact enumeration with pruning over D subset of `held` (|D| <= max_drops,
    including D = empty — "pure addition") x A subset of `pool` (1 <= |A| <=
    max_adds, distinct courses not in courses(held \\ D)).

    CENTRAL RULE (correction's two waves — removals processed first): an A is
    valid iff check_schedule((held \\ D) | A) has no conflict INVOLVING A —
    the slots freed by D count as free in the same suggestion; pre-existing
    conflicts among the kept sections never invalidate a candidate.

    delta = sum(valor(a)) - sum(valor(d)) over DISTINCT courses; only
    delta > 0 is kept (a swap must gain something). Ranking key:
    (-delta, |D|, sorted section-id signature) — fewer removals win ties;
    dedupe by signature keeps the best delta. Composes only public engine
    functions (check_schedule, compute_critical_path, graph helpers).

    Returns {"suggestions": [{"drop": (SectionSnap,...), "add": (...),
    "score_delta": float, "score_breakdown": {...}}], "truncated": bool}.
    Worst case ~C(8,<=2) x C(30,<=2) ~ 17k candidates; `_SWAP_NODE_CAP`
    guards the theoretical blow-up (truncated=True with best-so-far)."""
    held_by_id = {s.id: s for s in held if s.term == term}
    held_list = [held_by_id[i] for i in sorted(held_by_id)]
    pool_by_id = {
        s.id: s for s in pool if s.term == term and s.id not in held_by_id
    }
    pool_list = [pool_by_id[i] for i in sorted(pool_by_id)]

    codes = {s.course_code for s in held_list} | {s.course_code for s in pool_list}
    parts = _course_value_parts(curriculum, user_state, plan, codes)

    def value(code: str) -> float:
        p = parts[code]
        return 100.0 * p["critical"] + 10.0 * p["unlocks"] + 2.0 * p["credits"]

    # One public check_schedule call yields every conflicting pair up front.
    conflict_pairs = {
        frozenset((d.details["section_a"], d.details["section_b"]))
        for d in check_schedule(held_list + pool_list)
    }

    def clash(a_id: str, b_id: str) -> bool:
        return frozenset((a_id, b_id)) in conflict_pairs

    drop_subsets: list[tuple[SectionSnap, ...]] = [()]
    for k in range(1, min(max_drops, len(held_list)) + 1):
        drop_subsets.extend(combinations(held_list, k))
    add_subsets: list[tuple[SectionSnap, ...]] = []
    for k in range(1, min(max_adds, len(pool_list)) + 1):
        for a in combinations(pool_list, k):
            if len({s.course_code for s in a}) == len(a):  # distinct courses
                add_subsets.append(a)

    best: dict[tuple[str, ...], dict] = {}
    nodes = 0
    truncated = False
    for d_subset in drop_subsets:
        d_ids = {s.id for s in d_subset}
        base = [s for s in held_list if s.id not in d_ids]
        base_codes = {s.course_code for s in base}
        d_codes = sorted({s.course_code for s in d_subset})
        drop_value = sum(value(c) for c in d_codes)
        for a_subset in add_subsets:
            nodes += 1
            if nodes > _SWAP_NODE_CAP:
                truncated = True
                break
            a_codes = sorted(s.course_code for s in a_subset)
            if set(a_codes) & base_codes:
                continue  # add must target courses outside (held \ D)
            # two waves: conflicts must involve A; base-only conflicts ignored
            if any(clash(a.id, b.id) for a in a_subset for b in base):
                continue
            if any(clash(x.id, y.id) for x, y in combinations(a_subset, 2)):
                continue
            delta = sum(value(c) for c in a_codes) - drop_value
            if delta <= 0:
                continue
            signature = tuple(sorted(d_ids | {s.id for s in a_subset}))
            entry = {
                "drop": tuple(sorted(d_subset, key=lambda s: s.id)),
                "add": tuple(sorted(a_subset, key=lambda s: s.id)),
                "score_delta": round(delta, 2),
                "score_breakdown": {
                    "gained_critical": sum(parts[c]["critical"] for c in a_codes),
                    "gained_unlocks": sum(parts[c]["unlocks"] for c in a_codes),
                    "gained_credits": sum(parts[c]["credits"] for c in a_codes),
                    "lost_critical": sum(parts[c]["critical"] for c in d_codes),
                    "lost_unlocks": sum(parts[c]["unlocks"] for c in d_codes),
                    "lost_credits": sum(parts[c]["credits"] for c in d_codes),
                },
                "_signature": signature,
                "_n_drops": len(d_subset),
            }
            cur = best.get(signature)
            if cur is None or entry["score_delta"] > cur["score_delta"]:
                best[signature] = entry
        if truncated:
            break

    ranked = sorted(
        best.values(),
        key=lambda e: (-e["score_delta"], e["_n_drops"], e["_signature"]),
    )[: max(0, top_k)]
    for e in ranked:
        e.pop("_signature", None)
        e.pop("_n_drops", None)
    return {"suggestions": ranked, "truncated": truncated}


# ===== C3: rank_special_candidates (spec v4 7.3) ===========================
# Block kept self-contained at the end of the file — nothing above this line
# is touched by C3. The weights below are the SINGLE place of the C3 ranking
# contract (risk table, spec section 11): recalibrating = editing these
# constants + the golden tests, nowhere else. It reuses `_course_value_parts`
# (C2 block) on purpose: spec 7.4 defines valor(cadeira) as "the impact of
# 7.3 without section factors" — one implementation keeps C2 and C3 forever
# consistent with each other.

_FIT_CLEAN = 20.0  # zero conflicts with the accepted (fixed) sections
_FIT_CONFLICT = -60.0  # PER accepted section in conflict (check_schedule pair)
_VACANCY_OPEN = 10.0  # capacity - enrolled > 0
_VACANCY_FULL = -25.0  # enrolled >= capacity (full: penalized, NEVER excluded — D5)
_IMPACT_CRITICAL = 100.0  # course on the critical path
_IMPACT_PER_UNLOCK = 10.0  # x (height - 1)
_IMPACT_PER_CREDIT = 2.0
_BLOCKED_PENALTY = -50.0  # prereq missing at the term (still requestable — D6)
_FOREIGN_PENALTY = -10.0  # best section offered to another course (ADR-020)


@dataclass(frozen=True)
class Candidate:
    course_code: str
    section_id: str  # best section of the course (max fit+vacancy, tie by id)
    score: float
    score_breakdown: dict[str, float]  # keys of spec 6.7, weighted values
    blocked: bool  # not eligible at `term` (D6: never rank 1 with alternative)
    missing_prereqs: tuple[str, ...]  # same source as eligibility_at_term
    full: bool  # best section has enrolled >= capacity (D5: flag, not filter)
    conflicts_with: tuple[str, ...]  # accepted section ids clashing with best
    alternatives: tuple[str, ...]  # other sections of the same course, sorted


def rank_special_candidates(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    term: str,
    sections_of_term: list[SectionSnap],
    accepted: list[SectionSnap],
    offered_to: dict[str, list[str] | None],
    k: int = 5,
) -> list[Candidate]:
    """Special-enrollment top-k advisor (C3, spec v4 7.3). Pure, deterministic.

    Universe: 'falta' courses OF THE GRADE with >= 1 section in `term` (D3 —
    discovered electives have no curriculum entry so they are out by
    construction; a section of the user's own course is a legitimate
    candidate — D2). Conflict detection composes the public `check_schedule`
    per (candidate, accepted) pair; identical section ids are never compared
    against themselves.

    EXACT score (weights are the module constants above):
      per section:  fit     = +20 clean | -60 PER accepted section in conflict
                    vacancy = +10 open | -25 full (never excluded — D5)
                              | 0 when capacity/enrolled is None (neutral)
      per course:   impact  = +100 critical + 10 x (height-1) + 2 x credits
                    blocked = -50 when eligibility_at_term says blocked (D6)
                    foreign = -10 when the BEST section has offered_to != None
      best section = max (fit + vacancy), tie broken by smallest section_id;
      score = impact + blocked + foreign + best's (fit + vacancy).

    Ranking: sort by (-score, course_code), take top-k; POST-RULE (D6): if
    rank 1 is blocked and a non-blocked candidate exists in the top-k, the
    FIRST non-blocked one is promoted to rank 1 (others keep relative order).
    O(n x |accepted| x slots + n log n); no I/O."""
    elig = eligibility_at_term(curriculum, user_state, plan, term)

    by_course: dict[str, list[SectionSnap]] = {}
    for s in sections_of_term:
        if s.term == term and s.course_code in elig:
            by_course.setdefault(s.course_code, []).append(s)

    acc = sorted((a for a in accepted if a.term == term), key=lambda s: s.id)
    parts = _course_value_parts(curriculum, user_state, plan, set(by_course))

    def section_factors(sec: SectionSnap) -> tuple[float, float, bool, tuple[str, ...]]:
        """(fit, vacancy, full, conflicts_with) of one candidate section."""
        conflicts = tuple(
            a.id for a in acc if a.id != sec.id and check_schedule([sec, a])
        )
        fit = _FIT_CLEAN if not conflicts else _FIT_CONFLICT * len(conflicts)
        if sec.capacity is None or sec.enrolled is None:
            return fit, 0.0, False, conflicts  # level-2 data: neutral (D5)
        full = sec.enrolled >= sec.capacity
        vacancy = _VACANCY_FULL if full else (
            _VACANCY_OPEN if sec.capacity - sec.enrolled > 0 else 0.0
        )
        return fit, vacancy, full, conflicts

    candidates: list[Candidate] = []
    for code in sorted(by_course):
        secs = sorted(by_course[code], key=lambda s: s.id)
        factors = {s.id: section_factors(s) for s in secs}
        # best section: max (fit + vacancy), tie by smallest section_id
        best = min(secs, key=lambda s: (-(factors[s.id][0] + factors[s.id][1]), s.id))
        fit, vacancy, full, conflicts = factors[best.id]

        p = parts[code]
        e = elig[code]
        breakdown = {
            "critical": _IMPACT_CRITICAL * p["critical"],
            "unlocks": _IMPACT_PER_UNLOCK * p["unlocks"],
            "credits": _IMPACT_PER_CREDIT * p["credits"],
            "fit": fit,
            "vacancy": _VACANCY_OPEN if vacancy > 0 else 0.0,
            "full": _VACANCY_FULL if vacancy < 0 else 0.0,
            "blocked": _BLOCKED_PENALTY if not e.eligible else 0.0,
            "foreign": _FOREIGN_PENALTY if offered_to.get(best.id) is not None else 0.0,
        }
        candidates.append(
            Candidate(
                course_code=code,
                section_id=best.id,
                score=round(sum(breakdown.values()), 2),
                score_breakdown=breakdown,
                blocked=not e.eligible,
                missing_prereqs=e.missing_prereqs,
                full=full,
                conflicts_with=conflicts,
                alternatives=tuple(s.id for s in secs if s.id != best.id),
            )
        )

    candidates.sort(key=lambda c: (-c.score, c.course_code))
    top = candidates[: max(0, k)]
    # POST-RULE (D6): a blocked candidate never sits at rank 1 when a
    # non-blocked alternative made the top-k; the rest keep relative order.
    if top and top[0].blocked:
        idx = next((i for i, c in enumerate(top) if not c.blocked), None)
        if idx is not None:
            top.insert(0, top.pop(idx))
    return top
