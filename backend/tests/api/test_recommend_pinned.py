"""API contract tests for v5 F2a: pinned_courses on POST /plans/{id}/recommend-schedule."""
from __future__ import annotations


def _plan(client):
    return client.get("/api/v1/plans").json()["plans"][0]


def _import_sections(client, term="2026-2"):
    payload = {"sections": [
        {"course_code": "22000273", "label": "M1", "capacity": 40, "enrolled": 10,
         "timeslots": [{"weekday": 1, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000273", "label": "M2", "capacity": 40, "enrolled": 10,
         "timeslots": [{"weekday": 2, "start": "08:00", "end": "10:00"}]},
        {"course_code": "22000270", "label": "T1", "capacity": 40, "enrolled": 5,
         "timeslots": [{"weekday": 1, "start": "08:00", "end": "10:00"}]},
    ]}
    r = client.put(f"/api/v1/admin/terms/{term}/sections", json=payload)
    assert r.status_code == 200


def _prepare_demo_progress(client):
    """Release the recommendation scenario without relying on seed history."""
    for code in ("22000182", "22000297"):
        response = client.patch(f"/api/v1/courses/{code}/state", json={"status": "cursando"})
        assert response.status_code == 200, response.text


def test_without_pinned_response_is_the_legacy_shape(client):
    # anti-regression (ADR-029): absent/empty pinned_courses => the exact
    # pre-v5 recommend_schedule path — no pinned keys in the response.
    _import_sections(client)
    _prepare_demo_progress(client)
    p = _plan(client)
    r = client.post(
        f"/api/v1/plans/{p['id']}/recommend-schedule", json={"term": "2026/2"}
    )
    assert r.status_code == 200
    body = r.json()
    assert "pinned" not in body and "pinned_infeasible" not in body
    assert body["recommendations"]


def test_pinned_course_dispatches_and_appears_in_top_recommendation(client):
    _import_sections(client)
    p = _plan(client)
    r = client.post(
        f"/api/v1/plans/{p['id']}/recommend-schedule",
        json={"term": "2026/2", "pinned_courses": ["22000273"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pinned_infeasible"] is False
    pin = next(x for x in body["pinned"] if x["course_code"] == "22000273")
    assert pin["status"] in ("ok", "bloqueada")
    assert pin["section_id"] in ("2026-2:22000273:M1", "2026-2:22000273:M2")
    top = body["recommendations"][0]
    assert "22000273" in {c["course_code"] for c in top["choices"]}


def test_pinned_combined_with_locked_choices(client):
    _import_sections(client)
    p = _plan(client)
    r = client.post(
        f"/api/v1/plans/{p['id']}/recommend-schedule",
        json={
            "term": "2026/2",
            "locked_choices": ["2026-2:22000270:T1"],  # clashes with 273 M1
            "pinned_courses": ["22000273"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    top = body["recommendations"][0]
    ids = {c["section_id"] for c in top["choices"]}
    assert "2026-2:22000270:T1" in ids  # the locked SECTION stays
    assert "2026-2:22000273:M2" in ids  # the engine picked the free section


def test_pinned_code_outside_grade_is_400_with_unknown_details(client):
    _import_sections(client)
    p = _plan(client)
    r = client.post(
        f"/api/v1/plans/{p['id']}/recommend-schedule",
        json={"term": "2026/2", "pinned_courses": ["99999999"]},
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "BAD_REQUEST"
    assert err["details"]["unknown"] == ["99999999"]


def test_more_than_six_pinned_is_400(client):
    _import_sections(client)
    p = _plan(client)
    codes = [f"C{i}" for i in range(7)]
    r = client.post(
        f"/api/v1/plans/{p['id']}/recommend-schedule",
        json={"term": "2026/2", "pinned_courses": codes},
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["details"] == {"max_pinned": 6, "given": 7}


def test_pinned_on_term_without_data_is_still_400(client):
    p = _plan(client)
    r = client.post(
        f"/api/v1/plans/{p['id']}/recommend-schedule",
        json={"term": "2099/1", "pinned_courses": ["22000273"]},
    )
    assert r.status_code == 400
