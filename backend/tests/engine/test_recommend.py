"""Recommender (F3c): conflict-free, deterministic, respects locks/limits."""
from __future__ import annotations

from app.engine.recommend import recommend_schedule
from app.engine.types import SectionSnap, TimeslotSnap

from .helpers import course, curriculum, plan, state


def _sec(sid, code, slots, cap=40, enr=0):
    return SectionSnap(
        id=sid, course_code=code, term="2026/2",
        timeslots=tuple(TimeslotSnap(w, s, e) for w, s, e in slots),
        label=sid.split(":")[-1], capacity=cap, enrolled=enr,
    )


def test_recommendation_never_contains_conflict():
    cur = curriculum(course("A"), course("B"))
    secs = [
        _sec("s:A:T1", "A", [(1, 480, 600)]),
        _sec("s:B:T1", "B", [(1, 540, 660)]),  # conflicts with A/T1
        _sec("s:B:T2", "B", [(2, 480, 600)]),  # free slot
    ]
    res = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [], {}, top_n=3
    )
    assert res["recommendations"]
    for rec in res["recommendations"]:
        assert rec["diagnostics"] == []
        # top recommendation should take both via the non-conflicting B/T2
    top = res["recommendations"][0]
    codes = {c["course_code"] for c in top["choices"]}
    assert codes == {"A", "B"}


def test_respects_credit_limit():
    cur = curriculum(course("A", credits=20), course("B", credits=20))
    secs = [_sec("s:A:T1", "A", [(1, 480, 600)]), _sec("s:B:T1", "B", [(2, 480, 600)])]
    res = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [], {"max_credits": 20}, top_n=3
    )
    for rec in res["recommendations"]:
        assert rec["score_breakdown"]["credits"] <= 20


def test_eligibility_requires_prereqs_done():
    cur = curriculum(course("A"), course("B", ["A"]))
    secs = [_sec("s:A:T1", "A", [(1, 480, 600)]), _sec("s:B:T1", "B", [(2, 480, 600)])]
    # A not done -> B not eligible this term
    res = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [], {}, top_n=3
    )
    assert res["eligible_courses"] == ["A"]


def test_deterministic():
    cur = curriculum(course("A"), course("B"))
    secs = [_sec("s:A:T1", "A", [(1, 480, 600)]), _sec("s:B:T1", "B", [(2, 480, 600)])]
    a = recommend_schedule(cur, state(), plan(current="2026/1"), "2026/2", secs, [], {}, 3)
    b = recommend_schedule(cur, state(), plan(current="2026/1"), "2026/2", secs, [], {}, 3)
    assert a == b


# ===== item 1: cap electives at the remaining optativa hours ================


def test_elective_cap_never_recommends_more_than_needed():
    # two 60h electives, both fit and don't conflict; but only 60h remain -> a
    # recommendation must never bundle two optativas.
    cur = curriculum(
        course("E1", kind="optativa", hours=60, credits=4),
        course("E2", kind="optativa", hours=60, credits=4),
    )
    secs = [_sec("s:E1:T1", "E1", [(1, 480, 600)]), _sec("s:E2:T1", "E2", [(2, 480, 600)])]
    res = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [],
        {"remaining_elective_hours": 60}, top_n=5,
    )
    for rec in res["recommendations"]:
        elec = [c for c in rec["choices"] if c["course_code"] in {"E1", "E2"}]
        assert len(elec) <= 1
    # sanity: WITHOUT the cap the engine would take both (they fit)
    res2 = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [], {}, top_n=5
    )
    assert any(len(rec["choices"]) == 2 for rec in res2["recommendations"])


def test_elective_cap_zero_recommends_no_elective():
    cur = curriculum(
        course("E1", kind="optativa", hours=60), course("E2", kind="optativa", hours=60)
    )
    secs = [_sec("s:E1:T1", "E1", [(1, 480, 600)]), _sec("s:E2:T1", "E2", [(2, 480, 600)])]
    res = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [],
        {"remaining_elective_hours": 0}, top_n=5,
    )
    picked = {c["course_code"] for rec in res["recommendations"] for c in rec["choices"]}
    assert not (picked & {"E1", "E2"})  # optativa category satisfied -> none suggested


def test_elective_cap_absent_is_prior_behavior():
    cur = curriculum(course("E1", kind="optativa", hours=60), course("E2", kind="optativa", hours=60))
    secs = [_sec("s:E1:T1", "E1", [(1, 480, 600)]), _sec("s:E2:T1", "E2", [(2, 480, 600)])]
    without = recommend_schedule(cur, state(), plan(current="2026/1"), "2026/2", secs, [], {}, 5)
    none_key = recommend_schedule(
        cur, state(), plan(current="2026/1"), "2026/2", secs, [],
        {"remaining_elective_hours": None}, 5,
    )
    assert without == none_key  # None == absent == exact prior behavior
