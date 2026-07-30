"""API contract tests for v3 features:
- ADR-010: current_term validation (offending items in the past -> 400)
- ADR-011/016: per-plan scrape, elective discovery, scoped import
- sync invariant: a bare flowchart allocation never carries a chosen section
  (so it can never populate the Horários week grid — Fluxograma -> Horários off).

The scrape endpoint is exercised OFFLINE by monkeypatching ``scrape_offers`` to
run the real orchestration against the saved course-page fixture.
"""
from __future__ import annotations

from pathlib import Path

import app.api.sections as sections_mod
from app.scrapers.ufpel import scrape_offers as real_scrape_offers
from app.scrapers.ufpel.fetch import CURSO_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")


def _new_plan(client, current_term: str) -> str:
    r = client.post("/api/v1/plans", json={"name": f"v3 {current_term}", "current_term": current_term})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ----- ADR-010: current_term validation --------------------------------


def test_patch_current_term_with_item_in_past_is_400(client):
    pid = _new_plan(client, "2026/1")
    client.put(f"/api/v1/plans/{pid}/items/22000299", json={"term": "2026/1"})
    r = client.patch(f"/api/v1/plans/{pid}", json={"current_term": "2027/1"})
    assert r.status_code == 400
    details = r.json()["error"]["details"]
    offending = {i["course_code"] for i in details["offending_items"]}
    assert "22000299" in offending


def test_patch_current_term_valid_moves_forward(client):
    pid = _new_plan(client, "2026/1")
    client.put(f"/api/v1/plans/{pid}/items/22000299", json={"term": "2026/2"})
    r = client.patch(f"/api/v1/plans/{pid}", json={"current_term": "2026/2"})
    assert r.status_code == 200
    assert r.json()["current_term"] == "2026/2"


# ----- sync invariant (Fluxograma -> Horários off) ---------------------


def test_bare_allocation_has_no_section(client):
    pid = _new_plan(client, "2026/1")
    r = client.put(f"/api/v1/plans/{pid}/items/22000299", json={"term": "2026/1"})
    assert r.status_code == 200
    item = next(i for i in client.get(f"/api/v1/plans/{pid}").json()["items"]
                if i["course_code"] == "22000299")
    # dragging on the flowchart never selects a turma -> not renderable in the grid
    assert item["section_id"] is None


# ----- ADR-011/016: scrape, discovery, scoped import -------------------


def _patch_scrape(monkeypatch):
    """scrape_offers runs for real but fetches only the course-page fixture."""
    def fake_fetch(url: str) -> str:
        if url == CURSO_URL.format(code="3910"):
            return CURSO_HTML
        raise RuntimeError("no network in tests")

    def wrapped(portal, term, wanted, **kw):
        kw.pop("fetch", None)
        return real_scrape_offers(portal, term, wanted, sleep=0, fetch=fake_fetch, **kw)

    monkeypatch.setattr(sections_mod, "scrape_offers", wrapped)


def test_scrape_discovers_optativas_and_preserves_out_of_scope(client, monkeypatch):
    _patch_scrape(monkeypatch)
    # target term = next_term(2025/2) = 2026/1, matching the fixture's course page
    pid = _new_plan(client, "2025/2")

    # a manually imported section of an out-of-scope course must survive the scrape
    client.patch("/api/v1/courses/11100058/state", json={"status": "aprovada"})
    client.put("/api/v1/admin/terms/2026-1/sections", json={"sections": [
        {"course_code": "11100058", "label": "MAN", "capacity": 5, "enrolled": 1,
         "timeslots": [{"weekday": 4, "start": "19:00", "end": "21:00"}]},
    ]})

    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["term"] == "2026/1"
    assert body["level_used"] == 1
    disc_codes = {d["code"] for d in body["discovered_optativas"]}
    # 22000254 (Projetos em Computação II) is an elective on the course page but
    # not part of the 2019/1-derived grade -> discovered
    assert "22000254" in disc_codes
    assert all(d["name"] for d in body["discovered_optativas"])

    g = client.get("/api/v1/terms/2026-1/sections").json()
    by_code = {s["course_code"]: s for s in g["sections"]}
    # out-of-scope manual section preserved (scoped import, ADR-013)
    assert "11100058" in by_code and by_code["11100058"]["label"] == "MAN"
    # discovered elective is stored, tagged, and carries its course name
    elective = by_code["22000254"]
    assert elective["source"] == "scraper:ufpel-optativa"
    assert elective["course_name"]


def test_discovered_optativa_not_in_curriculum(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _new_plan(client, "2025/2")
    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    disc_codes = {d["code"] for d in r.json()["discovered_optativas"]}
    disc = next(iter(disc_codes))
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    curric = client.get(f"/api/v1/grade-versions/{gv}/curriculum").json()
    codes = {c["code"] for c in curric["courses"]}
    # materialized Course exists but is NOT part of the plan's grade (no flowchart node)
    assert disc not in codes


def test_grade_course_never_discovered_as_optativa(client, monkeypatch):
    # ADR-016: a curriculum course is NEVER a "discovered" elective, whatever its
    # status. 22000301 (FIA) is in the grade AND under the portal's Optativas
    # accordion; with status "pendente" it is neither wanted (falta) nor done —
    # it must still be excluded from discovery.
    _patch_scrape(monkeypatch)
    pid = _new_plan(client, "2025/2")
    client.patch("/api/v1/courses/22000301/state", json={"status": "pendente"})
    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert r.status_code == 200, r.text
    disc = {d["code"] for d in r.json()["discovered_optativas"]}
    assert "22000301" not in disc


def test_put_item_on_discovered_optativa_is_404(client, monkeypatch):
    # ADR-016: discovered electives are outside the grade -> cannot be allocated.
    _patch_scrape(monkeypatch)
    pid = _new_plan(client, "2025/2")
    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    disc_codes = sorted(d["code"] for d in r.json()["discovered_optativas"])
    assert disc_codes, "fixture should discover at least one elective"
    r2 = client.put(f"/api/v1/plans/{pid}/items/{disc_codes[0]}", json={"term": "2026/1"})
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "NOT_FOUND"


def test_scrape_missing_portal_code_is_400(client, monkeypatch):
    _patch_scrape(monkeypatch)
    from app.persistence.models import GradeVersion, Meta
    # ADR-018: the portal code lives on grade_version with meta k/v as fallback.
    # Clear BOTH to simulate a seed without meta.codigo_curso.
    session = client.app.state.sessionmaker()
    try:
        row = session.get(Meta, "portal_course_code")
        if row is not None:
            session.delete(row)
        for gv in session.query(GradeVersion).all():
            gv.portal_course_code = None
        session.commit()
    finally:
        session.close()
    pid = _new_plan(client, "2025/2")
    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "PORTAL_CODE_MISSING"


# ----- A2 (ADR-021): eligibility endpoint ------------------------------


def test_eligibility_missing_plan_is_404(client):
    r = client.get("/api/v1/plans/nope/terms/2026-2/eligibility")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_eligibility_shape_and_only_falta_courses(client):
    pid = _new_plan(client, "2026/1")
    r = client.get(f"/api/v1/plans/{pid}/terms/2026-2/eligibility")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["term"] == "2026/2"
    assert isinstance(body["courses"], list) and body["courses"]
    for c in body["courses"]:
        assert set(c) == {"code", "eligible", "missing_prereqs"}
        assert isinstance(c["eligible"], bool)
        for mp in c["missing_prereqs"]:
            assert set(mp) == {"code", "name", "status"}
