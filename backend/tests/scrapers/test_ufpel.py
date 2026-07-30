"""Scraper parser against a saved real HTML fixture (never hits the network)."""
from __future__ import annotations

from pathlib import Path

from app.scrapers.ufpel import parse_disciplina

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "scrapers"


def test_parses_turmas_ofertadas_from_real_html():
    html = (FIX / "disc_22000297.html").read_text(encoding="utf-8", errors="replace")
    sections = parse_disciplina(html, "22000297", "2026/1")
    assert sections, "should find offered sections"
    labels = {s.label for s in sections}
    assert "M3" in labels

    m3 = next(s for s in sections if s.label == "M3")
    assert m3.course_code == "22000297"
    assert m3.capacity and m3.capacity > 0
    assert m3.professor  # extracted from the /servidores/ link
    assert m3.timeslots  # schedule parsed
    # merged adjacent blocks (e.g. Tue 15:10-16:00 + 16:00-16:50 -> 15:10-16:50)
    for ts in m3.timeslots:
        assert ts.start < ts.end


def test_wrong_term_filters_out():
    html = (FIX / "disc_22000297.html").read_text(encoding="utf-8", errors="replace")
    # the fixture offers 2026/1 sections; asking 2026/2 yields none
    assert parse_disciplina(html, "22000297", "2026/2") == []
