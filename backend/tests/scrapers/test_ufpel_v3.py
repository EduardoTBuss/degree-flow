"""v3 scraper tests against saved real HTML fixtures (never hits the network).

Covers ADR-011 (2-level strategy), ADR-014 (cross-course note) and the real
HTML quirks documented in ARCHITECTURE-v3 §4.3/§4.4: term only in the h3,
professor as plain text (no /servidores/ link) on the course page, orphan
</a> on the servidor page, and `CH *` ("36+0") never read as capacity.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.scrapers.ufpel import (
    REASON_NO_PORTAL_CODE,
    SOURCE_CURSO,
    SOURCE_PROFESSORES,
    parse_curso_professores,
    parse_curso_turmas,
    parse_servidor_disciplinas,
    scrape_offers,
)
from app.scrapers.ufpel.fetch import CURSO_URL, SERVIDOR_URL

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"
CURSO_HTML = (FIX / "curso_3910.html").read_text(encoding="utf-8", errors="replace")
SERVIDOR_HTML = (FIX / "servidor_211188.html").read_text(encoding="utf-8", errors="replace")


# ===== level 1 — course page ==============================================


def test_parse_curso_turmas_header_term_from_h3():
    header_term, _ = parse_curso_turmas(CURSO_HTML, set())
    assert header_term == "2026/1"  # "Turmas ofertadas em 2026 / 1" (spaces around /)


def test_parse_curso_turmas_sections_with_label_professor_and_counts():
    _, sections = parse_curso_turmas(CURSO_HTML, {"22000294"})
    assert {s.course_code for s in sections} == {"22000294"}
    by_label = {s.label: s for s in sections}
    assert "M3" in by_label and "M4" in by_label

    m3 = by_label["M3"]
    assert m3.id == "2026-1:22000294:M3"
    assert m3.capacity == 24 and m3.enrolled == 25
    # professor extracted by LABEL from span.tabela-detalhe-info (no link here)
    assert m3.professor == "PAULO ROBERTO FERREIRA JUNIOR"
    # M4 also has "Professor Regente: …" — the responsible one must win
    assert by_label["M4"].professor == "PAULO ROBERTO FERREIRA JUNIOR"


def test_parse_curso_turmas_merges_adjacent_timeslots():
    _, sections = parse_curso_turmas(CURSO_HTML, {"22000294"})
    m3 = next(s for s in sections if s.label == "M3")
    # SEG 08:00-08:50 + 08:50-09:40 (empty span = same day) -> one merged block
    seg = [ts for ts in m3.timeslots if ts.weekday == 0]
    assert seg and seg[0].start == "08:00" and seg[0].end == "09:40"
    qua = [ts for ts in m3.timeslots if ts.weekday == 2]
    assert qua and qua[0].start == "08:00" and qua[0].end == "09:40"


def test_parse_curso_turmas_filters_wanted_codes():
    _, sections = parse_curso_turmas(CURSO_HTML, {"11100058"})
    assert sections and all(s.course_code == "11100058" for s in sections)
    _, none = parse_curso_turmas(CURSO_HTML, set())
    assert none == []


def test_parse_curso_professores_dedupes_preserving_order():
    ids = parse_curso_professores(CURSO_HTML)
    assert len(ids) == len(set(ids))  # each id appears 2x in the HTML — deduped
    assert len(ids) == 68
    assert ids[0] == "146325"  # first card keeps first position
    assert "211188" in ids


# ===== level 2 — servidor page ============================================


def test_parse_servidor_associates_tds_despite_orphan_anchor():
    secs = parse_servidor_disciplinas(SERVIDOR_HTML, "2026/2", {"22000299"}, "3910")
    assert {s.label for s in secs} == {"M3", "M4", "M1"}
    m3 = next(s for s in secs if s.label == "M3")
    assert m3.course_code == "22000299"
    assert m3.id == "2026-2:22000299:M3"
    # schedule from the nested grade-horarios of the Disciplina td
    wd = {ts.weekday: ts for ts in m3.timeslots}
    assert wd[2].start == "08:00" and wd[2].end == "09:40"  # QUA morning merged
    assert wd[1].start == "15:10" and wd[1].end == "16:50"  # TER afternoon merged


def test_parse_servidor_ch_column_is_never_capacity():
    secs = parse_servidor_disciplinas(SERVIDOR_HTML, "2026/2", {"22000299"}, "3910")
    for s in secs:
        assert s.capacity is None and s.enrolled is None  # "36+0" is workload


def test_parse_servidor_cross_course_row_gets_note():
    secs = parse_servidor_disciplinas(SERVIDOR_HTML, "2026/2", {"22000299"}, "3910")
    by_label = {s.label: s for s in secs}
    assert by_label["M3"].note is None  # offered to 3910 itself
    assert by_label["M4"].note is None
    assert by_label["M1"].note == "ofertada para: Ciência da Computação (Bacharelado)"


def test_parse_servidor_filters_other_terms():
    # 22000297 only appears in 2026/1 and 2025/2 rows of the fixture
    assert parse_servidor_disciplinas(SERVIDOR_HTML, "2026/2", {"22000297"}, "3910") == []
    older = parse_servidor_disciplinas(SERVIDOR_HTML, "2026/1", {"22000297"}, "3910")
    assert {s.label for s in older} == {"M3", "M4"}


def test_parse_servidor_extracts_professor_name_from_ficha():
    secs = parse_servidor_disciplinas(SERVIDOR_HTML, "2026/2", {"22000299"}, "3910")
    assert all(s.professor == "DOCENTE DE TESTE" for s in secs)


# ===== orchestration (fake fetch — offline) ================================


class _FakeFetch:
    """fetch stub: curso page from fixture; known servidor ids from fixture;
    every other servidor page raises (must be counted, never abort).
    A callable object (not a bare function) so `.calls` is a typed attribute."""

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


def _fake_fetch(known_servidores: set[str]) -> _FakeFetch:
    return _FakeFetch(known_servidores)


def test_scrape_offers_level1_when_header_matches():
    fetch = _fake_fetch(set())
    res = scrape_offers("3910", "2026/1", {"22000294", "99999999"}, sleep=0, fetch=fetch)
    assert res.level_used == 1
    assert res.payload.source == SOURCE_CURSO
    assert res.pages_fetched == 1 and res.pages_failed == 0
    assert fetch.calls == [CURSO_URL.format(code="3910")]  # one single GET
    assert res.found_codes == {"22000294"}
    assert res.missing == [
        {"code": "99999999", "reason": "nenhuma turma encontrada para 2026/1"}
    ]


def test_scrape_offers_level2_triggers_when_no_target_term_sections():
    fetch = _fake_fetch({"211188"})
    res = scrape_offers("3910", "2026/2", {"22000299"}, sleep=0, fetch=fetch)
    assert res.level_used == 2
    assert res.payload.source == SOURCE_PROFESSORES
    assert res.found_codes == {"22000299"}
    assert {s.label for s in res.payload.sections} == {"M3", "M4", "M1"}
    assert res.missing == []
    # failing professor pages counted, batch not aborted
    ids = parse_curso_professores(CURSO_HTML)
    visited = ids.index("211188") + 1
    assert res.pages_failed == visited - 1
    assert res.pages_fetched == 1 + 1  # curso page + the one servidor that worked


def test_scrape_offers_level2_early_stops():
    fetch = _fake_fetch({"211188"})
    scrape_offers("3910", "2026/2", {"22000299"}, sleep=0, fetch=fetch)
    ids = parse_curso_professores(CURSO_HTML)
    servidor_calls = [u for u in fetch.calls if "/servidores/" in u]
    # stops right after 211188 satisfies the scope — nothing fetched beyond it
    assert servidor_calls[-1] == SERVIDOR_URL.format(id="211188")
    assert len(servidor_calls) == ids.index("211188") + 1
    assert len(servidor_calls) < len(ids)


def test_scrape_offers_dedupes_same_section_seen_twice():
    # two professors returning the same fixture -> same section ids: first wins
    ids = parse_curso_professores(CURSO_HTML)
    fetch = _fake_fetch(set(ids[:2]))

    # force a full sweep (an unfindable code disables early-stop)
    res = scrape_offers("3910", "2026/2", {"22000299", "99999999"}, sleep=0, fetch=fetch)
    labels = [s.label for s in res.payload.sections if s.course_code == "22000299" and s.label]
    assert sorted(labels) == ["M1", "M3", "M4"]  # not duplicated


def test_merge_codes_never_generate_get():
    fetch = _fake_fetch(set())
    res = scrape_offers(
        "3910", "2026/2", {"MERGE:22000230+22000271"}, sleep=0, fetch=fetch
    )
    assert fetch.calls == []  # zero requests
    assert res.level_used == 1 and res.payload.sections == []
    assert res.missing == [
        {"code": "MERGE:22000230+22000271", "reason": REASON_NO_PORTAL_CODE}
    ]
    assert res.pages_fetched == 0 and res.pages_failed == 0


def test_merge_codes_mixed_with_portal_codes():
    fetch = _fake_fetch({"211188"})
    res = scrape_offers(
        "3910", "2026/2", {"MERGE:22000226+22000238", "22000299"}, sleep=0, fetch=fetch
    )
    assert all("MERGE" not in url for url in fetch.calls)
    reasons = {m["code"]: m["reason"] for m in res.missing}
    assert reasons == {"MERGE:22000226+22000238": REASON_NO_PORTAL_CODE}
    assert res.found_codes == {"22000299"}


def test_curso_page_failure_propagates():
    def fetch(_url: str) -> str:
        raise RuntimeError("portal down")

    with pytest.raises(RuntimeError):
        scrape_offers("3910", "2026/2", {"22000299"}, sleep=0, fetch=fetch)


# ===== optativa discovery (ADR-016) =======================================


def test_parse_curso_optativas_scoped_to_accordion():
    from app.scrapers.ufpel import parse_curso_optativas

    header, sections, names = parse_curso_optativas(CURSO_HTML)
    assert header == "2026/1"
    codes = {s.course_code for s in sections}
    # only disciplines under the "Optativas" heading — not the obrigatorias
    assert "22000301" in codes            # Fundamentos de IA (optativa)
    assert "22000294" not in codes        # Algoritmos e Programação (1º sem, obrigatoria)
    assert names.get("22000301")          # name captured from the link text


def test_scrape_discovers_optativas_excluding_done_and_wanted():
    fetch = _fake_fetch(set())
    res = scrape_offers(
        "3910", "2026/1", {"22000301"},         # 22000301 is a wanted faltante here
        sleep=0, fetch=fetch, done_codes={"22000229", "22000349"}, discover=True,
    )
    disc = {s.course_code for s in res.discovered_sections}
    assert disc                                   # some electives discovered
    assert "22000301" not in disc                 # wanted -> not re-discovered
    assert "22000229" not in disc and "22000349" not in disc  # done -> excluded
    assert all(res.discovered_names.get(c) for c in disc if c in res.discovered_names)


def test_level2_fires_when_level1_finds_only_optativas():
    # Course page shows the target term (2026/1) but NONE of the wanted codes;
    # discovered optativas alone must NOT satisfy level 1 (ADR-011: zero turmas
    # do alvo -> nível 2) — e.g. a faltante offered only to another course is
    # visible only on professor pages (ADR-014). Discoveries survive the fallback.
    ids = parse_curso_professores(CURSO_HTML)
    fetch = _fake_fetch({ids[0]})
    res = scrape_offers("3910", "2026/1", {"99999999"}, sleep=0, fetch=fetch, discover=True)
    assert res.level_used == 2
    assert any("/servidores/" in u for u in fetch.calls)
    assert res.discovered_sections  # course-page discoveries carried into level 2


def test_discover_flag_off_yields_nothing():
    fetch = _fake_fetch(set())
    res = scrape_offers("3910", "2026/1", {"22000294"}, sleep=0, fetch=fetch)
    assert res.discovered_sections == [] and res.discovered_names == {}


def test_parse_servidor_offered_to_is_structured():
    # A3 (ADR-020): the cross-course info is ALSO emitted structured, not only
    # in the human `note`; own-course rows carry offered_to=None.
    secs = parse_servidor_disciplinas(SERVIDOR_HTML, "2026/2", {"22000299"}, "3910")
    by_label = {s.label: s for s in secs}
    assert by_label["M3"].offered_to is None
    assert by_label["M4"].offered_to is None
    assert by_label["M1"].offered_to == ["Ciência da Computação (Bacharelado)"]
