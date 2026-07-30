"""History parser tests using synthetic, generated PDFs."""
from __future__ import annotations

import pytest

from app.importers.historico import ParseError, build_proposal, parse
from tests.pdf_samples import historico_pdf, integralizacao_pdf


def _read(name: str) -> bytes:
    return historico_pdf() if name == "historico_sample.pdf" else integralizacao_pdf()


def test_leading_bytes_before_pdf_header_are_stripped():
    raw = _read("historico_sample.pdf")
    assert raw[:4] == b"\n\n\n\n"  # the Cobalto quirk
    parsed = parse(raw)  # must not raise
    assert parsed.rows


def test_historico_format_detected_and_start_term():
    parsed = parse(_read("historico_sample.pdf"))
    assert parsed.fmt == "ufpel-historico"
    assert parsed.start_term == "2024/1"
    assert parsed.student_id == "00000000"


def test_historico_dedup_reproved_then_approved():
    parsed = parse(_read("historico_sample.pdf"))
    catalog = {r.code for r in parsed.rows}
    proposal = build_proposal(parsed, catalog, {})
    by_code = {m["code"]: m for m in proposal["matches"]}
    # Cálculo 2: reproved 2024/2 then approved 2025/1 -> resolves to approved
    assert by_code["11100059"]["status_inferred"] == "aprovada"
    assert by_code["11100059"]["term"] == "2025/1"


def test_integralizacao_completed_term_and_equivalence():
    parsed = parse(_read("integralizacao_sample.pdf"))
    assert parsed.fmt == "ufpel-integralizacao"
    rows = {r.code: r for r in parsed.rows}
    assert rows["11100058"].term == "2024/1"           # explicit (AAAA/S)
    assert rows["11100110"].equivalence == "11100005"  # by equivalence, no term
    assert rows["11100110"].term is None


def test_reproved_only_stays_falta_not_a_match():
    parsed = parse(_read("historico_sample.pdf"))
    # AED I appears reproved then cursando -> resolves to cursando (a match), not dropped
    catalog = {r.code for r in parsed.rows}
    proposal = build_proposal(parsed, catalog, {})
    by_code = {m["code"]: m for m in proposal["matches"]}
    assert by_code.get("22000297", {}).get("status_inferred") == "cursando"


def test_non_pdf_raises_parse_error():
    with pytest.raises(ParseError):
        parse(b"this is not a pdf")
