"""Part B end-to-end (API): a catalog elective offered ONLY on a professor
page becomes a stored Section (SOURCE_OPTATIVA) and surfaces with its turmas
in ``GET /plans/{id}/electives`` — the card that showed "sem oferta conhecida".

Unlike the other API scrape tests, the fake fetch also serves ONE professor
page (211188), whose fixture offers 22000227 (kind=optativa in the matrix,
absent from both the seed grade and the course page's Optativas accordion).
"""
from __future__ import annotations

from pathlib import Path

import app.api.sections as sections_mod
from app.scrapers.ufpel import scrape_offers as real_scrape_offers
from app.scrapers.ufpel.fetch import CURSO_URL, SERVIDOR_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")
SERVIDOR_HTML = (FIX / "servidor_211188.html").read_text(encoding="utf-8", errors="replace")

TE2 = "22000227"
TE2_NAME = "TÓPICOS ESPECIAIS EM COMPUTAÇÃO II"


def _patch_scrape(monkeypatch):
    """scrape_offers runs for real; fetch serves the course page + ONE servidor."""
    def fake_fetch(url: str) -> str:
        if url == CURSO_URL.format(code="3910"):
            return CURSO_HTML
        if url == SERVIDOR_URL.format(id="211188"):
            return SERVIDOR_HTML
        raise RuntimeError("no network in tests")

    def wrapped(portal, term, wanted, **kw):
        kw.pop("fetch", None)
        return real_scrape_offers(portal, term, wanted, sleep=0, fetch=fake_fetch, **kw)

    monkeypatch.setattr(sections_mod, "scrape_offers", wrapped)


def test_professor_only_elective_reaches_electives_endpoint(client, monkeypatch):
    _patch_scrape(monkeypatch)
    # target term = next_term(2025/2) = 2026/1, matching both fixtures
    r = client.post(
        "/api/v1/plans", json={"name": "part B", "current_term": "2025/2"}
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level_used"] == 1  # grade offer still comes from the course page

    disc = {d["code"]: d for d in body["discovered_optativas"]}
    assert TE2 in disc, "professor-only elective must be discovered (Part B)"
    assert disc[TE2]["name"] == TE2_NAME  # catalog name, never the bare code
    assert disc[TE2]["sections"] >= 1
    # discoveries are never rejected as out-of-scope by the import
    assert all(rej["course_code"] != TE2 for rej in body["rejected"])

    # stored as a read-only elective offer with its own source tag
    g = client.get("/api/v1/terms/2026-1/sections", params={"course_code": TE2}).json()
    assert g["sections"], "the discovered elective must be persisted as Section"
    sec = g["sections"][0]
    assert sec["source"] == "scraper:ufpel-optativa"
    assert sec["course_name"] == TE2_NAME
    assert sec["timeslots"], "schedule parsed from the professor page"

    # ...and it surfaces in the Horários electives card with its turmas
    e = client.get(f"/api/v1/plans/{pid}/electives", params={"term": "2026-1"}).json()
    by_code = {x["code"]: x for x in e["electives"]}
    assert TE2 in by_code
    assert by_code[TE2]["sections"], "offer known -> no more 'sem oferta conhecida'"
    assert by_code[TE2]["name"] == TE2_NAME

    # still NOT a flowchart node: outside the plan's grade until a commit
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    curric = client.get(f"/api/v1/grade-versions/{gv}/curriculum").json()
    assert TE2 not in {c["code"] for c in curric["courses"]}
