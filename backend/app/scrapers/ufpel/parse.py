"""Pure HTML parsers for the UFPel institutional portal (no I/O).

Three page kinds are understood:
- discipline page  (``/disciplinas/cod/{code}``)  -> ``parse_disciplina``       (v2, kept)
- course page      (``/cursos/cod/{code}``)       -> ``parse_curso_turmas``,
                                                     ``parse_curso_optativas``,
                                                     ``parse_curso_professores`` (v3) and
                                                     ``parse_curso_curriculo``   (v5, ADR-025)
- servidor page    (``/servidores/id/{id}``)      -> ``parse_servidor_disciplinas`` (v3)

All anchors are semantic (hrefs, CSS classes, text labels) rather than bare
``<td>`` indexes wherever the real HTML allows it; the servidor table is the
exception (documented below) because its columns carry no distinguishing
markup — there we anchor on the ``/disciplinas/cod/`` link plus td position
and NEVER on "first integers of the row" (`CH *` such as "36+0" would be
misread as capacity).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ...domain.schemas import SectionIn, TimeslotIn

_WEEKDAYS = {
    "SEG": 0, "SEGUNDA": 0,
    "TER": 1, "TERCA": 1, "TERÇA": 1,
    "QUA": 2, "QUARTA": 2,
    "QUI": 3, "QUINTA": 3,
    "SEX": 4, "SEXTA": 4,
    "SAB": 5, "SÁB": 5, "SABADO": 5,
    "DOM": 6, "DOMINGO": 6,
}
_LABEL_RE = re.compile(r"^[A-Za-z]{1,3}\d{1,3}$")
_TERM_RE = re.compile(r"(\d{4})\s*/\s*([12])")
_TIME_RE = re.compile(r"([0-2]\d:[0-5]\d)\s*-\s*([0-2]\d:[0-5]\d)")

# v3 anchors (ADR-011) — tolerant to whitespace around the slash in the h3
_H3_TURMAS_RE = re.compile(r"Turmas ofertadas em\s*(\d{4})\s*/\s*([12])")
_DISC_HREF_RE = re.compile(r"/disciplinas/cod/([A-Za-z0-9]+)")
_SERVIDOR_HREF_RE = re.compile(r"/servidores/id/(\d+)")
_CURSO_HREF_RE = re.compile(r"/cursos/cod/(\d+)")
# the course page shows the professor as PLAIN TEXT inside span.tabela-detalhe-info
# (NOT a /servidores/ link) — extract by label, first match wins, None fallback
_PROF_RESP_RE = re.compile(r"Professor respons[áa]vel pela turma:\s*([^\n]+)", re.IGNORECASE)
# accordion heading that groups the electives on the course page (portal vocabulary)
_OPTATIVA_RE = re.compile(r"optativ", re.IGNORECASE)


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _merge_slots(slots: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Merge adjacent/overlapping (weekday,start,end) blocks."""
    out: list[tuple[int, int, int]] = []
    for wd, s, e in sorted(slots):
        if out and out[-1][0] == wd and s <= out[-1][2]:
            pw, ps, pe = out[-1]
            out[-1] = (pw, ps, max(pe, e))
        else:
            out.append((wd, s, e))
    return out


def _parse_grade_horarios(grade_table) -> list[TimeslotIn]:
    """Parse a `table.grade-horarios`: spans mark the weekday (empty = continue)."""
    raw: list[tuple[int, int, int]] = []
    current_wd: int | None = None
    for td in grade_table.find_all("td"):
        # walk children in order: day spans then text with time ranges
        parts = list(td.stripped_strings)
        # rebuild a linear token stream honoring <span> day markers
        for span in td.find_all("span", class_="grade-horarios-dia"):
            day = span.get_text(strip=True).upper()
            if day in _WEEKDAYS:
                current_wd = _WEEKDAYS[day]
            # times that follow this span, up to the next span, live in the tail text
        text = td.get_text(" ", strip=True)
        # fallback: scan the whole cell honoring order of day-words and times
        tokens = re.split(r"\s+", text)
        wd = None
        i = 0
        while i < len(tokens):
            tok = tokens[i].upper().strip(".")
            if tok in _WEEKDAYS:
                wd = _WEEKDAYS[tok]
                current_wd = wd
                i += 1
                continue
            m = _TIME_RE.match(" ".join(tokens[i:i + 3]))
            if m:
                use = wd if wd is not None else current_wd
                if use is not None:
                    raw.append((use, _hhmm(m.group(1)), _hhmm(m.group(2))))
                # advance past the matched "HH:MM - HH:MM"
                i += 3
                continue
            i += 1
        _ = parts  # (kept for clarity; text-based scan above is authoritative)
    merged = _merge_slots(raw)
    return [TimeslotIn(weekday=wd, start=f"{s//60:02d}:{s%60:02d}", end=f"{e//60:02d}:{e%60:02d}")
            for wd, s, e in merged]


def parse_disciplina(html: str, code: str, term: str) -> list[SectionIn]:
    """Parse "Turmas Ofertadas" from one discipline page. Testable offline."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(string=re.compile("Turmas Ofertadas"))
    if not node:
        return []
    table = node.find_parent().find_next("table")
    if table is None:
        return []

    sections: list[SectionIn] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td", recursive=False) or tr.find_all("td")
        if not tds:
            continue
        label = tds[0].get_text(" ", strip=True)
        if not _LABEL_RE.match(label):
            continue  # not a section row (header / nested schedule rows)

        row_text = tr.get_text(" ", strip=True)
        mterm = _TERM_RE.search(row_text)
        sec_term = f"{mterm.group(1)}/{mterm.group(2)}" if mterm else term
        if sec_term != term:
            continue

        # capacity / enrolled: first two standalone integers after the term token
        ints = re.findall(r"\b(\d{1,4})\b", row_text)
        # drop the year and semester from the term token
        if mterm:
            ints = [n for n in ints if n not in (mterm.group(1), mterm.group(2))]
        capacity = int(ints[0]) if len(ints) >= 1 else None
        enrolled = int(ints[1]) if len(ints) >= 2 else None

        professor = None
        prof_link = tr.find("a", href=re.compile(r"/servidores/"))
        if prof_link:
            professor = prof_link.get_text(" ", strip=True)

        timeslots: list[TimeslotIn] = []
        grade = tr.find("table", class_="grade-horarios")
        if grade is not None:
            timeslots = _parse_grade_horarios(grade)

        sections.append(
            SectionIn(
                id=f"{term.replace('/', '-')}:{code}:{label}",
                course_code=code,
                label=label,
                professor=professor,
                capacity=capacity,
                enrolled=enrolled,
                timeslots=timeslots,
            )
        )
    return sections


# ===== v3 — course page (level 1) =========================================


def _int_or_none(td) -> int | None:
    text = td.get_text(" ", strip=True)
    return int(text) if text.isdigit() else None


def _course_name_from_link(link) -> str | None:
    """Discipline link text is "CODE - NAME"; return NAME (None if absent)."""
    text = link.get_text(" ", strip=True)
    if " - " in text:
        name = text.split(" - ", 1)[1].strip()
        return name or None
    return None


def _parse_curso_row(tr, header_term: str) -> tuple[str, str | None, SectionIn] | None:
    """Parse ONE offer row of a course-page ``table.tabela-dados`` into
    ``(code, course_name, SectionIn)``, or None if it's a header/malformed row.
    The single fragile HTML walk shared by every course-page parser."""
    # direct children only: the nested table.grade-horarios has its own tds
    tds = tr.find_all("td", recursive=False)
    if len(tds) < 4:
        return None  # header row (th) or malformed
    link = tds[0].find("a", href=_DISC_HREF_RE)
    if link is None:
        return None
    code = _DISC_HREF_RE.search(link["href"]).group(1)
    label = tds[1].get_text(" ", strip=True)
    if not _LABEL_RE.match(label):
        return None  # defensive: not a section row
    name = _course_name_from_link(link)

    professor = None
    span = tds[0].find("span", class_="tabela-detalhe-info")
    if span is not None:
        # <br>-separated lines; "Professor Regente" may follow — first
        # "responsável" wins, never abort on a missing label
        pm = _PROF_RESP_RE.search(span.get_text("\n", strip=True))
        if pm:
            professor = pm.group(1).strip() or None

    timeslots: list[TimeslotIn] = []
    grade = tds[0].find("table", class_="grade-horarios")
    if grade is not None:
        timeslots = _parse_grade_horarios(grade)

    return code, name, SectionIn(
        id=f"{header_term.replace('/', '-')}:{code}:{label}",
        course_code=code,
        label=label,
        professor=professor,
        capacity=_int_or_none(tds[2]),
        enrolled=_int_or_none(tds[3]),
        timeslots=timeslots,
    )


def _curso_header_term(turmas) -> str | None:
    m = _H3_TURMAS_RE.search(turmas.get_text(" ", strip=True))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _iter_curso_rows(html: str) -> tuple[str | None, list[tuple[str, str | None, SectionIn]]]:
    """Core parser of the `div#turmas` tab: yields EVERY offered row as
    ``(code, course_name, SectionIn)``, unfiltered. The term comes ONLY from the
    ``<h3>Turmas ofertadas em AAAA / S</h3>`` heading (no term per row); no
    heading -> ``(None, [])``.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    turmas = soup.find(id="turmas")
    if turmas is None:
        return None, []
    header_term = _curso_header_term(turmas)
    if header_term is None:
        return None, []

    rows: list[tuple[str, str | None, SectionIn]] = []
    for table in turmas.find_all("table", class_="tabela-dados"):
        for tr in table.find_all("tr", recursive=False):
            parsed = _parse_curso_row(tr, header_term)
            if parsed is not None:
                rows.append(parsed)
    return header_term, rows


def parse_curso_turmas(html: str, wanted_codes: set[str]) -> tuple[str | None, list[SectionIn]]:
    """Parse the `div#turmas` tab, keeping only sections in ``wanted_codes``.

    Returns ``(header_term, sections)``; see ``_iter_curso_rows`` for the term
    source. No heading -> ``(None, [])``.
    """
    header_term, rows = _iter_curso_rows(html)
    return header_term, [sec for code, _name, sec in rows if code in wanted_codes]


def parse_curso_optativas(
    html: str,
) -> tuple[str | None, list[SectionIn], dict[str, str]]:
    """Parse ONLY the electives — rows under an "Optativas" accordion of the
    `div#turmas` tab (ADR-016). Returns ``(header_term, sections, names)`` with
    ``names`` mapping ``course_code -> course_name``.

    Anchoring on the portal's own "Optativas" heading (portal vocabulary, like
    "Turmas ofertadas em") is what keeps discovery honest: only what the portal
    itself labels elective, never "everything outside our grade".
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    turmas = soup.find(id="turmas")
    if turmas is None:
        return None, [], {}
    header_term = _curso_header_term(turmas)
    if header_term is None:
        return None, [], {}

    sections: list[SectionIn] = []
    names: dict[str, str] = {}
    for h3 in turmas.find_all("h3", attrs={"data-control": True}):
        if not _OPTATIVA_RE.search(h3.get_text(" ", strip=True)):
            continue
        content = h3.find_next_sibling(attrs={"data-content": True})
        if content is None:
            continue
        for table in content.find_all("table", class_="tabela-dados"):
            for tr in table.find_all("tr", recursive=False):
                parsed = _parse_curso_row(tr, header_term)
                if parsed is None:
                    continue
                code, name, sec = parsed
                sections.append(sec)
                if name:
                    names[code] = name
    return header_term, sections, names


def parse_curso_professores(html: str) -> list[str]:
    """Unique servidor ids from `div#professores`, in order of appearance.

    Each id shows up twice in the real HTML (photo link + `.u-url` card link);
    dedupe preserves the first occurrence.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    prof_div = soup.find(id="professores")
    if prof_div is None:
        return []
    seen: set[str] = set()
    ids: list[str] = []
    for a in prof_div.find_all("a", href=_SERVIDOR_HREF_RE):
        sid = _SERVIDOR_HREF_RE.search(a["href"]).group(1)
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


# ===== v5 — course page, "Matriz Curricular" tab (ADR-025) =================

# Portal vocabulary -> our kinds. This map is ADAPTER-OWNED on purpose
# (principle: nothing course/portal-specific hard-coded outside seed/
# importers/scrapers). Unknown strings are REPORTED, never guessed.
_KIND_MAP = {
    "obrigatória": "obrigatoria",
    "optativa": "optativa",
    "atividade complementar": "atividade",
}
# accordion heading "3º Semestre" -> suggested_term_index 3;
# "Optativas"/"Complementares" carry no index (None)
_SEMESTRE_RE = re.compile(r"(\d+)\s*º\s*Semestre", re.IGNORECASE)
# portal discipline codes are 8 digits; the "Disciplina / Pré-requisitos" cell
# concatenates name + prereq codes, so the FIRST code marks the end of the name
_CODE8_RE = re.compile(r"\b\d{8}\b")


@dataclass(frozen=True)
class MatrixRow:
    """One discipline of the curriculum matrix (`div#curriculo`) — the CATALOG
    (what exists and with which kind/credits/hours), never the offer."""

    code: str
    name: str
    kind: str  # normalized: obrigatoria | optativa | atividade
    portal_kind: str  # raw portal string ("Optativa") — audit trail
    credits: int
    hours: float
    prereqs: tuple[str, ...]  # deduped, sorted
    suggested_term_index: int | None  # from the "Nº Semestre" heading


def _parse_curriculo_row(tr):
    """Parse ONE row of a `div#curriculo` table into
    ``(code, name, portal_kind, credits, hours, prereqs)`` or None (header /
    malformed row). Columns: Código | Disciplina / Pré-requisitos | Caráter |
    Cr. | Horas."""
    tds = tr.find_all("td", recursive=False)
    if len(tds) < 5:
        return None  # header row (th only)
    link = tds[0].find("a", href=_DISC_HREF_RE)
    if link is None:
        return None
    code = _DISC_HREF_RE.search(link["href"]).group(1)

    # name: the discipline link text is the clean name; fallback = cell text up
    # to the FIRST 8-digit code (the cell concatenates name + prereqs)
    cell_text = tds[1].get_text(" ", strip=True)
    name_link = tds[1].find("a", href=_DISC_HREF_RE)
    name = name_link.get_text(" ", strip=True) if name_link is not None else ""
    if not name:
        m = _CODE8_RE.search(cell_text)
        name = (cell_text[: m.start()] if m else cell_text).strip()
    if not name:
        name = code

    # prereqs: every 8-digit code of the cell except the row's own (dedupe, sorted)
    prereqs = tuple(sorted({c for c in _CODE8_RE.findall(cell_text) if c != code}))

    portal_kind = tds[2].get_text(" ", strip=True)
    try:
        credits = int(tds[3].get_text(" ", strip=True))
        hours = float(tds[4].get_text(" ", strip=True))
    except ValueError:
        return None  # malformed numeric cells — not a discipline row
    return code, name, portal_kind, credits, hours, prereqs


def parse_curso_curriculo(html: str) -> tuple[list[MatrixRow], list[dict]]:
    """Parse the `div#curriculo` tab ("Matriz Curricular") of a course page.

    Returns ``(rows, unknown_kind)``:
    - ``rows``: one ``MatrixRow`` per discipline, deduped by code (FIRST
      occurrence wins — the portal shows two "Optativas" accordions);
    - ``unknown_kind``: ``[{code, portal_kind}]`` for rows whose "Caráter" is
      outside ``_KIND_MAP`` (ignored, reported — never guessed).

    Missing tab -> ``([], [])``. Pure and offline-testable; called on the SAME
    course-page HTML the level-1 scrape already downloads (zero extra GETs).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    tab = soup.find(id="curriculo")
    if tab is None:
        return [], []

    rows: list[MatrixRow] = []
    unknown: list[dict] = []
    seen: set[str] = set()
    seen_unknown: set[str] = set()
    for h3 in tab.find_all("h3", attrs={"data-control": True}):
        m = _SEMESTRE_RE.search(h3.get_text(" ", strip=True))
        sem = int(m.group(1)) if m else None
        content = h3.find_next_sibling(attrs={"data-content": True})
        if content is None:
            continue
        for table in content.find_all("table", class_="tabela-disciplinas"):
            for tr in table.find_all("tr"):
                parsed = _parse_curriculo_row(tr)
                if parsed is None:
                    continue
                code, name, portal_kind, credits, hours, prereqs = parsed
                if code in seen:
                    continue  # dedupe across accordions — first occurrence wins
                kind = _KIND_MAP.get(portal_kind.strip().casefold())
                if kind is None:
                    if code not in seen_unknown:
                        seen_unknown.add(code)
                        unknown.append({"code": code, "portal_kind": portal_kind})
                    continue
                seen.add(code)
                rows.append(
                    MatrixRow(
                        code=code,
                        name=name,
                        kind=kind,
                        portal_kind=portal_kind,
                        credits=credits,
                        hours=hours,
                        prereqs=prereqs,
                        suggested_term_index=sem,
                    )
                )
    return rows, unknown


# ===== v3 — servidor page (level 2) ========================================


def _servidor_name(soup) -> str | None:
    """Professor name from the ficha ("Nome do Servidor" label). None fallback."""
    label = soup.find("div", class_="ficha-label", string=re.compile("Nome do Servidor"))
    if label is None:
        return None
    campo = label.find_next_sibling("div", class_="ficha-campo")
    if campo is None:
        return None
    return campo.get_text(" ", strip=True) or None


def parse_servidor_disciplinas(
    html: str, term: str, wanted_codes: set[str], portal_course_code: str
) -> list[SectionIn]:
    """Parse `div#disciplinas` of a servidor page (columns Ano|Turma|Disciplina|CH *|Curso).

    The real HTML is malformed (an orphan ``</a>`` after the Turma td);
    html.parser tolerates it, keeping the five tds as direct children of the
    row — so association is by td POSITION plus the ``/disciplinas/cod/`` href.
    ``CH *`` (e.g. "36+0") is workload, NOT capacity: capacity/enrolled stay
    None here. Rows offered to other courses are accepted with a note
    (ADR-014); the note lists the destination course names. v3 (ADR-020):
    the same list is ALSO emitted structured in ``offered_to`` — the note
    stays byte-for-byte identical for UI/back-compat.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    disc = soup.find(id="disciplinas")
    if disc is None:
        return []
    professor = _servidor_name(soup)

    sections: list[SectionIn] = []
    for table in disc.find_all("table", class_="tabela-dados"):
        for tr in table.find_all("tr", recursive=False):
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 5:
                continue  # header row (th only)
            if tds[0].get_text(" ", strip=True) != term:
                continue
            link = tds[2].find("a", href=_DISC_HREF_RE)
            if link is None:
                continue
            code = _DISC_HREF_RE.search(link["href"]).group(1)
            if code not in wanted_codes:
                continue
            label = tds[1].get_text(" ", strip=True) or None

            timeslots: list[TimeslotIn] = []
            grade = tds[2].find("table", class_="grade-horarios")
            if grade is not None:
                timeslots = _parse_grade_horarios(grade)

            course_links = tds[4].find_all("a", href=_CURSO_HREF_RE)
            course_codes = [
                _CURSO_HREF_RE.search(a["href"]).group(1) for a in course_links
            ]
            note = None
            offered_to: list[str] | None = None
            if portal_course_code not in course_codes:
                names = [a.get_text(" ", strip=True) for a in course_links]
                names = [n for n in names if n] or course_codes
                note = "ofertada para: " + ", ".join(names)
                # ADR-020: structured twin of the note (names; codes fallback).
                # Empty list (row without course links) stays None: "unknown".
                offered_to = names or None

            sections.append(
                SectionIn(
                    id=f"{term.replace('/', '-')}:{code}:{label or 'T'}",
                    course_code=code,
                    label=label,
                    professor=professor,
                    capacity=None,   # CH * is workload — never capacity (spec 4.4)
                    enrolled=None,
                    note=note,
                    offered_to=offered_to,
                    timeslots=timeslots,
                )
            )
    return sections
