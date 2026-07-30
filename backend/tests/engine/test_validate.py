"""Golden/edge tests for the pure validation engine (RN1, RN2, RN4, RN6)."""
from __future__ import annotations

from app.engine.validate import validate

from .helpers import category, course, curriculum, plan, reqs, state, types_of

NO_REQS = reqs()


def test_clean_plan_has_no_errors():
    cur = curriculum(course("A"), course("B", ["A"]))
    st = state({"A": "aprovada"})
    pl = plan({"B": "2026/2"})
    res = validate(cur, st, pl, NO_REQS)
    assert res.valid
    assert res.diagnostics == ()


def test_prereq_violation_same_term():
    cur = curriculum(course("A"), course("B", ["A"]))
    st = state()  # A is falta
    pl = plan({"A": "2026/2", "B": "2026/2"})  # B needs A strictly before
    res = validate(cur, st, pl, NO_REQS)
    assert "PREREQ_VIOLATION" in types_of(res.diagnostics)
    assert not res.valid
    assert "B" in res.blocked_codes


def test_prereq_satisfied_when_before():
    cur = curriculum(course("A"), course("B", ["A"]))
    pl = plan({"A": "2026/2", "B": "2027/1"})
    res = validate(cur, state(), pl, NO_REQS)
    assert res.valid


def test_offer_mismatch_is_warning_not_error():
    cur = curriculum(course("A", offer="/1"))
    pl = plan({"A": "2026/2"})  # 2026/2 is a /2 term
    res = validate(cur, state(), pl, NO_REQS)
    assert types_of(res.diagnostics) == ["OFFER_MISMATCH"]
    assert res.valid  # warning only


def test_overload_by_credits():
    cur = curriculum(*[course(f"C{i}", credits=8) for i in range(4)])
    pl = plan({f"C{i}": "2026/2" for i in range(4)}, max_credits=28)  # 32 > 28
    res = validate(cur, state(), pl, NO_REQS)
    assert "TERM_OVERLOADED" in types_of(res.diagnostics)


def test_overload_by_difficulty_independently():
    cur = curriculum(course("A", credits=2), course("B", credits=2))
    st = state(difficulty={"A": 5, "B": 5})
    pl = plan({"A": "2026/2", "B": "2026/2"}, max_credits=28, max_diff=8)  # 10 > 8
    res = validate(cur, st, pl, NO_REQS)
    over = [d for d in res.diagnostics if d.type == "TERM_OVERLOADED"]
    assert over and over[0].details["difficulty_sum"] == 10


def test_cycle_is_reported_and_aborts():
    cur = curriculum(course("A", ["B"]), course("B", ["A"]))
    res = validate(cur, state(), plan(), NO_REQS)
    assert types_of(res.diagnostics) == ["PREREQ_CYCLE"]
    assert res.blocked_codes == ()


def test_blocked_derived_for_falta_with_unmet_prereq():
    cur = curriculum(course("A"), course("B", ["A"]))
    # A falta and not allocated -> B is blocked even though B is not allocated
    res = validate(cur, state(), plan(), NO_REQS)
    assert "B" in res.blocked_codes


def test_requirement_shortfall_only_with_target():
    cur = curriculum(course("A"))
    r = reqs(category("complementares", 315, logged=100))
    # no target -> no shortfall diagnostic
    assert types_of(validate(cur, state(), plan(), r).diagnostics) == []
    # with target -> shortfall warning
    res = validate(cur, state(), plan(target="2028/2"), r)
    assert "REQUIREMENT_SHORTFALL" in types_of(res.diagnostics)


def test_term_summaries_aggregate_credits_and_difficulty():
    cur = curriculum(course("A", credits=4), course("B", credits=6))
    st = state(difficulty={"A": 3, "B": 4})
    res = validate(cur, st, plan({"A": "2026/2", "B": "2026/2"}), NO_REQS)
    s = next(t for t in res.term_summaries if t.term == "2026/2")
    assert s.credits == 10 and s.difficulty_sum == 7
