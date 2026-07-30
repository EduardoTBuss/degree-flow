"""Tests for the greedy autoplanner (RN5) and critical path."""
from __future__ import annotations

from app.engine.autoplan import autoplan
from app.engine.critical_path import compute_critical_path

from .helpers import course, curriculum, plan, reqs, state, types_of

NO_REQS = reqs()


def _chain():
    # A -> B -> C, all falta
    return curriculum(course("A"), course("B", ["A"]), course("C", ["B"]))


def test_critical_path_length_and_earliest():
    cur = _chain()
    cp, diags = compute_critical_path(cur, state(), "2026/2")
    assert diags == []
    assert cp.length_terms == 3
    assert cp.course_codes == ("A", "B", "C")
    assert cp.earliest_completion_term == "2028/1"  # 2026/2 + 3 semesters (2027/1,2027/2,2028/1)


def test_autoplan_feasible_schedules_whole_chain():
    cur = _chain()
    res = autoplan(cur, state(), plan(), NO_REQS, target_term="2028/1")
    assert res.feasible
    terms = {p.course_code: p.term for p in res.proposal}
    # strictly increasing terms along the chain
    assert terms["A"] < terms["B"] < terms["C"]


def test_autoplan_unreachable_when_target_too_soon():
    cur = _chain()
    # chain needs 3 terms; target is only 1 term ahead -> impossible
    res = autoplan(cur, state(), plan(), NO_REQS, target_term="2026/2")
    assert not res.feasible
    assert "TARGET_UNREACHABLE" in types_of(res.diagnostics)


def test_autoplan_respects_and_flags_bad_lock():
    from app.engine.types import PlanItemSnap

    cur = _chain()
    # lock C into a term where its prereqs cannot complete before
    pl = plan({"C": PlanItemSnap(term="2026/2", locked=True)})
    res = autoplan(cur, state(), pl, NO_REQS, target_term="2028/1")
    assert not res.feasible
    assert "LOCK_CONFLICT" in types_of(res.diagnostics)


def test_autoplan_offer_parity_respected():
    # B only offered in /1 terms -> must land on a /1 term after A
    cur = curriculum(course("A"), course("B", ["A"], offer="/1"))
    res = autoplan(cur, state(), plan(current="2026/2"), NO_REQS, target_term="2028/2")
    assert res.feasible
    b_term = next(p.term for p in res.proposal if p.course_code == "B")
    assert b_term.endswith("/1")


def test_autoplan_ignores_approved_courses():
    cur = _chain()
    res = autoplan(cur, state({"A": "aprovada", "B": "aprovada"}), plan(), NO_REQS, target_term="2027/1")
    codes = {p.course_code for p in res.proposal}
    assert codes == {"C"}
