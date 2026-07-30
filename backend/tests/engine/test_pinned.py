"""Golden tests for engine/pinned.py (F2a, ADR-029/030). Synthetic snapshots.

Covers the spec v5 section 7 matrix: global-best section choice, unsolvable
conflicts (never released silently), sem_oferta, blocked-with-badge,
pinned + locked_choices combined, determinism, combo_cap truncation, the
"pin appears in EVERY returned solution" guard, limit overflow and the
timeslot-less double-count regression (spec section 8).
"""
from __future__ import annotations

from app.engine.pinned import recommend_with_pinned_courses
from app.engine.recommend import recommend_schedule
from app.engine.types import SectionSnap, TimeslotSnap

from .helpers import course, curriculum, plan, state


def _sec(sid, code, slots, cap=40, enr=0):
    return SectionSnap(
        id=sid, course_code=code, term="2026/2",
        timeslots=tuple(TimeslotSnap(w, s, e) for w, s, e in slots),
        label=sid.split(":")[-1], capacity=cap, enrolled=enr,
    )


def _run(cur, secs, pinned, locked=(), limits=None, top_n=3, st=None, **kw):
    return recommend_with_pinned_courses(
        cur, st or state(), plan(current="2026/1"), "2026/2", secs,
        list(locked), list(pinned), limits or {}, top_n, **kw
    )


def _pin(res, code):
    return next(p for p in res["pinned"] if p["course_code"] == code)


def _codes(rec):
    return {c["course_code"] for c in rec["choices"]}


def _ids(rec):
    return {c["section_id"] for c in rec["choices"]}


# ----- section choice: global best, not smallest id ------------------------


def test_picks_section_of_best_global_combination_not_smallest_id():
    cur = curriculum(course("A"), course("B"), course("P"))
    secs = [
        _sec("s:P:M1", "P", [(1, 480, 600)]),  # smallest id, clashes with A
        _sec("s:P:M2", "P", [(2, 480, 600)]),  # frees A -> better global combo
        _sec("s:A:T1", "A", [(1, 480, 600)]),
        _sec("s:B:T1", "B", [(3, 480, 600)]),
    ]
    res = _run(cur, secs, ["P"])
    assert res["pinned_infeasible"] is False
    top = res["recommendations"][0]
    assert "s:P:M2" in _ids(top) and _codes(top) == {"A", "B", "P"}
    p = _pin(res, "P")
    assert p["status"] == "ok"
    assert p["section_id"] == "s:P:M2"
    assert p["alternatives"] == ["s:P:M1"]
    assert p["reason"] is None


# ----- infeasible: never released, never silent -----------------------------


def test_unsolvable_conflict_yields_empty_with_reasons_never_drops():
    cur = curriculum(course("P1"), course("P2"), course("A"))
    secs = [
        _sec("s:P1:M1", "P1", [(1, 480, 600)]),
        _sec("s:P2:M1", "P2", [(1, 540, 660)]),  # overlaps P1's only section
        _sec("s:A:T1", "A", [(3, 480, 600)]),
    ]
    res = _run(cur, secs, ["P1", "P2"])
    assert res["recommendations"] == []  # never a solution without a pin
    assert res["pinned_infeasible"] is True
    for code in ("P1", "P2"):
        p = _pin(res, code)
        assert p["status"] == "conflito"
        assert p["reason"] and "Conflito de horário" in p["reason"]
    # honest envelope: eligible list and engine-shaped conflict diagnostics
    assert "A" in res["eligible_courses"]
    assert any(d["type"] == "SCHEDULE_CONFLICT" for d in res["diagnostics"])


def test_limit_overflow_yields_estoura_limite():
    cur = curriculum(course("P1", credits=20), course("P2", credits=20))
    secs = [
        _sec("s:P1:M1", "P1", [(1, 480, 600)]),
        _sec("s:P2:M1", "P2", [(2, 480, 600)]),  # no time conflict
    ]
    res = _run(cur, secs, ["P1", "P2"], limits={"max_credits": 30})
    assert res["recommendations"] == []
    assert res["pinned_infeasible"] is True
    for code in ("P1", "P2"):
        p = _pin(res, code)
        assert p["status"] == "estoura_limite"
        assert "max_credits=30" in p["reason"]


# ----- sem_oferta: recommendation proceeds without the pin ------------------


def test_pin_without_offer_is_flagged_and_recommendation_proceeds():
    cur = curriculum(course("P", name="FIA"), course("A"))
    secs = [_sec("s:A:T1", "A", [(1, 480, 600)])]
    res = _run(cur, secs, ["P"])
    assert res["pinned_infeasible"] is False
    assert res["recommendations"] and _codes(res["recommendations"][0]) == {"A"}
    p = _pin(res, "P")
    assert p["status"] == "sem_oferta"
    assert p["section_id"] is None and p["alternatives"] == []
    assert "FIA" in p["reason"] and "2026/2" in p["reason"]


# ----- blocked by prereq: pinned anyway, with a badge ------------------------


def test_blocked_pin_is_kept_with_badge():
    cur = curriculum(course("X"), course("P", prereqs=("X",)), course("A"))
    secs = [
        _sec("s:P:M1", "P", [(1, 480, 600)]),
        _sec("s:A:T1", "A", [(2, 480, 600)]),
    ]
    res = _run(cur, secs, ["P"])  # X is falta -> P blocked at 2026/2
    assert res["pinned_infeasible"] is False
    top = res["recommendations"][0]
    assert "s:P:M1" in _ids(top)  # kept in the schedule (D6 authorized break)
    p = _pin(res, "P")
    assert p["status"] == "bloqueada"
    assert p["section_id"] == "s:P:M1"
    assert "X" in p["reason"]


# ----- pinned + locked_choices combined -------------------------------------


def test_pinned_combines_with_locked_choices():
    cur = curriculum(course("A"), course("P"))
    secs = [
        _sec("s:A:T1", "A", [(1, 480, 600)]),
        _sec("s:P:M1", "P", [(1, 480, 600)]),  # clashes with the locked A:T1
        _sec("s:P:M2", "P", [(2, 480, 600)]),
    ]
    res = _run(cur, secs, ["P"], locked=["s:A:T1"])
    assert res["pinned_infeasible"] is False
    top = res["recommendations"][0]
    assert {"s:A:T1", "s:P:M2"} <= _ids(top)
    assert _pin(res, "P")["section_id"] == "s:P:M2"


def test_pin_of_course_already_locked_is_turma_ja_fixada():
    cur = curriculum(course("A"))
    secs = [
        _sec("s:A:T1", "A", [(1, 480, 600)]),
        _sec("s:A:T2", "A", [(2, 480, 600)]),
    ]
    res = _run(cur, secs, ["A"], locked=["s:A:T1"])
    p = _pin(res, "A")
    assert p["status"] == "turma_ja_fixada"
    assert p["section_id"] == "s:A:T1"
    assert p["alternatives"] == ["s:A:T2"]
    assert res["recommendations"] and "s:A:T1" in _ids(res["recommendations"][0])


def test_pin_conflicting_with_locked_choice_is_infeasible_with_reason():
    cur = curriculum(course("A"), course("P"))
    secs = [
        _sec("s:A:T1", "A", [(1, 480, 600)]),
        _sec("s:P:M1", "P", [(1, 540, 660)]),  # only section clashes with lock
    ]
    res = _run(cur, secs, ["P"], locked=["s:A:T1"])
    assert res["recommendations"] == [] and res["pinned_infeasible"] is True
    p = _pin(res, "P")
    assert p["status"] == "conflito" and "s:A:T1".split(":")[-1] in p["reason"]


# ----- guard: the pin appears in EVERY returned solution ---------------------


def test_pin_present_in_every_returned_solution():
    cur = curriculum(course("P"), course("A"), course("B"), course("C"))
    secs = [
        _sec("s:P:M1", "P", [(1, 480, 600)]),
        _sec("s:P:M2", "P", [(2, 480, 600)]),
        _sec("s:A:T1", "A", [(3, 480, 600)]),
        _sec("s:B:T1", "B", [(4, 480, 600)]),
        _sec("s:C:T1", "C", [(1, 480, 600)]),  # clashes with P:M1
    ]
    res = _run(cur, secs, ["P"], top_n=10)
    assert res["recommendations"]
    for rec in res["recommendations"]:
        assert "P" in _codes(rec), "a pinned course leaked out of a solution"


# ----- determinism -----------------------------------------------------------


def test_deterministic_two_identical_runs():
    cur = curriculum(course("P"), course("A"), course("B"))
    secs = [
        _sec("s:P:M1", "P", [(1, 480, 600)]),
        _sec("s:P:M2", "P", [(2, 480, 600)]),
        _sec("s:A:T1", "A", [(1, 480, 600)]),
        _sec("s:B:T1", "B", [(3, 480, 600)]),
    ]
    a = _run(cur, secs, ["P"], top_n=5)
    b = _run(cur, secs, ["P"], top_n=5)
    assert a == b


# ----- combo cap -> truncated ------------------------------------------------


def test_combo_cap_truncates_with_best_so_far():
    cur = curriculum(course("P"))
    secs = [
        _sec("s:P:M1", "P", [(1, 480, 600)]),
        _sec("s:P:M2", "P", [(2, 480, 600)]),
        _sec("s:P:M3", "P", [(3, 480, 600)]),
    ]
    res = _run(cur, secs, ["P"], combo_cap=2)
    assert res["truncated"] is True
    assert res["recommendations"]  # best-so-far still returned
    assert _pin(res, "P")["status"] == "ok"


# ----- anti-regression: empty pins == plain engine ---------------------------


def test_empty_pinned_list_matches_plain_recommend_schedule():
    cur = curriculum(course("A"), course("B"))
    secs = [
        _sec("s:A:T1", "A", [(1, 480, 600)]),
        _sec("s:B:T1", "B", [(2, 480, 600)]),
    ]
    plain = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [], {}, 3
    )
    wrapped = _run(cur, secs, [])
    assert wrapped["pinned"] == [] and wrapped["pinned_infeasible"] is False
    for key in ("term", "recommendations", "eligible_courses", "truncated"):
        assert wrapped[key] == plain[key]


# ----- double-count regression (spec v5 section 8, risk nr. 6) ---------------


def test_locked_section_without_timeslots_counts_credits_once():
    # regression for the recommend.py fix: a timeslot-less locked section
    # passed `fits` against itself and was re-added, doubling its credits.
    cur = curriculum(course("NT", credits=4))
    sec = SectionSnap(id="s:NT:M1", course_code="NT", term="2026/2", timeslots=())
    res = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", [sec], ["s:NT:M1"], {}, 3
    )
    top = res["recommendations"][0]
    assert top["score_breakdown"]["credits"] == 4
    assert [c["section_id"] for c in top["choices"]] == ["s:NT:M1"]


def test_pinned_section_without_timeslots_counts_credits_once():
    cur = curriculum(course("NT", credits=4), course("A"))
    secs = [
        SectionSnap(id="s:NT:M1", course_code="NT", term="2026/2", timeslots=()),
        _sec("s:A:T1", "A", [(1, 480, 600)]),
    ]
    res = _run(cur, secs, ["NT"])
    top = res["recommendations"][0]
    assert top["score_breakdown"]["credits"] == 8  # NT(4) + A(4), never NT twice
    assert sorted(c["section_id"] for c in top["choices"]) == ["s:A:T1", "s:NT:M1"]
