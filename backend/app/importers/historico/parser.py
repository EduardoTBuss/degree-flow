"""Pure parser for the two Cobalto PDF formats (F2 / ADR-006). No DB, no HTTP.

Handles the quirk of leading bytes before the ``%PDF`` header and matches by
numeric course code (robust to encoding/accents in names).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

APROVADA = "aprovada"
CURSANDO = "cursando"
REPROVADA = "reprovada"

_CODE = r"\d{6,8}"
_TERM = r"\d{4}/[12]"

_HIST_HEADER = re.compile(rf"^({_TERM})\s+Componente\s+Curricular", re.IGNORECASE)
_HIST_LINE = re.compile(rf"^({_CODE})\s*-\s*(.+)$")
_INT_LINE = re.compile(rf"^({_CODE})\s+(.+?)\s+DISCIPLINA\s+\S+(.*)$", re.IGNORECASE)
_TERM_PAREN = re.compile(rf"\(({_TERM})\)")
_TURMA = re.compile(r"^[A-Z]{1,3}\d{1,3}$")
_SITUACAO = re.compile(r"\b(APR|REPR|RM|RF|APROVAD[OA]|REPROVAD[OA]|CURSANDO|MATRICULAD[OA])\b", re.IGNORECASE)
_INGRESS = re.compile(rf"Ano/semestre:\s*({_TERM})", re.IGNORECASE)


class ParseError(Exception):
    pass


@dataclass
class ParsedRow:
    code: str
    name: str
    status: str  # aprovada | cursando | reprovada
    term: str | None = None
    grade: str | None = None
    turma: str | None = None
    professor: str | None = None
    equivalence: str | None = None


@dataclass
class ParsedHistorico:
    fmt: str  # "ufpel-historico" | "ufpel-integralizacao"
    start_term: str | None = None
    student_name: str | None = None
    student_id: str | None = None
    rows: list[ParsedRow] = field(default_factory=list)


# ---------- byte handling + text extraction ----------


def strip_pdf_prefix(raw: bytes) -> bytes:
    idx = raw.find(b"%PDF")
    if idx < 0:
        raise ParseError("Não parece um PDF (cabeçalho %PDF ausente).")
    return raw[idx:]


def extract_text(raw: bytes) -> str:
    import pdfplumber
    from io import BytesIO

    clean = strip_pdf_prefix(raw)
    try:
        with pdfplumber.open(BytesIO(clean)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"PDF ilegível: {exc}")
    text = "\n".join(pages)
    if len(text.strip()) < 20:
        raise ParseError("PDF sem texto extraível (provavelmente escaneado).")
    return text


def detect_format(text: str) -> str | None:
    if "Componente Curricular" in text:
        return "ufpel-historico"
    if "DISCIPLINA" in text:
        return "ufpel-integralizacao"
    return None


# ---------- format sub-parsers ----------


def _parse_historico(text: str) -> ParsedHistorico:
    out = ParsedHistorico(fmt="ufpel-historico")
    m = _INGRESS.search(text)
    if m:
        out.start_term = m.group(1)
    mn = re.search(r"Nome:\s*(.+?)\s+Matr", text)
    if mn:
        out.student_name = mn.group(1).strip()
    mi = re.search(rf"Matr[ií]cula:\s*(\d+)", text)
    if mi:
        out.student_id = mi.group(1)

    current_term: str | None = None
    for line in text.splitlines():
        line = line.strip()
        h = _HIST_HEADER.match(line)
        if h:
            current_term = h.group(1)
            continue
        lm = _HIST_LINE.match(line)
        if not lm:
            continue
        code = lm.group(1)
        tail = lm.group(2)
        tokens = tail.split()
        # name = leading words until a turma/number/situacao token
        name_words: list[str] = []
        turma = None
        grade = None
        situ = None
        for tok in tokens:
            if _TURMA.match(tok) and turma is None and name_words:
                turma = tok
                continue
            if _SITUACAO.match(tok):
                situ = tok
                continue
            if re.match(r"^\d+[.,]\d+$", tok):
                grade = tok
                continue
            if tok.isdigit():
                continue  # CR / CH
            if turma is None and situ is None:
                name_words.append(tok)
        status = _situ_status(situ)
        if status is None:
            status = CURSANDO  # code line without a resolved situação = ongoing
        out.rows.append(
            ParsedRow(
                code=code,
                name=" ".join(name_words).strip() or code,
                status=status,
                term=current_term if status != CURSANDO else None,
                grade=grade,
                turma=turma,
            )
        )
    if out.start_term is None:
        out.start_term = _min_term(out.rows)
    return out


def _parse_integralizacao(text: str) -> ParsedHistorico:
    out = ParsedHistorico(fmt="ufpel-integralizacao")
    mn = re.search(r"Nome\s+(.+)", text)
    if mn:
        out.student_name = mn.group(1).strip()
    mi = re.search(r"Matr[ií]cula\s+(\d+)", text)
    if mi:
        out.student_id = mi.group(1)

    for line in text.splitlines():
        line = line.strip()
        lm = _INT_LINE.match(line)
        if not lm:
            continue
        code, name, tail = lm.group(1), lm.group(2).strip(), lm.group(3)
        term = None
        mt = _TERM_PAREN.search(tail)
        if mt:
            term = mt.group(1)
        low = tail.lower()
        equivalence = None
        eqm = re.search(rf"\b({_CODE})\b", tail)
        if "aprovad" in low or (term is not None):
            status = APROVADA
        elif "cursando" in low or "matriculad" in low:
            status = CURSANDO
        elif "reprovad" in low:
            status = REPROVADA
        elif eqm and term is None:
            status = APROVADA  # aprovada por equivalência (sem termo)
            equivalence = eqm.group(1)
        else:
            continue  # unrecognizable line
        out.rows.append(
            ParsedRow(code=code, name=name, status=status, term=term, equivalence=equivalence)
        )
    out.start_term = _min_term(out.rows)
    return out


def _situ_status(token: str | None) -> str | None:
    if not token:
        return None
    t = token.upper()
    if t.startswith("APR"):
        return APROVADA
    if t.startswith("REPR") or t in ("RM", "RF"):
        return REPROVADA
    if t.startswith("CURS") or t.startswith("MATRIC"):
        return CURSANDO
    return None


def _min_term(rows: list[ParsedRow]) -> str | None:
    terms = [r.term for r in rows if r.term]
    if not terms:
        return None
    return min(terms, key=lambda t: (int(t[:4]), int(t[-1])))


# ---------- entry point ----------


def parse(raw: bytes) -> ParsedHistorico:
    text = extract_text(raw)
    fmt = detect_format(text)
    if fmt == "ufpel-historico":
        return _parse_historico(text)
    if fmt == "ufpel-integralizacao":
        return _parse_integralizacao(text)
    raise ParseError("Formato de PDF não reconhecido (nem histórico nem integralização).")
