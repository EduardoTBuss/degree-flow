"""API/persistence contract tests for v5 F1a — curriculum-matrix CATALOG.

The scrape endpoint is exercised OFFLINE by monkeypatching ``scrape_offers`` to
run the real orchestration against the saved course-page fixture (same pattern
as test_v3). These cover what the parser goldens (test_ufpel_v5) cannot: the
WRITE side — Course materialization rules (ADR-026), the persisted
``elective_catalog``, the seed × portal diff report, idempotence, and that a
failed parse never erases a previously stored catalog (ADR-027).
"""
from __future__ import annotations

from pathlib import Path

import app.api.sections as sections_mod
from app.persistence.models import Course, ElectiveCatalog
from app.scrapers.ufpel import scrape_offers as real_scrape_offers
from app.scrapers.ufpel.fetch import CURSO_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")

# a discovered elective in the matrix that the seed does NOT own (orphan) — its
# Course is created/refreshed with the real credits/hours from the matrix.
ORPHAN_CODE = "22000279"  # MICROCONTROLADORES — optativa, 4cr/60h, not in seed
# seed-owned electives that must NEVER be overwritten by the scrape
CLP = "22000188"   # CONCEITOS DE LINGUAGENS DE PROGRAMAÇÃO
FIA = "22000301"   # FUNDAMENTOS DE INTELIGÊNCIA ARTIFICIAL
PS = "22000237"    # PROGRAMAÇÃO DE SISTEMAS


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


def _default_plan(client) -> str:
    return client.get("/api/v1/plans").json()["plans"][0]["id"]


def _plan_on_grade(client, gv_id: str) -> str:
    r = client.post(
        "/api/v1/plans",
        json={"name": f"v5 {gv_id}", "current_term": "2026/1", "grade_version_id": gv_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _catalog_rows(client, gv_id: str) -> list[ElectiveCatalog]:
    maker = client.app.state.sessionmaker
    with maker() as s:
        return list(
            s.query(ElectiveCatalog).filter_by(grade_version_id=gv_id).all()
        )


def _course(client, code: str) -> Course | None:
    maker = client.app.state.sessionmaker
    with maker() as s:
        return s.get(Course, code)


# ===== scrape writes the catalog ============================================


def test_scrape_reports_catalog_counts(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _default_plan(client)
    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert r.status_code == 200, r.text
    cat = r.json()["catalog"]
    assert cat["source"] == "scraper:ufpel-curriculo"
    assert cat["rows_parsed"] == 92
    assert cat["obrigatorias"] == 44
    assert cat["optativas"] == 47
    assert cat["atividades"] == 1
    assert cat["unknown_kind"] == []
    assert cat["error"] is None
    assert cat["fetched_at"] is not None


def test_scrape_persists_catalog_per_grade(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _default_plan(client)
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    rows = _catalog_rows(client, gv)
    assert len(rows) == 92
    kinds = sorted(r.kind for r in rows)
    assert kinds.count("obrigatoria") == 44
    assert kinds.count("optativa") == 47
    assert kinds.count("atividade") == 1


# ===== Course materialization (ADR-026) =====================================


def test_seed_owned_course_never_overwritten(client, monkeypatch):
    # CLP/FIA are curated by the seed; the scrape must keep their seed record.
    before_clp = _course(client, CLP)
    before_fia = _course(client, FIA)
    assert before_clp is not None and before_fia is not None
    seed_clp = (before_clp.name, before_clp.credits, before_clp.hours)
    seed_fia = (before_fia.name, before_fia.credits, before_fia.hours)
    _patch_scrape(monkeypatch)
    pid = _default_plan(client)
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    after_clp = _course(client, CLP)
    after_fia = _course(client, FIA)
    assert after_clp is not None and after_fia is not None
    assert (after_clp.name, after_clp.credits, after_clp.hours) == seed_clp
    assert (after_fia.name, after_fia.credits, after_fia.hours) == seed_fia


def test_orphan_elective_materialized_with_real_credits(client, monkeypatch):
    # an elective not owned by the seed is (re)created with the matrix's real
    # credits/hours — never left at the 0/0 of the old ADR-016 discovery.
    _patch_scrape(monkeypatch)
    pid = _default_plan(client)
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    orphan = _course(client, ORPHAN_CODE)
    assert orphan is not None
    assert orphan.credits == 4 and orphan.hours == 60.0
    assert orphan.name == "MICROCONTROLADORES"


# ===== seed × portal diff (ADR-026) — regression guard ======================


def test_catalog_diff_clean_on_base_grade(client, monkeypatch):
    # Regression guard on grade 2019/1 (the BASE grade, no reform): the portal's
    # published matrix IS the 2019/1 curriculum, so seed and matrix must match
    # exactly. A non-empty missing_in_matrix / kind_changed here means the parser
    # silently broke (a real obligatoria vanished or a Caráter was misread).
    _patch_scrape(monkeypatch)
    pid = _plan_on_grade(client, "gv-2019-1")
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    r = client.get(f"/api/v1/admin/plans/{pid}/catalog-diff")
    assert r.status_code == 200, r.text
    diff = r.json()
    assert diff["missing_in_matrix"] == []
    assert diff["kind_changed"] == []
    assert diff["fetched_at"] is not None


def test_catalog_diff_reflects_2027_reform_divergence(client, monkeypatch):
    # On grade 2027/1 the reform swapped PS <-> FIA and merged CD/EB. The portal
    # still publishes the 2019/1 matrix, so the diff is RIGHT to flag exactly:
    #  - missing_in_matrix: only the synthetic MERGE: codes (not yet on the portal);
    #  - kind_changed: only the reform swap pair PS (22000237) and FIA (22000301).
    # This proves the diff detects reform drift without ever emitting a Diagnostic.
    _patch_scrape(monkeypatch)
    pid = _default_plan(client)  # default plan is on gv-2027-1
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    diff = client.get(f"/api/v1/admin/plans/{pid}/catalog-diff").json()
    missing = [m["code"] for m in diff["missing_in_matrix"]]
    assert all(c.startswith("MERGE:") for c in missing), missing
    changed = {c["code"] for c in diff["kind_changed"]}
    assert changed == {PS, FIA}, changed


def test_catalog_diff_before_scrape_is_empty(client):
    pid = _default_plan(client)
    r = client.get(f"/api/v1/admin/plans/{pid}/catalog-diff")
    assert r.status_code == 200
    d = r.json()
    assert d["missing_in_matrix"] == [] and d["kind_changed"] == []
    assert d["hours_changed"] == [] and d["new_obligatory"] == []
    assert d["fetched_at"] is None


def test_catalog_diff_missing_plan_is_404(client):
    r = client.get("/api/v1/admin/plans/nope/catalog-diff")
    assert r.status_code == 404


# ===== idempotence + failed-parse safety (ADR-027) ==========================


def test_scrape_catalog_is_idempotent(client, monkeypatch):
    _patch_scrape(monkeypatch)
    pid = _default_plan(client)
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    first = _catalog_rows(client, gv)
    r2 = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    second = _catalog_rows(client, gv)
    assert len(first) == len(second) == 92  # replace, never accumulate
    # second run created nothing new (courses already materialized)
    assert r2.json()["catalog"]["created_courses"] == 0


def test_failed_parse_keeps_previous_catalog(client, monkeypatch):
    # first a healthy scrape populates the catalog...
    _patch_scrape(monkeypatch)
    pid = _default_plan(client)
    gv = client.get(f"/api/v1/plans/{pid}").json()["grade_version_id"]
    client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert len(_catalog_rows(client, gv)) == 92

    # ...then the matrix parse blows up (portal changed): offer still scrapes,
    # rows_parsed == 0, and the stored catalog is NOT erased.
    def boom(_html):
        raise RuntimeError("portal mudou a matriz")

    import app.scrapers.ufpel.orchestrate as orch_mod
    monkeypatch.setattr(orch_mod, "parse_curso_curriculo", boom)
    r = client.post(f"/api/v1/admin/plans/{pid}/sections/scrape")
    assert r.status_code == 200
    assert r.json()["catalog"]["rows_parsed"] == 0
    assert r.json()["catalog"]["error"] is not None
    assert len(_catalog_rows(client, gv)) == 92  # preserved
