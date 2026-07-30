"""Golden tests for engine/campaign.py (A2/C1).

- eligibility_at_term (ADR-021): term-sensitive RN1; cross-checked against
  validate() so the "blocked" badge and PREREQ_VIOLATION never contradict each
  other (risk listed in ARCHITECTURE-v4 section 11).
- criticality_queue (spec v4 7.2): exact deterministic sort key
  (blocked, !critical, -height, -credits, difficulty, course_code).
"""
from __future__ import annotations

from app.engine.campaign import criticality_queue, eligibility_at_term
from app.engine.types import PlanItemSnap, SectionSnap, UserState
from app.engine.validate import validate

from .helpers import course, curriculum, plan, reqs


def _state(status, completed=None, difficulty=None):
    return UserState(
        status=dict(status),
        difficulty=dict(difficulty or {}),
        completed_term=dict(completed or {}),
    )


# ----- universe ----------------------------------------------------------


def test_universe_is_falta_obrigatoria_or_optativa_only():
    cur = curriculum(
        course("A"),                          # aprovada -> out
        course("B"),                          # falta obrigatoria -> in
        course("C", kind="optativa"),         # falta optativa -> in
        course("D", kind="atividade"),        # falta atividade -> out
        course("E", kind="fora_curriculo"),   # out
        course("F"),                          # cursando -> out
    )
    st = _state({"A": "aprovada", "F": "cursando"})
    got = eligibility_at_term(cur, st, plan(current="2026/1"), "2026/2")
    assert set(got) == {"B", "C"}
    assert got["B"].eligible and got["C"].eligible


# ----- prereq rules at `term` (RN1, strict) -------------------------------


def test_prereq_allocated_same_term_blocks():
    # DoD: prereq allocated in the SAME term -> eligible=false with the reason
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    st = _state({})
    p = plan({"P": PlanItemSnap(term="2026/2")}, current="2026/1")
    got = eligibility_at_term(cur, st, p, "2026/2")
    assert got["D"].eligible is False
    assert got["D"].missing_prereqs == ("P",)


def test_prereq_allocated_strictly_before_is_ok():
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    p = plan({"P": PlanItemSnap(term="2026/2")}, current="2026/1")
    got = eligibility_at_term(cur, _state({}), p, "2027/1")
    assert got["D"].eligible is True
    assert got["D"].missing_prereqs == ()


def test_prereq_cursando_eligible_only_in_future_term():
    # DoD: prereq cursando -> eligible at a term AFTER current, not at current
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    st = _state({"P": "cursando"})
    p = plan({}, current="2026/1")
    assert eligibility_at_term(cur, st, p, "2026/2")["D"].eligible is True
    at_current = eligibility_at_term(cur, st, p, "2026/1")["D"]
    assert at_current.eligible is False
    assert at_current.missing_prereqs == ("P",)


def test_prereq_aprovada_without_completed_term_is_ok():
    # DoD: aprovada with completed_term=None counts everywhere
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    st = _state({"P": "aprovada"})
    got = eligibility_at_term(cur, st, plan(current="2026/1"), "2026/1")
    assert got["D"].eligible is True


def test_prereq_completed_term_must_be_strictly_before():
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    st = _state({"P": "aprovada"}, completed={"P": "2026/2"})
    p = plan(current="2026/1")
    assert eligibility_at_term(cur, st, p, "2026/2")["D"].eligible is False
    assert eligibility_at_term(cur, st, p, "2027/1")["D"].eligible is True


def test_missing_prereqs_sorted_by_code():
    cur = curriculum(
        course("B2"), course("A1"), course("D", prereqs=("B2", "A1", "ZZ-fora")),
    )
    got = eligibility_at_term(cur, _state({}), plan(current="2026/1"), "2026/2")
    # 'ZZ-fora' is not in the curriculum -> ignored (validate/recommend rule)
    assert got["D"].missing_prereqs == ("A1", "B2")


# ----- cross-check with validate (badge x PREREQ_VIOLATION) ----------------


def _prereq_violations(cur, st, p):
    res = validate(cur, st, p, reqs())
    return {d.course_codes[0] for d in res.diagnostics if d.type == "PREREQ_VIOLATION"}


def test_cross_same_term_allocation_agrees_with_validate():
    # eligibility says D is blocked at 2026/2 when P is allocated at 2026/2;
    # allocating D there must then raise PREREQ_VIOLATION — never a free pass.
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    st = _state({})
    term = "2026/2"
    p_alloc = plan(
        {"P": PlanItemSnap(term=term), "D": PlanItemSnap(term=term)}, current="2026/1"
    )
    elig = eligibility_at_term(cur, st, plan({"P": PlanItemSnap(term=term)}, current="2026/1"), term)
    assert elig["D"].eligible is False
    assert "D" in _prereq_violations(cur, st, p_alloc)


def test_cross_cursando_prereq_agrees_with_validate():
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    st = _state({"P": "cursando"})
    # future term: eligible AND no violation when allocated there
    future = plan({"D": PlanItemSnap(term="2026/2")}, current="2026/1")
    assert eligibility_at_term(cur, st, future, "2026/2")["D"].eligible is True
    assert "D" not in _prereq_violations(cur, st, future)
    # current term: blocked AND violation when allocated there
    now = plan({"D": PlanItemSnap(term="2026/1")}, current="2026/1")
    assert eligibility_at_term(cur, st, now, "2026/1")["D"].eligible is False
    assert "D" in _prereq_violations(cur, st, now)


def test_cross_completed_term_boundary_agrees_with_validate():
    cur = curriculum(course("P"), course("D", prereqs=("P",)))
    st = _state({"P": "aprovada"}, completed={"P": "2026/1"})
    # completed at 2026/1 -> blocked AT 2026/1, free at 2026/2 (strict <)
    blocked = plan({"D": PlanItemSnap(term="2026/1")}, current="2026/1")
    assert eligibility_at_term(cur, st, blocked, "2026/1")["D"].eligible is False
    assert "D" in _prereq_violations(cur, st, blocked)
    ok = plan({"D": PlanItemSnap(term="2026/2")}, current="2026/1")
    assert eligibility_at_term(cur, st, ok, "2026/2")["D"].eligible is True
    assert "D" not in _prereq_violations(cur, st, ok)


# ===== C1: criticality_queue (spec v4 7.2) ================================


TERM = "2026/2"


def _sec(code, label="M1", term=TERM):
    return SectionSnap(
        id=f"{term.replace('/', '-')}:{code}:{label}",
        course_code=code,
        term=term,
        timeslots=(),
        label=label,
    )


def _codes(entries):
    return [e.course_code for e in entries]


def test_queue_universe_falta_with_offer_only():
    cur = curriculum(
        course("A"),                        # falta + section -> in
        course("B"),                        # falta, NO section -> out
        course("C"),                        # aprovada + section -> out
        course("D", kind="atividade"),      # atividade + section -> out
    )
    st = _state({"C": "aprovada"})
    secs = [_sec("A"), _sec("C"), _sec("D"), _sec("A", label="X", term="2027/1")]
    got = criticality_queue(cur, st, plan(current="2026/1"), TERM, secs)
    assert _codes(got) == ["A"]
    assert got[0].section_ids == ("2026-2:A:M1",)


def test_queue_blocked_never_before_released():
    # B is critical, tall and fat (best on every other key) but BLOCKED at the
    # term; A is a modest released course -> A must still come first (D6).
    cur = curriculum(
        course("P"),                                   # falta, blocks B
        course("B", prereqs=("P",), credits=8),        # blocked
        course("B2", prereqs=("B",)),                  # gives B height 2 within falta
        course("A", credits=2),                        # released, low value
    )
    st = _state({})
    got = criticality_queue(cur, st, plan(current="2026/1"), TERM, [_sec("B"), _sec("A")])
    assert _codes(got) == ["A", "B"]
    assert got[1].blocked is True
    assert got[1].missing_prereqs == ("P",)
    assert got[0].blocked is False


def test_queue_critical_before_non_critical():
    # chain X -> Y (length 2) is the critical path; W is standalone.
    # W has more credits, but critical wins first.
    cur = curriculum(
        course("X"), course("Y", prereqs=("X",)), course("W", credits=8),
    )
    st = _state({})
    got = criticality_queue(cur, st, plan(current="2026/1"), TERM, [_sec("X"), _sec("W")])
    assert _codes(got) == ["X", "W"]
    assert got[0].critical is True and got[1].critical is False


def test_queue_height_credits_difficulty_and_code_tiebreaks():
    # all non-critical is impossible with a single longest chain; make the
    # critical chain offer-less so the queue exercises the later keys only.
    cur = curriculum(
        course("CP1"), course("CP2", prereqs=("CP1",)), course("CP3", prereqs=("CP2",)),
        course("T", credits=4),                  # height 2 (T2 depends on it)
        course("T2", prereqs=("T",), credits=4),  # no section -> not in queue
        course("F", credits=8),                  # height 1, more credits
        course("G", credits=4),                  # ties with H except difficulty
        course("H", credits=4),
        course("I", credits=4),                  # full tie with H -> code order
    )
    st = _state({}, difficulty={"G": 5, "H": 2, "I": 2})
    secs = [_sec("T"), _sec("F"), _sec("G"), _sec("H"), _sec("I")]
    got = criticality_queue(cur, st, plan(current="2026/1"), TERM, secs)
    # -height first (T=2), then -credits (F=8), then difficulty (H/I=2 < G=5)
    # with course_code breaking the H x I tie.
    assert _codes(got) == ["T", "F", "H", "I", "G"]
    t = got[0]
    assert t.height == 2 and t.unlocks == 1


def test_queue_deterministic_and_sections_sorted():
    cur = curriculum(course("A"), course("B"))
    st = _state({})
    secs = [_sec("A", label="T9"), _sec("B"), _sec("A", label="M1")]
    p = plan(current="2026/1")
    first = criticality_queue(cur, st, p, TERM, secs)
    second = criticality_queue(cur, st, p, TERM, list(reversed(secs)))
    assert first == second  # input order never leaks into the result
    a = next(e for e in first if e.course_code == "A")
    assert a.section_ids == ("2026-2:A:M1", "2026-2:A:T9")


# ===== C2: suggest_swaps (spec v4 7.4) =====================================

from app.engine.campaign import suggest_swaps  # noqa: E402  (C2 block-local)
from app.engine.types import TimeslotSnap  # noqa: E402

MON = TimeslotSnap(weekday=0, start_min=480, end_min=600)   # seg 08:00-10:00
TUE = TimeslotSnap(weekday=1, start_min=480, end_min=600)
WED = TimeslotSnap(weekday=2, start_min=480, end_min=600)


def _tsec(code, label="M1", slots=(), term=TERM):
    return SectionSnap(
        id=f"{term.replace('/', '-')}:{code}:{label}",
        course_code=code,
        term=term,
        timeslots=tuple(slots),
        label=label,
    )


def _delta_matches_breakdown(sug):
    b = sug["score_breakdown"]
    return sug["score_delta"] == (
        100 * (b["gained_critical"] - b["lost_critical"])
        + 10 * (b["gained_unlocks"] - b["lost_unlocks"])
        + 2 * (b["gained_credits"] - b["lost_credits"])
    )


def test_swap_valid_only_thanks_to_drop():
    # DoD's dedicated two-wave case: the add (Y) conflicts with a held section
    # and that held section IS exactly the drop — the slot freed by the drop
    # must count as free in the SAME suggestion (removals before additions).
    cur = curriculum(
        course("X", credits=2),
        course("Y", credits=4),
        course("Y2", prereqs=("Y",)),  # puts Y on the critical path (height 2)
    )
    st = _state({})
    held = [_tsec("X", slots=(MON,))]
    pool = [_tsec("Y", slots=(MON,))]  # same slot: only fits if X is dropped
    got = suggest_swaps(cur, st, plan(current="2026/1"), TERM, held, pool)
    assert got["truncated"] is False
    assert len(got["suggestions"]) == 1
    sug = got["suggestions"][0]
    assert [s.course_code for s in sug["drop"]] == ["X"]
    assert [s.course_code for s in sug["add"]] == ["Y"]
    # value(Y) = 100 (critical) + 10*1 (unlocks Y2) + 2*4; value(X) = 2*2
    assert sug["score_delta"] == 114.0
    assert sug["score_breakdown"] == {
        "gained_critical": 1, "gained_unlocks": 1, "gained_credits": 4,
        "lost_critical": 0, "lost_unlocks": 0, "lost_credits": 2,
    }
    assert _delta_matches_breakdown(sug)


def test_no_suggestion_when_delta_not_positive():
    # Y only fits by dropping the critical X -> delta < 0, never suggested;
    # "no beneficial swap" is an empty result, not an error.
    cur = curriculum(
        course("X", credits=4),
        course("X2", prereqs=("X",)),  # X critical (height 2)
        course("Y", credits=2),
    )
    held = [_tsec("X", slots=(MON,))]
    pool = [_tsec("Y", slots=(MON,))]
    got = suggest_swaps(cur, _state({}), plan(current="2026/1"), TERM, held, pool)
    assert got == {"suggestions": [], "truncated": False}


def test_add_of_base_course_excluded_and_zero_delta_never_kept():
    # A pure add of a course already in base is invalid; the same-course
    # section swap (drop X:M1, add X:T1) has delta == 0 -> delta > 0 filters it.
    cur = curriculum(course("X", credits=4))
    held = [_tsec("X", "M1", (MON,))]
    pool = [_tsec("X", "T1", (TUE,))]
    got = suggest_swaps(cur, _state({}), plan(current="2026/1"), TERM, held, pool)
    assert got["suggestions"] == []


def test_pure_add_ranking_and_fewer_drops_tiebreak():
    # A is critical (108), B/H are worth 8. Candidates kept (delta > 0):
    # [] +{A,B} = 116; [] +{A} = 108; {H}+{A,B} = 108; {H}+{A} = 100.
    # The 108 tie must be broken by |D| (pure addition first).
    cur = curriculum(course("A", credits=4), course("B", credits=4), course("H", credits=4))
    held = [_tsec("H", slots=(MON,))]
    pool = [_tsec("A", slots=(TUE,)), _tsec("B", slots=(WED,))]
    got = suggest_swaps(cur, _state({}), plan(current="2026/1"), TERM, held, pool)
    assert got["truncated"] is False
    sugs = got["suggestions"]
    shape = [
        ([d.course_code for d in s["drop"]], [a.course_code for a in s["add"]],
         s["score_delta"])
        for s in sugs
    ]
    assert shape == [
        ([], ["A", "B"], 116.0),
        ([], ["A"], 108.0),
        (["H"], ["A", "B"], 108.0),
        (["H"], ["A"], 100.0),
        ([], ["B"], 8.0),
    ]
    for s in sugs:
        assert s["score_delta"] > 0
        assert _delta_matches_breakdown(s)


def test_swap_input_order_never_changes_output():
    cur = curriculum(
        course("A", credits=4), course("B", credits=6),
        course("H1", credits=2), course("H2", credits=2),
    )
    held = [_tsec("H1", slots=(MON,)), _tsec("H2", slots=(TUE,))]
    pool = [_tsec("A", slots=(MON,)), _tsec("B", slots=(TUE,))]
    p = plan(current="2026/1")
    first = suggest_swaps(cur, _state({}), p, TERM, held, pool)
    second = suggest_swaps(
        cur, _state({}), p, TERM, list(reversed(held)), list(reversed(pool))
    )
    assert first == second


def test_swap_node_cap_truncates_with_best_so_far(monkeypatch):
    import app.engine.campaign as campaign_mod

    monkeypatch.setattr(campaign_mod, "_SWAP_NODE_CAP", 2)
    cur = curriculum(course("A"), course("B"), course("C"))
    pool = [_tsec("A"), _tsec("B"), _tsec("C")]  # 6 candidate subsets > cap 2
    got = suggest_swaps(cur, _state({}), plan(current="2026/1"), TERM, [], pool)
    assert got["truncated"] is True
    assert got["suggestions"], "best-so-far must still be returned on overflow"
    assert all(s["score_delta"] > 0 for s in got["suggestions"])


# ===== C3: rank_special_candidates (spec v4 7.3) ============================

from app.engine.campaign import rank_special_candidates  # noqa: E402


def _csec(code, label="M1", slots=(), capacity=None, enrolled=None, term=TERM):
    return SectionSnap(
        id=f"{term.replace('/', '-')}:{code}:{label}",
        course_code=code,
        term=term,
        timeslots=tuple(slots),
        label=label,
        capacity=capacity,
        enrolled=enrolled,
    )


def _rank(cur, st, secs, accepted=(), offered=None, k=5, p=None):
    return rank_special_candidates(
        cur, st, p or plan(current="2026/1"), TERM,
        list(secs), list(accepted), offered or {}, k=k,
    )


# offer-less 3-long chain: absorbs the critical path so the candidates under
# test stay off it (same trick as the C1/C2 tests above)
_CP_CHAIN = (
    course("ZCP1"),
    course("ZCP2", prereqs=("ZCP1",)),
    course("ZCP3", prereqs=("ZCP2",)),
)


def test_rank_weights_exact_full_breakdown():
    # Y: critical (single longest chain Y -> Y2), height 2, 4 credits; its only
    # section is clean vs the accepted one and has open vacancy.
    # score = 100 + 10*(2-1) + 2*4 + fit 20 + vacancy 10 = 148.
    cur = curriculum(course("Y", credits=4), course("Y2", prereqs=("Y",)))
    st = _state({})
    got = _rank(
        cur, st,
        [_csec("Y", slots=(MON,), capacity=40, enrolled=10)],
        accepted=[_csec("ACC", slots=(TUE,))],
    )
    assert [c.course_code for c in got] == ["Y"]
    c = got[0]
    assert c.score == 148.0
    assert c.score_breakdown == {
        "critical": 100.0, "unlocks": 10.0, "credits": 8.0,
        "fit": 20.0, "vacancy": 10.0, "full": 0.0, "blocked": 0.0, "foreign": 0.0,
    }
    assert c.blocked is False and c.missing_prereqs == ()
    assert c.full is False
    assert c.conflicts_with == () and c.alternatives == ()


def test_rank_fit_minus_60_per_accepted_conflict():
    # the only section of X overlaps BOTH accepted sections -> fit = -120
    cur = curriculum(course("X", credits=4), *_CP_CHAIN)
    got = _rank(
        cur, _state({}),
        [_csec("X", slots=(MON, TUE))],
        accepted=[_csec("A1", slots=(MON,)), _csec("A2", "T1", (TUE,))],
    )
    c = got[0]
    assert c.score_breakdown["fit"] == -120.0
    assert c.conflicts_with == ("2026-2:A1:M1", "2026-2:A2:T1")
    assert c.score == 8.0 - 120.0  # 2*4 credits - 120 fit


def test_rank_full_penalized_and_flagged_never_excluded():
    # D5: F is full (-25, full=True) but MUST stay in the list; G, identical
    # except for the open vacancy (+10), outranks it by exactly 35.
    cur = curriculum(course("F", credits=4), course("G", credits=4), *_CP_CHAIN)
    got = _rank(
        cur, _state({}),
        [
            _csec("F", capacity=30, enrolled=30),
            _csec("G", capacity=30, enrolled=29),
        ],
    )
    assert [c.course_code for c in got] == ["G", "F"]
    g, f = got
    assert f.full is True
    assert f.score_breakdown["full"] == -25.0 and f.score_breakdown["vacancy"] == 0.0
    assert g.full is False
    assert g.score_breakdown["vacancy"] == 10.0 and g.score_breakdown["full"] == 0.0
    assert g.score - f.score == 35.0


def test_rank_capacity_none_is_neutral():
    # level-2 data (D5): no capacity/enrolled -> vacancy 0, full 0, full=False
    cur = curriculum(course("N", credits=4), *_CP_CHAIN)
    c = _rank(cur, _state({}), [_csec("N")])[0]
    assert c.full is False
    assert c.score_breakdown["vacancy"] == 0.0
    assert c.score_breakdown["full"] == 0.0
    assert c.score == 28.0  # 2*4 credits + 20 fit


def test_rank_universe_grade_falta_only_d3():
    # D3: a section whose course has no curriculum entry (discovered elective)
    # never enters; aprovada/atividade courses and other-term sections are out.
    cur = curriculum(
        course("A"),                      # falta + section -> in
        course("B"),                      # aprovada -> out
        course("D", kind="atividade"),    # out by kind
    )
    st = _state({"B": "aprovada"})
    secs = [
        _csec("A"),
        _csec("B"),
        _csec("D"),
        _csec("ZZ999"),                   # not in the curriculum (ADR-016) -> out
        _csec("A", label="X", term="2027/1"),  # other term -> out
    ]
    got = _rank(cur, st, secs)
    assert [c.course_code for c in got] == ["A"]


def test_rank_own_course_candidate_and_foreign_penalty_d2():
    # D2: a section of the user's own course (offered_to=None / absent) is a
    # legitimate candidate with foreign=0; only offered_to != None takes -10.
    cur = curriculum(course("OWN", credits=4), course("EXT", credits=4), *_CP_CHAIN)
    offered = {"2026-2:EXT:M1": ["Ciência da Computação"], "2026-2:OWN:M1": None}
    got = _rank(cur, _state({}), [_csec("OWN"), _csec("EXT")], offered=offered)
    by_code = {c.course_code: c for c in got}
    assert by_code["OWN"].score_breakdown["foreign"] == 0.0
    assert by_code["EXT"].score_breakdown["foreign"] == -10.0
    assert [c.course_code for c in got] == ["OWN", "EXT"]  # -10 decides


def test_rank_best_section_by_fit_vacancy_then_section_id():
    # M1 conflicts with the accepted block (-60) and is full (-25); T9 is clean
    # (+20) with vacancy (+10) -> best is T9 despite the larger id; M1 becomes
    # the alternative. foreign applies to the BEST section only.
    cur = curriculum(course("C", credits=4), *_CP_CHAIN)
    offered = {"2026-2:C:M1": ["Outro Curso"]}  # penalty would hit M1 only
    got = _rank(
        cur, _state({}),
        [
            _csec("C", "M1", (MON,), capacity=20, enrolled=20),
            _csec("C", "T9", (TUE,), capacity=20, enrolled=10),
        ],
        accepted=[_csec("ACC", slots=(MON,))],
        offered=offered,
    )
    c = got[0]
    assert c.section_id == "2026-2:C:T9"
    assert c.alternatives == ("2026-2:C:M1",)
    assert c.conflicts_with == ()  # conflicts are the BEST section's
    assert c.score_breakdown["foreign"] == 0.0  # best (T9) has no offered_to
    assert c.score == 38.0  # 8 credits + 20 fit + 10 vacancy


def test_rank_best_section_tie_breaks_by_smallest_id():
    # identical (fit + vacancy) -> smallest section_id wins deterministically
    cur = curriculum(course("C", credits=4))
    got = _rank(cur, _state({}), [_csec("C", "T1"), _csec("C", "M1")])
    assert got[0].section_id == "2026-2:C:M1"
    assert got[0].alternatives == ("2026-2:C:T1",)


def test_rank_blocked_never_rank1_with_alternative_d6():
    # B is blocked but far more valuable even after -50; the post-rule promotes
    # the FIRST non-blocked candidate to rank 1, everyone else keeps order.
    cur = curriculum(
        course("P"),                                  # falta -> blocks B and C
        course("B", prereqs=("P",), credits=8),
        course("B2", prereqs=("B",)),                 # B critical, height 2
        course("C", prereqs=("P",), credits=6),      # blocked, mid value
        course("A", credits=2),                      # released, low value
    )
    st = _state({})
    got = _rank(cur, st, [_csec("B"), _csec("C"), _csec("A")])
    # raw scores: B = 100+10+16-50+20 = 96; C = 12-50+20 = -18; A = 4+20 = 24
    # raw order [B, A, C] -> post-rule: A jumps over B; B and C keep order.
    assert [c.course_code for c in got] == ["A", "B", "C"]
    assert got[0].blocked is False
    assert got[1].blocked is True and got[1].score == 96.0
    assert got[1].score_breakdown["blocked"] == -50.0
    assert got[1].missing_prereqs == ("P",)


def test_rank_all_blocked_keeps_raw_order():
    # no non-blocked candidate in the top-k -> the post-rule is a no-op
    cur = curriculum(
        course("P"),
        course("B", prereqs=("P",), credits=8),
        course("B2", prereqs=("B",)),  # critical path fixed on P->B->B2
        course("C", prereqs=("P",), credits=4),
    )
    got = _rank(cur, _state({}), [_csec("B"), _csec("C")])
    assert [c.course_code for c in got] == ["B", "C"]
    assert all(c.blocked for c in got)


def test_rank_post_rule_sees_only_the_topk():
    # the released candidate is CUT by k -> rank 1 stays blocked (D6 post-rule
    # promotes within the top-k, never resurrects below the cut). B is made
    # critical and tall so its raw score tops A's even after the -50.
    cur = curriculum(
        course("P"),
        course("B", prereqs=("P",), credits=8),
        course("B2", prereqs=("B",)),
        course("A", credits=2),
    )
    got = _rank(cur, _state({}), [_csec("B"), _csec("A")], k=1)
    assert [c.course_code for c in got] == ["B"]
    assert got[0].blocked is True


def test_rank_sort_by_score_then_course_code_and_topk():
    cur = curriculum(
        course("K1", credits=4), course("K2", credits=4), course("K3", credits=6),
        *_CP_CHAIN,
    )
    got = _rank(cur, _state({}), [_csec("K2"), _csec("K3"), _csec("K1")])
    # K3 wins by credits; K1/K2 tie -> course_code decides
    assert [c.course_code for c in got] == ["K3", "K1", "K2"]
    assert [c.course_code for c in _rank(
        cur, _state({}), [_csec("K2"), _csec("K3"), _csec("K1")], k=2
    )] == ["K3", "K1"]


def test_rank_input_order_never_changes_output():
    cur = curriculum(course("A", credits=4), course("B", credits=6))
    secs = [_csec("A", "T1"), _csec("B"), _csec("A", "M1", (MON,))]
    accepted = [_csec("ACC", slots=(MON,)), _csec("AC2", "T1", (TUE,))]
    p = plan(current="2026/1")
    first = rank_special_candidates(
        cur, _state({}), p, TERM, secs, accepted, {}, k=5
    )
    second = rank_special_candidates(
        cur, _state({}), p, TERM, list(reversed(secs)), list(reversed(accepted)), {}, k=5
    )
    assert first == second
