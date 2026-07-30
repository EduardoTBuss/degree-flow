"""Pinned-course recommendation (F2a, ADR-029/030). Pure, deterministic, no I/O.

The user pins COURSES ("cadeiras que com certeza quero fazer"); the engine
picks the section ("o restante vai se moldando na volta"). This module
composes the untouched public `recommend_schedule` — one call per cartesian
combo of the pinned courses' sections — and merges the results
deterministically, following the campaign.py precedent (v4): new capability
as a new pure module, existing signatures intact.

D5 semantics (ADR-030): a pin never fails silently and is never released
silently. No offer this term -> status "sem_oferta" and the recommendation
proceeds without it. Unsolvable conflict / limit overflow -> empty
`recommendations`, `pinned_infeasible=True` and a per-course reason; releasing
the pin is an explicit user action (frontend button, F2b). Blocked by prereq
-> pinned anyway with a badge (authorized break, D6/v4).
"""
from __future__ import annotations

from itertools import product

from .campaign import eligibility_at_term
from .recommend import recommend_schedule
from .schedule import check_schedule
from .types import (
    CurriculumSnapshot,
    PlanSnapshot,
    SectionSnap,
    UserState,
)

_PIN_COMBO_CAP = 64  # max cartesian combos enumerated (ADR-029) -> truncated

# pinned[i].status vocabulary (spec v5 section 4.6)
STATUS_OK = "ok"
STATUS_SEM_OFERTA = "sem_oferta"
STATUS_BLOQUEADA = "bloqueada"
STATUS_TURMA_JA_FIXADA = "turma_ja_fixada"
STATUS_CONFLITO = "conflito"
STATUS_ESTOURA_LIMITE = "estoura_limite"


def _credits_of(curriculum: CurriculumSnapshot, code: str) -> int:
    info = curriculum.courses.get(code)
    return info.credits if info is not None else 0


def recommend_with_pinned_courses(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    plan: PlanSnapshot,
    term: str,
    sections_of_term: list[SectionSnap],
    locked_choices: list[str],
    pinned_courses: list[str],
    limits: dict,
    top_n: int = 3,
    combo_cap: int = _PIN_COMBO_CAP,
) -> dict:
    """`recommend_schedule` shape + {"pinned": [...], "pinned_infeasible": bool}.

    Steps (ADR-029): (1) classify each pinned course; (2) cartesian product of
    the remaining pinned courses' sections (lexicographic by section_id),
    pruning combos with internal conflicts, conflicts against locked sections
    (public `check_schedule`) or limit overflow; (3) one `recommend_schedule`
    call per surviving combo with `locked_choices = locked + combo`;
    (4) discard solutions missing any pinned section (guard against a silent
    engine drop — mandatory per ADR-029); (5) deterministic merge: dedupe by
    signature, sort by (-score, signature), top_n, renumbered ranks;
    (6) nothing survived -> `pinned_infeasible=True` with a per-course reason.
    """
    max_credits = limits.get("max_credits") or plan.max_credits_per_term
    max_diff = limits.get("max_difficulty")

    by_course: dict[str, list[SectionSnap]] = {}
    for s in sections_of_term:
        if s.term == term:
            by_course.setdefault(s.course_code, []).append(s)
    for lst in by_course.values():
        lst.sort(key=lambda s: s.id)

    locked_ids = sorted(set(locked_choices))
    locked_secs = sorted(
        (s for s in sections_of_term if s.id in set(locked_ids)), key=lambda s: s.id
    )
    # first locked section of each course (by id) — used for "turma_ja_fixada"
    locked_sec_of_course: dict[str, SectionSnap] = {}
    for s in locked_secs:
        locked_sec_of_course.setdefault(s.course_code, s)

    pinned = sorted(set(pinned_courses))
    elig = eligibility_at_term(curriculum, user_state, plan, term)

    # ----- step 1: classify -------------------------------------------------
    pin_state: dict[str, dict] = {}
    product_courses: list[str] = []  # pins that enter the cartesian product
    for code in pinned:
        alt_ids = [s.id for s in by_course.get(code, [])]
        if code in locked_sec_of_course:
            sec = locked_sec_of_course[code]
            pin_state[code] = {
                "status": STATUS_TURMA_JA_FIXADA,
                "section_id": sec.id,
                "alternatives": [i for i in alt_ids if i != sec.id],
                "reason": None,
            }
            continue
        if not alt_ids:
            info = curriculum.courses.get(code)
            name = info.name if info is not None else code
            pin_state[code] = {
                "status": STATUS_SEM_OFERTA,
                "section_id": None,
                "alternatives": [],
                "reason": (
                    f"nenhuma turma de {name} cadastrada em {term} — "
                    "a recomendação seguiu sem esta fixação"
                ),
            }
            continue
        e = elig.get(code)
        blocked = e is not None and not e.eligible
        pin_state[code] = {
            "status": STATUS_BLOQUEADA if blocked else STATUS_OK,
            "section_id": None,
            "alternatives": alt_ids,
            "reason": (
                "pré-requisitos pendentes em "
                f"{term}: {', '.join(e.missing_prereqs)} — fixada mesmo assim"
                if blocked
                else None
            ),
        }
        product_courses.append(code)

    # ----- step 2: cartesian product + pruning ------------------------------
    conflict_reason: dict[str, str] = {}
    limit_reason: dict[str, str] = {}
    prune_diags: list[dict] = []
    truncated = False
    valid_combos: list[tuple[SectionSnap, ...]] = []
    if product_courses:
        base_credits = sum(_credits_of(curriculum, s.course_code) for s in locked_secs)
        base_diff = sum(user_state.difficulty_of(s.course_code) for s in locked_secs)
        enumerated = 0
        for combo in product(*(by_course[c] for c in product_courses)):
            if enumerated >= combo_cap:
                truncated = True
                break
            enumerated += 1
            combo_ids = {s.id for s in combo}
            clashes = [
                d
                for d in check_schedule(list(combo) + locked_secs)
                if d.details["section_a"] in combo_ids
                or d.details["section_b"] in combo_ids
            ]
            if clashes:
                for d in clashes:
                    for side in ("section_a", "section_b"):
                        sid = d.details[side]
                        if sid in combo_ids:
                            code = next(s.course_code for s in combo if s.id == sid)
                            conflict_reason.setdefault(code, d.message)
                    prune_diags.append(d.to_dict())
                continue
            credits = base_credits + sum(
                _credits_of(curriculum, s.course_code) for s in combo
            )
            if credits > max_credits:
                for s in combo:
                    limit_reason.setdefault(
                        s.course_code,
                        f"estoura max_credits={max_credits} junto com as demais "
                        "fixações/travas",
                    )
                continue
            diff = base_diff + sum(
                user_state.difficulty_of(s.course_code) for s in combo
            )
            if max_diff is not None and diff > max_diff:
                for s in combo:
                    limit_reason.setdefault(
                        s.course_code,
                        f"estoura max_difficulty={max_diff} junto com as demais "
                        "fixações/travas",
                    )
                continue
            valid_combos.append(combo)
    else:
        valid_combos = [()]  # no pin survives classification: plain run

    # ----- steps 3-5: run per combo, filter, merge --------------------------
    ja_fixada_ids = {
        locked_sec_of_course[c].id for c in pinned if c in locked_sec_of_course
    }
    merged: dict[tuple[str, ...], dict] = {}
    diag_by_id: dict[str, dict] = {}
    eligible_courses: list[str] | None = None
    for combo in valid_combos:
        res = recommend_schedule(
            curriculum,
            user_state,
            plan,
            term,
            sections_of_term,
            locked_ids + sorted(s.id for s in combo),
            limits,
            top_n,
        )
        truncated = truncated or res["truncated"]
        if eligible_courses is None:
            eligible_courses = res["eligible_courses"]
        for d in res["diagnostics"]:
            diag_by_id.setdefault(d["id"], d)
        required = {s.id for s in combo} | ja_fixada_ids
        for rec in res["recommendations"]:
            ids = {c["section_id"] for c in rec["choices"]}
            if not required <= ids:
                continue  # D5 guard: a dropped pin never reaches the user
            sig = tuple(sorted(ids))
            cur = merged.get(sig)
            if cur is None or rec["score"] > cur["score"]:
                kept = dict(rec)
                kept["_signature"] = sig
                merged[sig] = kept
    ranked = sorted(merged.values(), key=lambda r: (-r["score"], r["_signature"]))
    ranked = ranked[: max(1, top_n)]
    for i, rec in enumerate(ranked, 1):
        rec.pop("_signature", None)
        rec["rank"] = i

    # ----- step 6: infeasible => never silent --------------------------------
    infeasible = bool(product_courses) and not ranked
    if infeasible:
        for code in product_courses:
            st = pin_state[code]
            if code in conflict_reason:
                st["status"] = STATUS_CONFLITO
                st["reason"] = conflict_reason[code]
            elif code in limit_reason:
                st["status"] = STATUS_ESTOURA_LIMITE
                st["reason"] = limit_reason[code]
            else:
                st["status"] = STATUS_CONFLITO
                st["reason"] = "nenhuma combinação viável com as demais fixações/travas"
        # base engine call keeps the response shape honest (eligible courses +
        # locked-conflict diagnostics) without inventing recommendations.
        base = recommend_schedule(
            curriculum, user_state, plan, term, sections_of_term,
            locked_ids, limits, top_n,
        )
        eligible_courses = base["eligible_courses"]
        for d in base["diagnostics"]:
            diag_by_id.setdefault(d["id"], d)
        for d in prune_diags:
            diag_by_id.setdefault(d["id"], d)
    elif ranked:
        top_by_course = {c["course_code"]: c["section_id"] for c in ranked[0]["choices"]}
        for code in product_courses:
            sid = top_by_course.get(code)
            pin_state[code]["section_id"] = sid
            pin_state[code]["alternatives"] = [
                i for i in pin_state[code]["alternatives"] if i != sid
            ]

    return {
        "term": term,
        "recommendations": ranked,
        "eligible_courses": eligible_courses or [],
        "diagnostics": [diag_by_id[k] for k in sorted(diag_by_id)],
        "truncated": truncated,
        "pinned": [
            {
                "course_code": code,
                "status": pin_state[code]["status"],
                "section_id": pin_state[code]["section_id"],
                "alternatives": pin_state[code]["alternatives"],
                "reason": pin_state[code]["reason"],
            }
            for code in pinned
        ],
        "pinned_infeasible": infeasible,
    }
