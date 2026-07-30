"""v5 F1b (ADR-028) — semester commit materializes catalog electives.

Central flow: scrape populates the elective_catalog; "selecionar semestre"
(`POST /plans/{id}/terms/{term}/commit`) turns a chosen catalog elective into a
real flowchart node (elective_adoption + plan_item), which the engine then sees
and whose hours count toward the 226.6h. Plus the regression that the v0.1
"single injection point" premise missed: a materialized elective must be
draggable (`PUT /items` -> 200, not 404).
"""
from __future__ import annotations

from pathlib import Path

import app.api.sections as sections_mod
from app.scrapers.ufpel import scrape_offers as real_scrape_offers
from app.scrapers.ufpel.fetch import CURSO_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")

# optativa in the matrix, NOT in the 2027/1 grade (a genuine materialization)
ELECTIVE = "22000279"       # MICROCONTROLADORES — optativa, 60h
ELECTIVE_HOURS = 60.0
OTHER_ELECTIVE = "22000282"  # ROBÓTICA — optativa, not materialized (contraprova)
TERM = "2026/1"              # == default plan current_term (body/response)
TERM_PATH = "2026-1"         # dash form for URL paths (endpoint normalizes)


def _patch_scrape(monkeypatch):
    def fake_fetch(url: str) -> str:
        if url == CURSO_URL.format(code="3910"):
            return CURSO_HTML
        raise RuntimeError("no network in tests")

    def wrapped(portal, term, wanted, **kw):
        kw.pop("fetch", None)
        return real_scrape_offers(portal, term, wanted, sleep=0, fetch=fake_fetch, **kw)

    monkeypatch.setattr(sections_mod, "scrape_offers", wrapped)


def _plan(client) -> str:
    return client.get("/api/v1/plans").json()["plans"][0]["id"]


def _scrape(client, pid):
    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert r.status_code == 200, r.text


def _curriculum_codes(client, gv: str) -> set[str]:
    return {c["code"] for c in client.get(f"/api/v1/grade-versions/{gv}/curriculum").json()["courses"]}


def _optativas_total(client, pid: str) -> float:
    cats = client.get(f"/api/v1/requirements?plan_id={pid}").json()["categories"]
    cat = next(c for c in cats if c["key"] == "optativas")
    return round(cat["total_hours"], 2)


# ===== central commit flow ==================================================


def test_commit_materializes_elective_end_to_end(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    _scrape(client, pid)

    assert ELECTIVE not in _curriculum_codes(client, gv)  # only in catalog, not the grade
    before_hours = _optativas_total(client, pid)

    r = client.post(
        f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
        json={"choices": [{"course_code": ELECTIVE, "section_id": None}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["committed"]["adopted"] == [ELECTIVE]
    assert ELECTIVE in body["committed"]["allocated"]

    # now a real node in the flowchart, kind optativa
    codes_after = client.get(f"/api/v1/grade-versions/{gv}/curriculum").json()["courses"]
    node = next((c for c in codes_after if c["code"] == ELECTIVE), None)
    assert node is not None and node["kind"] == "optativa"

    # allocated in the plan at the term
    item = next(i for i in body["plan"]["items"] if i["course_code"] == ELECTIVE)
    assert item["term"] == TERM

    # its hours now count toward the 226.6h
    assert _optativas_total(client, pid) == round(before_hours + ELECTIVE_HOURS, 2)


def test_commit_is_idempotent(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    _scrape(client, pid)
    client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
                json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})
    r2 = client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
                     json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})
    assert r2.status_code == 200
    c = r2.json()["committed"]
    assert c["adopted"] == [] and c["allocated"] == [] and c["unchanged"] == [ELECTIVE]


def test_commit_unknown_code_is_400(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    _scrape(client, pid)
    r = client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
                    json={"choices": [{"course_code": "00000000", "section_id": None}]})
    assert r.status_code == 400
    assert "00000000" in r.json()["error"]["details"]["unknown"]


def test_non_materialized_elective_stays_out_of_curriculum(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    _scrape(client, pid)
    client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
                json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})
    codes = _curriculum_codes(client, gv)
    assert ELECTIVE in codes            # committed one is in
    assert OTHER_ELECTIVE not in codes  # the other 46 stay out of the motor


# ===== regression: the materialized elective is DRAGGABLE (plans.py:196) =====


def test_materialized_elective_is_draggable(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    _scrape(client, pid)
    client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
                json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})
    # drag to another term -> 200, NOT 404 "não pertence à grade do plano"
    r = client.put(f"/api/v1/plans/{pid}/items/{ELECTIVE}", json={"term": "2026/2"})
    assert r.status_code == 200, r.text
    item = next(i for i in client.get(f"/api/v1/plans/{pid}").json()["items"]
                if i["course_code"] == ELECTIVE)
    assert item["term"] == "2026/2"


def test_non_materialized_elective_is_not_draggable(client, monkeypatch):
    # contraproof: the guard still guards — a catalog elective not committed 404s
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    _scrape(client, pid)
    r = client.put(f"/api/v1/plans/{pid}/items/{OTHER_ELECTIVE}", json={"term": "2026/2"})
    assert r.status_code == 404


# ===== de-materialize (DELETE /electives/{code}) ============================


def test_drop_materialized_elective_flow(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    _scrape(client, pid)
    client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
                json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})

    # allocated -> DELETE is 409 (unallocate first)
    r409 = client.delete(f"/api/v1/plans/{pid}/electives/{ELECTIVE}")
    assert r409.status_code == 409
    assert r409.json()["error"]["details"]["reason"] == "allocated"

    # unallocate in the flowchart, then DELETE -> 204 and it leaves the curriculum
    client.delete(f"/api/v1/plans/{pid}/items/{ELECTIVE}")
    r204 = client.delete(f"/api/v1/plans/{pid}/electives/{ELECTIVE}")
    assert r204.status_code == 204
    assert ELECTIVE not in _curriculum_codes(client, gv)


def test_drop_unknown_elective_is_404(client):
    pid = _plan(client)
    r = client.delete(f"/api/v1/plans/{pid}/electives/22000279")
    assert r.status_code == 404


# ===== read-only catalog (GET /electives) ===================================


def test_list_electives_shape(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _plan(client)
    _scrape(client, pid)
    r = client.get(f"/api/v1/plans/{pid}/electives?term={TERM_PATH}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["fetched_at"] is not None
    by_code = {e["code"]: e for e in data["electives"]}
    assert ELECTIVE in by_code
    e = by_code[ELECTIVE]
    assert e["kind"] == "optativa" and e["hours"] == ELECTIVE_HOURS
    assert e["adopted"] is False
    # after commit it flips to adopted and sorts first
    client.post(f"/api/v1/plans/{pid}/terms/{TERM_PATH}/commit",
                json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})
    data2 = client.get(f"/api/v1/plans/{pid}/electives?term={TERM_PATH}").json()
    assert data2["electives"][0]["code"] == ELECTIVE
    assert data2["electives"][0]["adopted"] is True
