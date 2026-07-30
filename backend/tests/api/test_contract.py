"""API contract tests via TestClient against a throwaway DB (section 6)."""
from __future__ import annotations

import json
from pathlib import Path


def _first_plan(client):
    plans = client.get("/api/v1/plans").json()["plans"]
    assert plans, "seed should bootstrap an initial plan"
    return plans[0]


def test_health_and_seed_import(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["seed_hash"]


def test_fresh_boot_is_anonymous_demo(client):
    plans = client.get("/api/v1/plans").json()["plans"]
    assert len(plans) == 1
    assert plans[0]["name"] == "Meu plano"

    curriculum = client.get("/api/v1/grade-versions/gv-2027-1/curriculum").json()
    assert curriculum["courses"]
    assert all(course["status"] == "falta" for course in curriculum["courses"])

    root = Path(__file__).resolve().parents[3]
    seed = json.loads((root / "seed" / "curriculum.json").read_text(encoding="utf-8"))
    assert "aluno" not in seed["meta"]
    assert all("status" not in course and "nota" not in course for course in seed["courses"])
    assert not list((root / "backend" / "tests").rglob("*.pdf"))


def test_grade_versions_default_is_2027(client):
    gvs = client.get("/api/v1/grade-versions").json()["grade_versions"]
    by_id = {g["id"]: g for g in gvs}
    assert by_id["gv-2027-1"]["is_default"] is True
    assert by_id["gv-2027-1"]["transition_policy"] == "optional"
    assert by_id["gv-2019-1"]["is_base"] is True


def test_reform_materialization_2027(client):
    data = client.get("/api/v1/grade-versions/gv-2027-1/curriculum").json()
    by_code = {c["code"]: c for c in data["courses"]}
    # PS became optativa, FIA became obrigatoria (swap)
    assert by_code["22000237"]["kind"] == "optativa"
    assert by_code["22000301"]["kind"] == "obrigatoria"
    # EB I/II merged away
    assert "22000230" not in by_code and "22000271" not in by_code
    merged = [c for c in by_code if "MERGE" in c]
    assert any("22000230" in m for m in merged)


def test_patch_course_state_persists_offer_and_status(client):
    r = client.patch("/api/v1/courses/22000299/state", json={"status": "aprovada", "offer": "/1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "aprovada"
    assert body["offer"] == "/1" and body["offer_source"] == "user"
    # reset offer to seed default
    r2 = client.patch("/api/v1/courses/22000299/state", json={"offer": None})
    assert r2.json()["offer_source"] == "seed"


def test_patch_unknown_course_is_404(client):
    assert client.patch("/api/v1/courses/00000000/state", json={"status": "falta"}).status_code == 404


def test_put_item_accepts_invalid_and_returns_diagnostics(client):
    plan = _first_plan(client)
    pid = plan["id"]
    # allocate SO (22000270) at current term: its prereqs are not done -> prereq violation, still 200
    r = client.put(f"/api/v1/plans/{pid}/items/22000270", json={"term": plan["current_term"]})
    assert r.status_code == 200
    assert "item" in r.json() and "diagnostics" in r.json()


def test_put_item_before_current_term_is_400(client):
    plan = _first_plan(client)
    r = client.put(f"/api/v1/plans/{plan['id']}/items/22000270", json={"term": "2020/1"})
    assert r.status_code == 400


def test_delete_locked_item_is_409(client):
    plan = _first_plan(client)
    pid = plan["id"]
    client.put(f"/api/v1/plans/{pid}/items/22000270", json={"term": "2027/1"})
    client.post(f"/api/v1/plans/{pid}/items/22000270/lock")
    assert client.delete(f"/api/v1/plans/{pid}/items/22000270").status_code == 409
    client.post(f"/api/v1/plans/{pid}/items/22000270/unlock")
    assert client.delete(f"/api/v1/plans/{pid}/items/22000270").status_code == 204


def test_create_plan_defaults_to_default_grade(client):
    r = client.post("/api/v1/plans", json={"name": "sem grade", "current_term": "2026/2"})
    assert r.status_code == 201
    assert r.json()["grade_version_id"] == "gv-2027-1"


def test_validate_returns_critical_path(client):
    plan = _first_plan(client)
    v = client.post(f"/api/v1/plans/{plan['id']}/validate").json()
    assert "critical_path" in v and v["critical_path"]["length_terms"] >= 1
    assert "blocked_codes" in v


def test_autoplan_requires_a_target(client):
    plan = _first_plan(client)
    r = client.post(f"/api/v1/plans/{plan['id']}/autoplan", json={"mode": "preview"})
    assert r.status_code == 400


def test_autoplan_apply_persists_proposal(client):
    plan = _first_plan(client)
    pid = plan["id"]
    r = client.post(f"/api/v1/plans/{pid}/autoplan", json={"target_term": "2030/1", "mode": "apply"})
    body = r.json()
    assert body["applied"] is True
    got = client.get(f"/api/v1/plans/{pid}").json()
    assert len(got["items"]) == len(body["proposal"]) > 0


def test_grade_switch_translates_and_notes(client):
    plan = _first_plan(client)
    pid = plan["id"]
    r = client.patch(f"/api/v1/plans/{pid}", json={"grade_version_id": "gv-2019-1"})
    assert r.status_code == 200
    assert r.json()["grade_version_id"] == "gv-2019-1"


def test_requirements_progress_and_entries(client):
    cats = client.get("/api/v1/requirements").json()["categories"]
    keys = {c["key"] for c in cats}
    assert {"complementares", "optativas", "livres"} <= keys
    r = client.post("/api/v1/requirements/complementares/entries",
                    json={"description": "Monitoria", "hours": 100})
    assert r.status_code == 201
    eid = r.json()["id"]
    after = client.get("/api/v1/requirements").json()["categories"]
    compl = next(c for c in after if c["key"] == "complementares")
    assert compl["logged_hours"] == 100
    assert client.delete(f"/api/v1/requirements/entries/{eid}").status_code == 204


def test_export_dump(client):
    ex = client.get("/api/v1/export").json()
    assert "course_state" in ex and "plans" in ex and "requirement_entries" in ex
