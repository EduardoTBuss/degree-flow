"""v5 ADR-028b — a materialized elective survives a grade switch.

Migrates when the target grade's catalog also lists it (elective_migrated,
item preserved); otherwise drops with an ACTIONABLE reason (no_catalog /
not_in_catalog) — never the generic "dropped" that would lie about an elective
that exists in the portal but just wasn't adopted in the target grade.
"""
from __future__ import annotations

from pathlib import Path

import app.api.sections as sections_mod
from app.scrapers.ufpel import scrape_offers as real_scrape_offers
from app.scrapers.ufpel.fetch import CURSO_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")

ELECTIVE = "22000279"  # MICROCONTROLADORES — optativa in the matrix (both grades)


def _patch_scrape(monkeypatch):
    def fake_fetch(url: str) -> str:
        if url == CURSO_URL.format(code="3910"):
            return CURSO_HTML
        raise RuntimeError("no network in tests")

    def wrapped(portal, term, wanted, **kw):
        kw.pop("fetch", None)
        return real_scrape_offers(portal, term, wanted, sleep=0, fetch=fake_fetch, **kw)

    monkeypatch.setattr(sections_mod, "scrape_offers", wrapped)


def _plan_on(client, gv: str) -> str:
    r = client.post("/api/v1/plans",
                    json={"name": f"t {gv}", "current_term": "2026/1", "grade_version_id": gv})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _notes_of(patch_response) -> dict[str, dict]:
    return {
        d["details"]["relation"]: d
        for d in patch_response.json().get("diagnostics", [])
        if d.get("type") == "TRANSITION_NOTE"
    }


def test_adopted_elective_migrates_when_target_catalog_has_it(client, monkeypatch):
    _patch_scrape(monkeypatch)
    # scrape BOTH grades so both catalogs list the elective
    p2019 = _plan_on(client, "gv-2019-1")
    client.post(f"/api/v1/admin/plans/{p2019}/sections/scrape")
    pid = _plan_on(client, "gv-2027-1")
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")

    # materialize + allocate on 2027/1
    client.post(f"/api/v1/plans/{pid}/terms/2026-1/commit",
                json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})
    # switch grade -> should migrate (item preserved, adoption created on target)
    r = client.patch(f"/api/v1/plans/{pid}", json={"grade_version_id": "gv-2019-1"})
    assert r.status_code == 200, r.text
    notes = _notes_of(r)
    assert "elective_migrated" in notes
    assert notes["elective_migrated"]["details"]["to"] == ELECTIVE
    item = next((i for i in r.json()["items"] if i["course_code"] == ELECTIVE), None)
    assert item is not None and item["term"] == "2026/1"  # preserved


def test_adopted_elective_drops_when_target_never_scraped(client, monkeypatch):
    _patch_scrape(monkeypatch)
    # only 2027/1 scraped; 2019/1 has no catalog -> no_catalog reason
    pid = _plan_on(client, "gv-2027-1")
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    client.post(f"/api/v1/plans/{pid}/terms/2026-1/commit",
                json={"choices": [{"course_code": ELECTIVE, "section_id": None}]})
    r = client.patch(f"/api/v1/plans/{pid}", json={"grade_version_id": "gv-2019-1"})
    assert r.status_code == 200, r.text
    notes = _notes_of(r)
    assert "elective_dropped" in notes
    assert notes["elective_dropped"]["details"]["reason"] == "no_catalog"
    assert all(i["course_code"] != ELECTIVE for i in r.json()["items"])  # dropped
