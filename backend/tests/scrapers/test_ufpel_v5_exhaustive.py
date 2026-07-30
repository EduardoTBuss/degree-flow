"""Part B — exhaustive catalog-scoped elective discovery on professor pages.

The bug (2026-07-18): catalog electives offered ONLY on professor pages never
became Sections — level 2 filtered by the grade scope, early-stopped, and did
not even run when level 1 resolved the faltantes. These tests pin the fix:
- discover=True widens the professor-sweep scope to the catalog's optativa
  codes and visits EVERY professor (no early-stop), even in mixed mode
  (level 1 already resolved the grade offer);
- discover=False keeps the legacy behaviour byte-for-byte (level-1 short
  circuit + early-stop + grade-only scope).

Fixture facts used: ``servidor_211188.html`` offers 22000227 (TÓPICOS
ESPECIAIS EM COMPUTAÇÃO II — kind=optativa in the matrix of
``curso_3910.html``) in 2026/1, label T1 — and 22000227 is NOT under the
course page's "Optativas" accordion, so only the professor sweep can find it.
"""
from __future__ import annotations

from pathlib import Path

import app.scrapers.ufpel.orchestrate as orch_mod
from app.scrapers.ufpel import (
    SOURCE_CURSO,
    parse_curso_professores,
    scrape_offers,
)
from app.scrapers.ufpel.fetch import CURSO_URL, SERVIDOR_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")
SERVIDOR_HTML = (FIX / "servidor_211188.html").read_text(encoding="utf-8", errors="replace")

TE2 = "22000227"  # professor-page-only elective of the fixture (see docstring)
TE2_NAME = "TÓPICOS ESPECIAIS EM COMPUTAÇÃO II"


class _FakeFetch:
    def __init__(self, known_servidores: set[str]):
        self.known = known_servidores
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url == CURSO_URL.format(code="3910"):
            return CURSO_HTML
        for sid in self.known:
            if url == SERVIDOR_URL.format(id=sid):
                return SERVIDOR_HTML
        raise RuntimeError("page unavailable")


def _servidor_calls(fetch: _FakeFetch) -> list[str]:
    return [u for u in fetch.calls if "/servidores/" in u]


# ===== (a) professor-only elective becomes a discovery ======================


def test_professor_only_elective_discovered_in_mixed_mode():
    # level 1 resolves the grade faltante (22000294 on the course page), yet
    # the sweep still runs and finds the catalog elective on 211188's page
    fetch = _FakeFetch({"211188"})
    res = scrape_offers(
        "3910", "2026/1", {"22000294"}, sleep=0, fetch=fetch, discover=True
    )
    # grade offer untouched: level 1, course-page source, grade codes only
    assert res.level_used == 1
    assert res.payload.source == SOURCE_CURSO
    assert {s.course_code for s in res.payload.sections} == {"22000294"}

    disc_codes = {s.course_code for s in res.discovered_sections}
    assert TE2 in disc_codes  # was DISCARDED before the fix
    te2 = next(s for s in res.discovered_sections if s.course_code == TE2)
    assert te2.id == "2026-1:22000227:T1"
    assert te2.professor == "DOCENTE DE TESTE"
    assert te2.note is None  # offered to 3910 itself -> no cross-course note
    # name resolved from the CATALOG (the servidor row has no course name)
    assert res.discovered_names[TE2] == TE2_NAME
    # level-1 discoveries (course-page Optativas accordion) survive the merge
    assert "22000254" in disc_codes
    # dedupe by section id across both levels
    ids = [s.id for s in res.discovered_sections]
    assert len(ids) == len(set(ids))


def test_done_elective_never_discovered_on_professor_pages():
    fetch = _FakeFetch({"211188"})
    res = scrape_offers(
        "3910", "2026/1", {"22000294"},
        sleep=0, fetch=fetch, done_codes={TE2}, discover=True,
    )
    assert TE2 not in {s.course_code for s in res.discovered_sections}


# ===== (b) discover=True sweeps ALL professors ==============================


def test_mixed_mode_sweeps_every_professor():
    fetch = _FakeFetch({"211188"})
    scrape_offers("3910", "2026/1", {"22000294"}, sleep=0, fetch=fetch, discover=True)
    ids = parse_curso_professores(CURSO_HTML)
    calls = _servidor_calls(fetch)
    assert len(calls) == len(ids) == 68  # exhaustive, no early-stop
    assert len(set(calls)) == len(calls)  # each professor visited once


def test_level2_with_discover_has_no_early_stop():
    # grade faltante found on 211188's page (early in the list) — the sweep
    # must still visit everyone hunting for electives
    fetch = _FakeFetch({"211188"})
    res = scrape_offers("3910", "2026/2", {"22000299"}, sleep=0, fetch=fetch, discover=True)
    assert res.level_used == 2
    assert res.found_codes == {"22000299"}
    assert len(_servidor_calls(fetch)) == 68
    # the fixture offers no catalog elective in 2026/2 -> nothing discovered
    assert res.discovered_sections == []


def test_catalog_failure_disables_sweep_in_mixed_mode(monkeypatch):
    # no catalog -> no elective scope -> zero extra GETs (legacy level-1 wins);
    # documents the "exhaustive only when there is something to hunt" decision
    def boom(_html: str):
        raise RuntimeError("portal mudou a matriz")

    monkeypatch.setattr(orch_mod, "parse_curso_curriculo", boom)
    fetch = _FakeFetch({"211188"})
    res = scrape_offers("3910", "2026/1", {"22000294"}, sleep=0, fetch=fetch, discover=True)
    assert res.level_used == 1
    assert fetch.calls == [CURSO_URL.format(code="3910")]


# ===== (c) regression: discover=False is byte-for-byte legacy ===============


def test_no_discover_keeps_level1_short_circuit():
    fetch = _FakeFetch({"211188"})
    res = scrape_offers("3910", "2026/1", {"22000294"}, sleep=0, fetch=fetch)
    assert res.level_used == 1 and res.pages_failed == 0
    assert fetch.calls == [CURSO_URL.format(code="3910")]  # zero professor GETs


def test_no_discover_keeps_early_stop():
    fetch = _FakeFetch({"211188"})
    res = scrape_offers("3910", "2026/2", {"22000299"}, sleep=0, fetch=fetch)
    assert res.found_codes == {"22000299"}
    ids = parse_curso_professores(CURSO_HTML)
    calls = _servidor_calls(fetch)
    assert calls[-1] == SERVIDOR_URL.format(id="211188")
    assert len(calls) == ids.index("211188") + 1 < len(ids)
    assert res.discovered_sections == [] and res.discovered_names == {}
