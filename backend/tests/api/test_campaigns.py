"""API contract tests for v4.3 (C1): enrollment campaigns and requests.

Real seed, throwaway db (client fixture). Course cast (stable across the
2027/1 reform — none of them is touched by its rules):
- 22000236 Análise de Circuitos I  — falta, no prereqs -> eligible at 2026/2
- 22000270 Sistemas Operacionais   — falta, prereqs cursando -> eligible at 2026/2
- 22000273 Redes de Computadores   — falta, prereq 22000270 falta -> BLOCKED
- 22000299 AED II                  — falta (used as the foreign-offer section)
"""
from __future__ import annotations

TERM = "2026/2"
SEC_236_M1 = "2026-2:22000236:M1"
SEC_236_T1 = "2026-2:22000236:T1"
SEC_270_M1 = "2026-2:22000270:M1"
SEC_273_M1 = "2026-2:22000273:M1"
SEC_299_M1 = "2026-2:22000299:M1"


def _new_plan(client, current_term="2026/1", **kw) -> str:
    body = {"name": "camp", "current_term": current_term, **kw}
    r = client.post("/api/v1/plans", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _offer(client, sections=None):
    default = [
        {"course_code": "22000236", "label": "M1",
         "timeslots": [{"weekday": 0, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000236", "label": "T1",
         "timeslots": [{"weekday": 1, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000270", "label": "M1",
         "timeslots": [{"weekday": 2, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000273", "label": "M1",
         "timeslots": [{"weekday": 3, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000299", "label": "M1",
         "offered_to": ["Ciência da Computação"],
         "timeslots": [{"weekday": 4, "start": "08:00", "end": "10:00"}]},
    ]
    r = client.put("/api/v1/admin/terms/2026-2/sections",
                   json={"sections": sections if sections is not None else default})
    assert r.status_code == 200, r.text


def _prepare_demo_progress(client):
    """Release prerequisites explicitly; the public seed has no user history."""
    for code in ("22000182", "22000297"):
        response = client.patch(f"/api/v1/courses/{code}/state", json={"status": "cursando"})
        assert response.status_code == 200, response.text


def _campaign(client, pid, term=None) -> dict:
    r = client.post(f"/api/v1/plans/{pid}/campaigns",
                    json={"term": term} if term else {})
    assert r.status_code == 201, r.text
    return r.json()


def _request(client, pid, cid, **body):
    return client.post(f"/api/v1/plans/{pid}/campaigns/{cid}/requests", json=body)


def _patch_req(client, pid, cid, rid, **body):
    return client.patch(f"/api/v1/plans/{pid}/campaigns/{cid}/requests/{rid}", json=body)


# ----- campaign CRUD (6.3) ----------------------------------------------


def test_create_campaign_default_term_rounds_from_grade(client):
    pid = _new_plan(client)
    body = _campaign(client, pid)  # no term -> next_term(2026/1) = 2026/2
    assert body["term"] == TERM
    assert body["status"] == "aberta"
    assert body["requests"] == []
    assert body["accepted_section_ids"] == []
    # rounds come from grade_version.enrollment_rules (ADR-018), in order
    keys = [r["key"] for r in body["rounds"]]
    assert keys == ["rematricula", "correcao", "especial"]
    especial = body["rounds"][2]
    assert especial["max_adds"] == 2 and especial["scope"] == "qualquer_curso"


def test_create_campaign_duplicate_is_409_campaign_exists(client):
    pid = _new_plan(client)
    first = _campaign(client, pid)
    r = client.post(f"/api/v1/plans/{pid}/campaigns", json={"term": TERM})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "CAMPAIGN_EXISTS"
    assert err["details"]["campaign_id"] == first["id"]


def test_create_campaign_past_or_malformed_term_is_400(client):
    pid = _new_plan(client)
    r = client.post(f"/api/v1/plans/{pid}/campaigns", json={"term": "2025/2"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BAD_REQUEST"
    r = client.post(f"/api/v1/plans/{pid}/campaigns", json={"term": "20262"})
    assert r.status_code == 400


def test_create_campaign_missing_plan_is_404(client):
    r = client.post("/api/v1/plans/nope/campaigns", json={})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_campaign_history_and_full_shape(client):
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    _campaign(client, pid, term="2027/1")  # retroactive/extra campaign
    lst = client.get(f"/api/v1/plans/{pid}/campaigns").json()["campaigns"]
    assert [c["term"] for c in lst] == [TERM, "2027/1"]
    assert {"id", "term", "status", "counts", "requests_total"} <= set(lst[0])
    full = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}")
    assert full.status_code == 200
    assert {"rounds", "requests", "accepted_section_ids"} <= set(full.json())


def test_patch_campaign_close_has_no_side_effect(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    plan_before = client.get(f"/api/v1/plans/{pid}").json()["items"]
    r = client.patch(f"/api/v1/plans/{pid}/campaigns/{cid}",
                     json={"status": "encerrada", "note": "fim"})
    assert r.status_code == 200
    assert r.json()["status"] == "encerrada" and r.json()["note"] == "fim"
    assert client.get(f"/api/v1/plans/{pid}").json()["items"] == plan_before
    # reopening is allowed (status is UI organization only)
    r = client.patch(f"/api/v1/plans/{pid}/campaigns/{cid}", json={"status": "aberta"})
    assert r.json()["status"] == "aberta"


# ----- request creation (6.4) --------------------------------------------


def test_request_round_and_kind_validated_against_grade_rules(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _request(client, pid, cid, round="inexistente", course_code="22000236")
    assert r.status_code == 400
    assert "known_rounds" in r.json()["error"]["details"]
    # drop in a round with allows_drops=false
    r = _request(client, pid, cid, round="rematricula", kind="drop",
                 course_code="22000236")
    assert r.status_code == 400


def test_request_course_outside_grade_is_404(client):
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _request(client, pid, cid, round="correcao", course_code="99999999")
    assert r.status_code == 404


def test_request_section_of_other_course_or_term_is_400(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _request(client, pid, cid, round="correcao", course_code="22000236",
                 section_id=SEC_270_M1)
    assert r.status_code == 400


def test_request_duplicate_is_409(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _request(client, pid, cid, round="correcao", course_code="22000236",
                 section_id=SEC_236_M1)
    assert r.status_code == 201, r.text
    r = _request(client, pid, cid, round="correcao", course_code="22000236",
                 section_id=SEC_236_T1)  # same round+kind+course, other section
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_third_add_in_special_round_is_round_limit_exceeded(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    for code in ("22000236", "22000270"):
        r = _request(client, pid, cid, round="especial", course_code=code)
        assert r.status_code == 201, r.text
    r = _request(client, pid, cid, round="especial", course_code="22000273")
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "ROUND_LIMIT_EXCEEDED"
    assert err["details"] == {"round": "especial", "max_adds": 2, "current": 2}


def test_foreign_offer_in_own_course_round_warns_but_never_blocks(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _request(client, pid, cid, round="rematricula", course_code="22000299",
                 section_id=SEC_299_M1)
    assert r.status_code == 201, r.text  # NEVER blocks (spec 5.1)
    assert r.json()["warnings"], "scope mismatch must produce a warning"
    # same section in 'especial' (scope qualquer_curso) -> no warning
    r = _request(client, pid, cid, round="especial", course_code="22000299",
                 section_id=SEC_299_M1)
    assert r.status_code == 201 and r.json()["warnings"] == []


# ----- lifecycle + sync (5.2, ADR-017) ------------------------------------


def test_invalid_transition_is_409_with_details(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    assert _patch_req(client, pid, cid, rid, status="aceita").status_code == 200
    r = _patch_req(client, pid, cid, rid, status="pedida")  # terminal -> anything
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "INVALID_TRANSITION"
    assert err["details"] == {"from": "aceita", "to": "pedida", "allowed": []}
    # note stays editable on a terminal request (D4)
    r = _patch_req(client, pid, cid, rid, note="coordenador aprovou")
    assert r.status_code == 200
    assert r.json()["request"]["note"] == "coordenador aprovou"


def test_accept_add_syncs_plan_item_and_special_credits_count(client):
    _offer(client)
    pid = _new_plan(client, max_credits_per_term=4)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="pedida")
    r = _patch_req(client, pid, cid, rid, status="aceita")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_synced"] is True
    assert body["request"]["in_plan"] is True
    item = next(i for i in body["plan"]["items"] if i["course_code"] == "22000236")
    assert item["term"] == TERM and item["section_id"] == SEC_236_M1
    # second accept via the SPECIAL round: its credits count in the plan (D1)
    rid2 = _request(client, pid, cid, round="especial", course_code="22000270",
                    section_id=SEC_270_M1).json()["request"]["id"]
    r = _patch_req(client, pid, cid, rid2, status="aceita")
    body = r.json()
    overloads = [d for d in body["plan"]["diagnostics"]
                 if d["type"] == "TERM_OVERLOADED" and d["term"] == TERM]
    assert overloads, "special-round credits must trigger TERM_OVERLOADED (8 > 4)"
    assert overloads[0]["severity"] == "warning"  # D8: warning, never a block
    camp = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").json()
    assert camp["accepted_section_ids"] == sorted([SEC_236_M1, SEC_270_M1])


def test_accept_add_without_section_is_400(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao",
                   course_code="22000236").json()["request"]["id"]
    r = _patch_req(client, pid, cid, rid, status="aceita")
    assert r.status_code == 400
    # the failed accept must NOT leave the request accepted (single tx)
    camp = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").json()
    assert camp["requests"][0]["status"] == "desejada"


def test_accept_drop_clears_section_but_keeps_allocation(client):
    _offer(client)
    pid = _new_plan(client)
    r = client.put(f"/api/v1/plans/{pid}/items/22000236",
                   json={"term": TERM, "section_id": SEC_236_M1})
    assert r.status_code == 200, r.text
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao", kind="drop",
                   course_code="22000236", section_id=SEC_236_M1).json()["request"]["id"]
    r = _patch_req(client, pid, cid, rid, status="aceita")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_synced"] is True
    item = next(i for i in body["plan"]["items"] if i["course_code"] == "22000236")
    assert item["term"] == TERM        # allocation KEPT (never auto-removed)
    assert item["section_id"] is None  # chosen section cleared


def test_denied_request_never_touches_the_plan(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao", course_code="22000270",
                   section_id=SEC_270_M1).json()["request"]["id"]
    r = _patch_req(client, pid, cid, rid, status="negada")
    assert r.status_code == 200
    body = r.json()
    assert body["plan_synced"] is False
    assert "plan" not in body  # plan only present when synced (spec 6.4)
    items = client.get(f"/api/v1/plans/{pid}").json()["items"]
    assert all(i["course_code"] != "22000270" for i in items)


def test_denied_can_be_rerequested_in_next_round_same_section(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao", course_code="22000270",
                   section_id=SEC_270_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="negada")
    # D7: new request, SAME section, next round
    r = _request(client, pid, cid, round="especial", course_code="22000270",
                 section_id=SEC_270_M1)
    assert r.status_code == 201, r.text
    camp = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").json()
    assert len(camp["requests"]) == 2


def test_rescrape_removing_section_keeps_history_readable(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    _request(client, pid, cid, round="correcao", course_code="22000236",
             section_id=SEC_236_M1)
    # full-term replace WITHOUT 22000236 M1 (same path a re-scrape takes)
    _offer(client, sections=[
        {"course_code": "22000273", "label": "M1",
         "timeslots": [{"weekday": 3, "start": "08:00", "end": "10:00"}]},
    ])
    req = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").json()["requests"][0]
    assert req["section_alive"] is False       # derived: offer is gone
    assert req["section_id"] == SEC_236_M1     # id kept (no FK — ADR-017)
    assert req["section_label"] == "M1"        # snapshot survives the re-scrape


# ----- deletes -------------------------------------------------------------


def test_delete_campaign_with_accepted_request_is_409(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="aceita")
    r = client.delete(f"/api/v1/plans/{pid}/campaigns/{cid}")
    assert r.status_code == 409
    assert rid in r.json()["error"]["details"]["accepted_request_ids"]


def test_delete_campaign_without_accepted_is_204(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="negada")  # terminal but not aceita
    r = client.delete(f"/api/v1/plans/{pid}/campaigns/{cid}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").status_code == 404


def test_force_delete_campaign_with_accepted_keeps_plan_item(client):
    """?force=true (spec 6.3 rev.): drops the whole campaign log, accepted
    requests included — but plan_item is the source of truth (ADR-017) and
    MUST survive untouched."""
    from app.persistence.models import EnrollmentRequest
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="aceita")
    # without force the guard still holds (no regression)
    r = client.delete(f"/api/v1/plans/{pid}/campaigns/{cid}")
    assert r.status_code == 409
    assert r.json()["error"]["details"]["accepted_request_ids"] == [rid]
    # with force: 204, campaign and its requests are gone
    r = client.delete(f"/api/v1/plans/{pid}/campaigns/{cid}?force=true")
    assert r.status_code == 204
    assert client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").status_code == 404
    session = client.app.state.sessionmaker()
    try:
        assert session.query(EnrollmentRequest).filter_by(campaign_id=cid).count() == 0
    finally:
        session.close()
    # the allocation the accept created PERMANECE in the plan
    items = client.get(f"/api/v1/plans/{pid}").json()["items"]
    item = next(i for i in items if i["course_code"] == "22000236")
    assert item["term"] == TERM and item["section_id"] == SEC_236_M1


def test_force_delete_without_accepted_is_also_204(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    _request(client, pid, cid, round="correcao", course_code="22000236")
    r = client.delete(f"/api/v1/plans/{pid}/campaigns/{cid}?force=true")
    assert r.status_code == 204
    assert client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").status_code == 404


def test_delete_request_pending_204_terminal_409(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao",
                   course_code="22000236").json()["request"]["id"]
    r = client.delete(f"/api/v1/plans/{pid}/campaigns/{cid}/requests/{rid}")
    assert r.status_code == 204
    rid = _request(client, pid, cid, round="correcao", course_code="22000270",
                   section_id=SEC_270_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="aceita")
    r = client.delete(f"/api/v1/plans/{pid}/campaigns/{cid}/requests/{rid}")
    assert r.status_code == 409


def test_deleting_plan_cascades_campaigns_and_requests(client):
    from app.persistence.models import EnrollmentCampaign, EnrollmentRequest
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    _request(client, pid, cid, round="correcao", course_code="22000236")
    assert client.delete(f"/api/v1/plans/{pid}").status_code == 204
    session = client.app.state.sessionmaker()
    try:
        assert session.get(EnrollmentCampaign, cid) is None
        assert session.query(EnrollmentRequest).count() == 0
    finally:
        session.close()


# ----- criticality queue (6.5) ---------------------------------------------


def test_queue_blocked_last_shape_and_flags(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}/queue", params={"round": "correcao"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["term"] == TERM and body["round"] == "correcao"
    queue = body["queue"]
    codes = [e["course_code"] for e in queue]
    # every offered falta course of the grade appears exactly once
    assert set(codes) == {"22000236", "22000270", "22000273", "22000299"}
    assert [e["rank"] for e in queue] == [1, 2, 3, 4]
    by_code = {e["course_code"]: e for e in queue}
    # 22000273 is blocked (prereq 22000270 still falta) -> AFTER every released
    blocked = by_code["22000273"]
    assert blocked["blocked"] is True
    assert blocked["missing_prereqs"][0]["code"] == "22000270"
    assert blocked["missing_prereqs"][0]["status"] == "falta"
    first_blocked = next(i for i, e in enumerate(queue) if e["blocked"])
    assert all(e["blocked"] for e in queue[first_blocked:]), "blocked never outrank released"
    assert by_code["22000236"]["sections"] == [SEC_236_M1, SEC_236_T1]
    for e in queue:
        assert {"rank", "course_code", "name", "critical", "height", "unlocks",
                "credits", "difficulty", "blocked", "missing_prereqs", "sections",
                "already_accepted", "requested_in_round"} <= set(e)


def test_queue_marks_accepted_and_requested(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="aceita")
    _request(client, pid, cid, round="correcao", course_code="22000270",
             section_id=SEC_270_M1)
    q = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}/queue",
                   params={"round": "correcao"}).json()["queue"]
    by_code = {e["course_code"]: e for e in q}
    assert by_code["22000236"]["already_accepted"] is True
    assert by_code["22000236"]["requested_in_round"] is False  # other round
    assert by_code["22000270"]["requested_in_round"] is True


def test_queue_unknown_round_is_400(client):
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}/queue",
                   params={"round": "verao"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "BAD_REQUEST"


# ===== C2: swap-suggestions (6.6, spec 7.4) ================================
# Seed-derived values used below (BASE grade of the real seed, where
# 22000230+22000271 collapse into one MERGE node and the critical path is the
# TCC chain 22000199 -> 22000305 -> 22000306):
#   value = 100*critical + 10*(height-1) + 2*credits
#   22000236 -> 18 (height 2: unlocks the MERGE node), 22000270 -> 18
#   (height 2: unlocks 22000272/273), 22000273 -> 8, 22000299 -> 8.


def _swaps(client, pid, cid, **body):
    return client.post(
        f"/api/v1/plans/{pid}/campaigns/{cid}/swap-suggestions", json=body
    )


def test_swap_suggestions_missing_campaign_is_404(client):
    pid = _new_plan(client)
    r = _swaps(client, pid, "nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_swap_suggestions_no_offer_is_200_empty(client):
    # nothing to swap is a 200 with suggestions=[], never an error (6.6)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _swaps(client, pid, cid)
    assert r.status_code == 200, r.text
    assert r.json() == {"term": TERM, "held_sections": [],
                        "suggestions": [], "truncated": False}


def test_swap_suggestions_pure_adds_shape_and_ranking(client):
    _offer(client)
    _prepare_demo_progress(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _swaps(client, pid, cid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["term"] == TERM
    assert body["held_sections"] == []      # empty plan, no accepted requests
    assert body["truncated"] is False
    sugs = body["suggestions"]
    assert [s["rank"] for s in sugs] == list(range(1, len(sugs) + 1))
    assert len(sugs) == 5                   # default max_suggestions
    top = sugs[0]
    # best pure addition: the two most valuable courses (18 + 18)
    assert top["drop"] == [] and top["freed_slots"] == []
    assert {a["course_code"] for a in top["add"]} == {"22000236", "22000270"}
    assert top["score_delta"] == 36.0
    assert top["score_breakdown"] == {
        "gained_critical": 0, "gained_unlocks": 2, "gained_credits": 8,
        "lost_critical": 0, "lost_unlocks": 0, "lost_credits": 0,
    }
    for s in sugs:
        assert s["conflicts_after"] == []   # contract guarantee (6.6)
        assert s["score_delta"] > 0
    # max_suggestions caps the list
    assert len(_swaps(client, pid, cid, max_suggestions=2).json()["suggestions"]) == 2


def test_swap_suggestion_only_fits_thanks_to_the_drop(client):
    # held 22000273 (value 8) occupies the ONLY slot of 22000236 (value 18):
    # the add is valid solely because the drop frees the slot (two waves).
    _offer(client, sections=[
        {"course_code": "22000273", "label": "M1",
         "timeslots": [{"weekday": 0, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000236", "label": "M1",
         "timeslots": [{"weekday": 0, "start": "08:00", "end": "10:00"}]},
    ])
    pid = _new_plan(client)
    r = client.put(f"/api/v1/plans/{pid}/items/22000273",
                   json={"term": TERM, "section_id": SEC_273_M1})
    assert r.status_code == 200, r.text
    cid = _campaign(client, pid)["id"]
    body = _swaps(client, pid, cid).json()
    assert body["held_sections"] == [SEC_273_M1]
    assert len(body["suggestions"]) == 1    # the pure add conflicts -> only swap
    sug = body["suggestions"][0]
    assert sug["drop"] == [{"course_code": "22000273", "section_id": SEC_273_M1}]
    assert sug["add"] == [{"course_code": "22000236", "section_id": SEC_236_M1}]
    assert sug["score_delta"] == 10.0       # 18 - 8
    assert sug["freed_slots"] == [{"weekday": 0, "start": "08:00", "end": "10:00"}]
    assert sug["conflicts_after"] == []


def test_swap_held_unions_accepted_requests_and_plan_items(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="aceita")   # also syncs plan_item
    body = _swaps(client, pid, cid).json()
    assert body["held_sections"] == [SEC_236_M1]
    # held course never reappears as an add; everything suggested is clean
    for s in body["suggestions"]:
        assert all(a["course_code"] != "22000236" for a in s["add"])
        assert s["conflicts_after"] == []
    top = body["suggestions"][0]
    assert top["drop"] == []
    assert {a["course_code"] for a in top["add"]} == {"22000270", "22000273"}


# ===== C2: what-if (6.6, ADR-024) ==========================================


def _what_if(client, pid, cid, assume):
    return client.post(f"/api/v1/plans/{pid}/campaigns/{cid}/what-if",
                       json={"assume": assume})


def _plan_item_rows(client):
    from app.persistence.models import PlanItem
    session = client.app.state.sessionmaker()
    try:
        return [
            (i.plan_id, i.course_code, i.term, i.locked, i.section_id)
            for i in session.query(PlanItem)
            .order_by(PlanItem.plan_id, PlanItem.course_code)
        ]
    finally:
        session.close()


def test_what_if_projection_differs_and_db_is_untouched(client):
    _offer(client)
    pid = _new_plan(client, max_credits_per_term=4)
    cid = _campaign(client, pid)["id"]
    rid1 = _request(client, pid, cid, round="correcao", course_code="22000236",
                    section_id=SEC_236_M1).json()["request"]["id"]
    rid2 = _request(client, pid, cid, round="correcao", course_code="22000270",
                    section_id=SEC_270_M1).json()["request"]["id"]
    rows_before = _plan_item_rows(client)
    r = _what_if(client, pid, cid, [{"request_id": rid1, "outcome": "aceita"},
                                    {"request_id": rid2, "outcome": "aceita"}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["persisted"] is False
    base_terms = {t["term"]: t for t in body["baseline"]["term_summaries"]}
    proj_terms = {t["term"]: t for t in body["projection"]["term_summaries"]}
    assert TERM not in base_terms           # nothing allocated in 2026/2 today
    assert proj_terms[TERM]["credits"] == 8
    assert set(proj_terms[TERM]["course_codes"]) == {"22000236", "22000270"}
    # hypothetical overload appears ONLY in the projection (D8: warning)
    assert not any(d["type"] == "TERM_OVERLOADED" and d["term"] == TERM
                   for d in body["baseline"]["diagnostics"])
    over = [d for d in body["projection"]["diagnostics"]
            if d["type"] == "TERM_OVERLOADED" and d["term"] == TERM]
    assert over and over[0]["severity"] == "warning"
    # NOTHING persisted (ADR-024): plan_item rows identical, GET plan unchanged
    assert _plan_item_rows(client) == rows_before == []
    assert client.get(f"/api/v1/plans/{pid}").json()["items"] == []
    camp = client.get(f"/api/v1/plans/{pid}/campaigns/{cid}").json()
    assert all(req["status"] == "desejada" for req in camp["requests"])


def test_what_if_accepted_drop_removes_conflict_only_in_projection(client):
    _offer(client, sections=[
        {"course_code": "22000236", "label": "M1",
         "timeslots": [{"weekday": 0, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000270", "label": "M1",
         "timeslots": [{"weekday": 0, "start": "08:00", "end": "10:00"}]},
    ])
    pid = _new_plan(client)
    for code, sec in (("22000236", SEC_236_M1), ("22000270", SEC_270_M1)):
        assert client.put(f"/api/v1/plans/{pid}/items/{code}",
                          json={"term": TERM, "section_id": sec}).status_code == 200
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao", kind="drop",
                   course_code="22000270", section_id=SEC_270_M1).json()["request"]["id"]
    rows_before = _plan_item_rows(client)
    body = _what_if(client, pid, cid, [{"request_id": rid, "outcome": "aceita"}]).json()
    assert any(d["type"] == "SCHEDULE_CONFLICT" for d in body["baseline"]["diagnostics"])
    assert not any(d["type"] == "SCHEDULE_CONFLICT"
                   for d in body["projection"]["diagnostics"])
    # allocation is KEPT in the projection (drop clears the section only)
    proj_terms = {t["term"]: t for t in body["projection"]["term_summaries"]}
    assert "22000270" in proj_terms[TERM]["course_codes"]
    # and the real plan still has the section chosen (nothing persisted)
    assert _plan_item_rows(client) == rows_before
    item = next(i for i in client.get(f"/api/v1/plans/{pid}").json()["items"]
                if i["course_code"] == "22000270")
    assert item["section_id"] == SEC_270_M1


def test_what_if_request_of_another_campaign_is_400(client):
    _offer(client)
    pid = _new_plan(client)
    cid1 = _campaign(client, pid)["id"]
    cid2 = _campaign(client, pid, term="2027/1")["id"]
    rid = _request(client, pid, cid2, round="correcao",
                   course_code="22000236").json()["request"]["id"]
    r = _what_if(client, pid, cid1, [{"request_id": rid, "outcome": "aceita"}])
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "BAD_REQUEST"
    assert err["details"] == {"request_id": rid}


def test_what_if_invalid_outcome_is_400(client):
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="correcao",
                   course_code="22000236").json()["request"]["id"]
    r = _what_if(client, pid, cid, [{"request_id": rid, "outcome": "talvez"}])
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "BAD_REQUEST"
    assert err["details"]["allowed"] == ["aceita", "negada"]


def test_what_if_terminal_request_can_be_reassumed(client):
    # a request already 'aceita' for real can be re-assumed freely (ADR-024);
    # assuming 'negada' means "no effect" — it never un-does the real accept.
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="aceita")
    r = _what_if(client, pid, cid, [{"request_id": rid, "outcome": "negada"}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["persisted"] is False
    assert body["projection"] == body["baseline"]  # negada => nada
    # empty assume is also a valid (identity) simulation
    r = _what_if(client, pid, cid, [])
    assert r.status_code == 200
    assert r.json()["projection"] == r.json()["baseline"]


# ===== C3: special-candidates (6.7, spec 7.3) ==============================
# Seed-derived impact values (same BASE-grade cast as the C2 block above):
#   22000236 -> 18, 22000270 -> 18, 22000273 -> 8, 22000299 -> 8.
# The default _offer has no capacity/enrolled (vacancy neutral, D5) and no
# schedule overlaps, so with no accepted request every fit is +20:
#   236 = 38, 270 = 38, 299 = 18 - 10 (foreign) = 8... see each test.


def _special(client, pid, cid):
    return client.get(f"/api/v1/plans/{pid}/campaigns/{cid}/special-candidates")


def test_special_candidates_missing_campaign_is_404(client):
    pid = _new_plan(client)
    r = _special(client, pid, "nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_special_candidates_no_offer_is_200_empty(client):
    # no offer in the term: 200 with candidates=[] (front points to the
    # ScrapeButton), max_choices still comes from the grade rule
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _special(client, pid, cid)
    assert r.status_code == 200, r.text
    assert r.json() == {"term": TERM, "max_choices": 2,
                        "candidates": [], "rescrape_hint": None}


def test_special_candidates_shape_ranking_and_flags(client):
    _offer(client)
    _prepare_demo_progress(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    r = _special(client, pid, cid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["term"] == TERM
    assert body["max_choices"] == 2  # from grade_version.enrollment_rules
    cands = body["candidates"]
    # scores: 236 = 18+20 = 38; 270 = 18+20 = 38 (code breaks the tie);
    # 299 = 8+20-10 (foreign) = 18; 273 = 8+20-50 (blocked) = -22.
    assert [c["course_code"] for c in cands] == [
        "22000236", "22000270", "22000299", "22000273"
    ]
    assert [c["rank"] for c in cands] == [1, 2, 3, 4]
    assert [c["score"] for c in cands] == [38.0, 38.0, 18.0, -22.0]
    for c in cands:
        assert {"rank", "course_code", "name", "section_id", "section_label",
                "score", "score_breakdown", "blocked", "missing_prereqs",
                "capacity", "enrolled", "full", "foreign_offer", "offered_to",
                "conflicts_with", "alternatives"} <= set(c)
        assert c["capacity"] is None and c["enrolled"] is None  # level-2 data
        assert c["full"] is False                               # None = neutral (D5)
        assert c["conflicts_with"] == []                        # nothing accepted yet
    by_code = {c["course_code"]: c for c in cands}
    # best section of 236: M1/T1 tie on (fit+vacancy) -> smallest id wins
    c236 = by_code["22000236"]
    assert c236["section_id"] == SEC_236_M1 and c236["section_label"] == "M1"
    assert c236["alternatives"] == [SEC_236_T1]
    assert c236["score_breakdown"] == {
        "critical": 0, "unlocks": 10, "credits": 8,
        "fit": 20, "vacancy": 0, "full": 0, "blocked": 0, "foreign": 0,
    }
    # foreign offer (A3/ADR-020): read from the structured column, never note
    c299 = by_code["22000299"]
    assert c299["foreign_offer"] is True
    assert c299["offered_to"] == ["Ciência da Computação"]
    assert c299["score_breakdown"]["foreign"] == -10
    # own-course sections are legitimate candidates with no penalty (D2)
    assert by_code["22000236"]["foreign_offer"] is False
    # blocked is flagged with reasons and NEVER excluded (D6 badge)
    c273 = by_code["22000273"]
    assert c273["blocked"] is True
    assert c273["score_breakdown"]["blocked"] == -50
    assert c273["missing_prereqs"] == [
        {"code": "22000270", "name": "Sistemas Operacionais", "status": "falta"}
    ]
    assert body["rescrape_hint"].startswith("oferta coletada em ")


def test_special_full_section_penalized_never_excluded(client):
    # D5: 22000236 M1 full (30/30) vs T1 with vacancy -> best is T1 (+10 beats
    # -25), the full section survives as alternative; a course whose ONLY
    # section is full stays in the list, flagged.
    _offer(client, sections=[
        {"course_code": "22000236", "label": "M1", "capacity": 30, "enrolled": 30,
         "timeslots": [{"weekday": 0, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000236", "label": "T1", "capacity": 30, "enrolled": 10,
         "timeslots": [{"weekday": 1, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000299", "label": "M1", "capacity": 40, "enrolled": 45,
         "timeslots": [{"weekday": 4, "start": "08:00", "end": "10:00"}]},
    ])
    _prepare_demo_progress(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    cands = _special(client, pid, cid).json()["candidates"]
    by_code = {c["course_code"]: c for c in cands}
    c236 = by_code["22000236"]
    assert c236["section_id"] == SEC_236_T1
    assert c236["alternatives"] == [SEC_236_M1]
    assert c236["full"] is False and c236["score_breakdown"]["vacancy"] == 10
    c299 = by_code["22000299"]           # only section is full -> still listed
    assert c299["full"] is True
    assert c299["capacity"] == 40 and c299["enrolled"] == 45
    assert c299["score_breakdown"]["full"] == -25
    assert c299["score_breakdown"]["vacancy"] == 0
    assert c299["score"] == 8 + 20 - 25  # impact + fit - full (no offered_to here)


def test_special_conflicts_with_accepted_sections(client):
    # accepted request occupies seg 08-10; 22000270's only section overlaps it
    # -> fit -60 with the conflicting id listed (transparency for the UI)
    _offer(client, sections=[
        {"course_code": "22000236", "label": "M1",
         "timeslots": [{"weekday": 0, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000270", "label": "M1",
         "timeslots": [{"weekday": 0, "start": "09:00", "end": "11:00"}]},
    ])
    _prepare_demo_progress(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    rid = _request(client, pid, cid, round="rematricula", course_code="22000236",
                   section_id=SEC_236_M1).json()["request"]["id"]
    _patch_req(client, pid, cid, rid, status="aceita")
    cands = _special(client, pid, cid).json()["candidates"]
    by_code = {c["course_code"]: c for c in cands}
    c270 = by_code["22000270"]
    assert c270["conflicts_with"] == [SEC_236_M1]
    assert c270["score_breakdown"]["fit"] == -60
    assert c270["score"] == 18 - 60


def test_special_blocked_never_rank1_with_released_alternative(client):
    # only 22000273 (blocked, impact 8) and 22000299 (released, impact 8 but
    # foreign -10) offered: raw scores 273 = -22, 299 = 8 -> 299 already first.
    # Force the interesting case: give 273 a vacancy bonus and make 299 full,
    # keeping 273's raw score below anyway (blocked -50 dominates) — then
    # check the general invariant: rank 1 is never blocked here (D6).
    _offer(client, sections=[
        {"course_code": "22000273", "label": "M1", "capacity": 40, "enrolled": 10,
         "timeslots": [{"weekday": 3, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000299", "label": "M1", "capacity": 40, "enrolled": 45,
         "offered_to": ["Ciência da Computação"],
         "timeslots": [{"weekday": 4, "start": "08:00", "end": "10:00"}]},
    ])
    _prepare_demo_progress(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    cands = _special(client, pid, cid).json()["candidates"]
    assert len(cands) == 2
    assert cands[0]["blocked"] is False, "a blocked candidate must never be rank 1"
    assert cands[0]["course_code"] == "22000299"
    assert cands[1]["course_code"] == "22000273" and cands[1]["blocked"] is True


def test_special_max_choices_comes_from_grade_rule_not_literal(client):
    # DoD: max_choices is DATA (ADR-018) — change the grade rule to 3 and the
    # endpoint must follow, no code change, no literal 2 anywhere
    import json as _json
    from app.persistence.models import GradeVersion, Plan
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    session = client.app.state.sessionmaker()
    try:
        grade = session.get(GradeVersion, session.get(Plan, pid).grade_version_id)
        rules = _json.loads(grade.enrollment_rules)
        for rnd in rules["rodadas"]:
            if rnd["key"] == "especial":
                rnd["max_adds"] = 3
        grade.enrollment_rules = _json.dumps(rules, ensure_ascii=False)
        session.commit()
    finally:
        session.close()
    r = _special(client, pid, cid)
    assert r.status_code == 200, r.text
    assert r.json()["max_choices"] == 3


def test_special_rescrape_hint_uses_oldest_fetched_at(client):
    from app.persistence.models import Section
    _offer(client)
    pid = _new_plan(client)
    cid = _campaign(client, pid)["id"]
    session = client.app.state.sessionmaker()
    try:
        sec = session.get(Section, SEC_270_M1)
        sec.fetched_at = "2026-07-01T08:00:00+00:00"  # older than the others
        session.commit()
    finally:
        session.close()
    hint = _special(client, pid, cid).json()["rescrape_hint"]
    assert hint == "oferta coletada em 2026-07-01 — atualize antes de decidir"
