"""v5 scraper tests — curriculum matrix parse (ADR-025) + catalog orchestration.

Golden tests of ``parse_curso_curriculo`` against the saved real course page
(never hits the network): the CATALOG rides on the SAME html the level-1
scrape already downloads, so orchestration gains it for free — and a broken
matrix parse must NEVER abort the OFFER collection (ADR-027).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import app.scrapers.ufpel.orchestrate as orch_mod
from app.scrapers.ufpel import parse_curso_curriculo, scrape_offers
from app.scrapers.ufpel.fetch import CURSO_URL, SERVIDOR_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")
SERVIDOR_HTML = (FIX / "servidor_211188.html").read_text(encoding="utf-8", errors="replace")

# Tópicos Especiais em Computação I..X — invisible to the old ADR-016 discovery,
# first-class citizens of the matrix (spec v5 §7)
TE_CODES = {
    "22000242", "22000227", "22000228", "22000229", "22000346",
    "22000347", "22000348", "22000349", "22000350", "22000351",
}


# ===== golden — real fixture ================================================


def test_parse_curriculo_golden_counts():
    rows, unknown = parse_curso_curriculo(CURSO_HTML)
    assert len(rows) == 92
    assert len({r.code for r in rows}) == 92  # deduped
    assert Counter(r.kind for r in rows) == {
        "obrigatoria": 44, "optativa": 47, "atividade": 1,
    }
    assert unknown == []  # every portal "Caráter" is in _KIND_MAP


def test_parse_curriculo_row_22000294_complete():
    rows, _ = parse_curso_curriculo(CURSO_HTML)
    r = next(r for r in rows if r.code == "22000294")
    assert r.name == "ALGORITMOS E PROGRAMAÇÃO"
    assert r.kind == "obrigatoria"
    assert r.portal_kind == "Obrigatória"  # raw string kept for audit
    assert r.credits == 4 and r.hours == 60.0
    assert r.prereqs == ()
    assert r.suggested_term_index == 1  # from the "1º Semestre" heading


def test_parse_curriculo_name_never_polluted_by_prereqs():
    # the "Disciplina / Pré-requisitos" cell concatenates name + prereq codes
    # ('CÁLCULO 2 11100058 - CÁLCULO 1') — the name must come out clean
    rows, _ = parse_curso_curriculo(CURSO_HTML)
    calc2 = next(r for r in rows if r.code == "11100059")
    assert calc2.name == "CÁLCULO 2"
    assert calc2.prereqs == ("11100058",)
    assert calc2.suggested_term_index == 2
    # multi-prereq row: deduped and sorted
    calc3 = next(r for r in rows if r.code == "11100060")
    assert calc3.name == "CÁLCULO 3"
    assert calc3.prereqs == ("11100059", "11100110")


def test_parse_curriculo_topicos_especiais_i_to_x_present():
    rows, _ = parse_curso_curriculo(CURSO_HTML)
    by_code = {r.code: r for r in rows}
    assert TE_CODES <= set(by_code)
    for code in TE_CODES:
        assert by_code[code].kind == "optativa"
        assert by_code[code].suggested_term_index is None  # "Optativas" heading


def test_parse_curriculo_covers_both_optativas_accordions():
    # the portal splits the electives across TWO "Optativas" accordions:
    # 22000300 lives in the first, the TEs (22000346…) in the second — both
    # must be parsed, and the total stays deduped at 47
    rows, _ = parse_curso_curriculo(CURSO_HTML)
    codes = {r.code for r in rows if r.kind == "optativa"}
    assert "22000300" in codes and "22000346" in codes
    assert len(codes) == 47


def test_parse_curriculo_atividade_complementar():
    rows, _ = parse_curso_curriculo(CURSO_HTML)
    ativ = [r for r in rows if r.kind == "atividade"]
    assert len(ativ) == 1
    assert ativ[0].code == "22000575"
    assert ativ[0].portal_kind == "Atividade complementar"
    assert ativ[0].hours == 315.0
    assert ativ[0].suggested_term_index is None  # "Complementares" heading


def test_parse_curriculo_missing_tab_is_empty():
    assert parse_curso_curriculo("<html><body>sem matriz</body></html>") == ([], [])


# ===== synthetic — unknown kind + dedupe rule ===============================

_SYNTH = """
<div id="curriculo">
<h3 data-control>1º Semestre</h3><div data-content>
<table class="tabela-disciplinas">
<tr><th>Código</th><th>Disciplina / Pré-requisitos</th><th>Caráter</th><th>Cr.</th><th>Horas</th></tr>
<tr><td><a href="/disciplinas/cod/12345678">12345678</a></td>
<td><a href="/disciplinas/cod/12345678">COISA I</a></td><td>Eletiva</td><td>4</td><td>60</td></tr>
<tr><td><a href="/disciplinas/cod/23456789">23456789</a></td>
<td><a href="/disciplinas/cod/23456789">COISA II</a></td><td>Optativa</td><td>2</td><td>30</td></tr>
</table></div>
<h3 data-control>Optativas</h3><div data-content>
<table class="tabela-disciplinas">
<tr><td><a href="/disciplinas/cod/23456789">23456789</a></td>
<td><a href="/disciplinas/cod/23456789">COISA II RENOMEADA</a></td><td>Optativa</td><td>8</td><td>120</td></tr>
</table></div>
</div>
"""


def test_unknown_kind_is_reported_never_guessed():
    rows, unknown = parse_curso_curriculo(_SYNTH)
    assert unknown == [{"code": "12345678", "portal_kind": "Eletiva"}]
    assert "12345678" not in {r.code for r in rows}  # ignored, not guessed


def test_dedupe_first_occurrence_wins():
    rows, _ = parse_curso_curriculo(_SYNTH)
    dup = [r for r in rows if r.code == "23456789"]
    assert len(dup) == 1
    assert dup[0].name == "COISA II"  # first table wins
    assert dup[0].credits == 2 and dup[0].hours == 30.0
    assert dup[0].suggested_term_index == 1


# ===== orchestration (fake fetch — offline) =================================


class _FakeFetch:
    def __init__(self, known_servidores: set[str], curso_html: str = CURSO_HTML):
        self.known = known_servidores
        self.curso_html = curso_html
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url == CURSO_URL.format(code="3910"):
            return self.curso_html
        for sid in self.known:
            if url == SERVIDOR_URL.format(id=sid):
                return SERVIDOR_HTML
        raise RuntimeError("page unavailable")


def test_scrape_offers_catalog_on_level1():
    fetch = _FakeFetch(set())
    res = scrape_offers("3910", "2026/1", {"22000294"}, sleep=0, fetch=fetch, discover=True)
    assert res.level_used == 1
    assert len(res.catalog) == 92
    assert res.catalog_unknown == [] and res.catalog_error is None
    # the CATALOG itself costs zero extra GETs (parsed from the same course
    # page); every call beyond the first is the Part-B exhaustive elective
    # sweep over professor pages — never a second course-page GET
    curso_url = CURSO_URL.format(code="3910")
    assert fetch.calls[0] == curso_url
    assert fetch.calls.count(curso_url) == 1
    assert all("/servidores/" in u for u in fetch.calls[1:])


def test_scrape_offers_catalog_survives_level2():
    # target term not on the course page -> professor fallback; the catalog was
    # parsed from the level-1 page and must ride along (ADR-025)
    fetch = _FakeFetch({"211188"})
    res = scrape_offers("3910", "2026/2", {"22000299"}, sleep=0, fetch=fetch, discover=True)
    assert res.level_used == 2
    assert len(res.catalog) == 92 and res.catalog_error is None


def test_scrape_offers_without_discover_has_no_catalog():
    res = scrape_offers("3910", "2026/1", {"22000294"}, sleep=0, fetch=_FakeFetch(set()))
    assert res.catalog == [] and res.catalog_unknown == []
    assert res.catalog_error is None


def test_matrix_parse_exception_never_aborts_offer(monkeypatch):
    def boom(_html: str):
        raise RuntimeError("portal mudou a matriz")

    monkeypatch.setattr(orch_mod, "parse_curso_curriculo", boom)
    res = scrape_offers(
        "3910", "2026/1", {"22000294"}, sleep=0, fetch=_FakeFetch(set()), discover=True
    )
    # the OFFER scrape proceeds untouched (isolated try/except, ADR-027)
    assert res.level_used == 1
    assert res.payload.sections and res.found_codes == {"22000294"}
    assert res.catalog == []
    assert res.catalog_error is not None and "matriz" in res.catalog_error


def test_missing_curriculo_tab_sets_error_keeps_offer():
    html = CURSO_HTML.replace('id="curriculo"', 'id="curriculo-fora"', 1)
    res = scrape_offers(
        "3910", "2026/1", {"22000294"}, sleep=0, fetch=_FakeFetch(set(), html), discover=True
    )
    assert res.payload.sections  # offer intact
    assert res.catalog == []
    assert res.catalog_error is not None and "#curriculo" in res.catalog_error
