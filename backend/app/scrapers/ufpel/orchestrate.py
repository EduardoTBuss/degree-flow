"""Scrape orchestration — the only functions of the flow with I/O (ADR-011).

``scrape_offers`` implements the 2-level strategy:
- level 1: one GET on the course page; used when the "Turmas ofertadas" tab
  header matches the target term AND at least one wanted section is there.
- level 2 (fallback): visit the professor pages listed on the same course
  page, rate-limited. A failing professor page is counted in ``pages_failed``
  and never aborts the batch; a failing COURSE page propagates (router turns
  it into 502).

Optativa discovery (ADR-016): when ``discover=True`` and the course page shows
the target term, every offered discipline that is NOT a wanted (grade) course
and NOT already done is returned in ``discovered_sections`` (+ ``discovered_names``)
so the router can materialize them as read-only elective offers.

Part B (exhaustive catalog-scoped discovery): electives of the curriculum
matrix are often offered ONLY on professor pages (never on the course page's
"Turmas ofertadas" tab). When ``discover=True`` the professor sweep therefore:
- widens its scope to ``portal_codes ∪ catalog optativa codes`` (the catalog
  is parsed from the level-1 HTML, so this never inflates beyond the course's
  real electives);
- becomes EXHAUSTIVE (no early-stop) — an elective taught by the last
  professor of the list must not be lost;
- runs even when level 1 already resolved the grade faltantes (mixed mode:
  the grade offer keeps ``level_used=1``/``SOURCE_CURSO`` because it came from
  the course page; the extra GETs only show up in ``pages_fetched``/``failed``).
When ``discover=False`` the legacy behaviour (early-stop, level-1 short
circuit, grade-only scope) is preserved byte-for-byte.

``fetch`` is injectable so every test runs offline against fixtures.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ...domain.schemas import SectionIn, SectionsPayload
from .fetch import CURSO_URL, DISCIPLINA_URL, SERVIDOR_URL, _RATE_LIMIT_S, _fetch_url
from .parse import (
    MatrixRow,
    parse_curso_curriculo,
    parse_curso_optativas,
    parse_curso_professores,
    parse_curso_turmas,
    parse_disciplina,
    parse_servidor_disciplinas,
)

SOURCE_CURSO = "scraper:ufpel-curso"
SOURCE_PROFESSORES = "scraper:ufpel-professores"

_PORTAL_CODE_RE = re.compile(r"\d+")
REASON_NO_PORTAL_CODE = "sem código no portal (cadeira unificada da reforma)"


@dataclass
class ScrapeResult:
    payload: SectionsPayload
    level_used: int  # 1 | 2
    found_codes: set[str] = field(default_factory=set)
    missing: list[dict] = field(default_factory=list)  # [{code, reason}] — no name (router joins)
    pages_fetched: int = 0
    pages_failed: int = 0
    # ADR-016 optativa discovery (empty unless discover=True and course page shows the term)
    discovered_sections: list[SectionIn] = field(default_factory=list)
    discovered_names: dict[str, str] = field(default_factory=dict)
    # v5 (ADR-025) curriculum-matrix CATALOG, parsed from the SAME course-page
    # HTML of level 1 (zero extra GETs; empty unless discover=True). A failing
    # matrix parse NEVER aborts the offer scrape: catalog stays empty and
    # catalog_error carries the reason (the router reports it, ADR-027).
    catalog: list[MatrixRow] = field(default_factory=list)
    catalog_unknown: list[dict] = field(default_factory=list)  # [{code, portal_kind}]
    catalog_error: str | None = None


def _discover_from_curso(
    curso_html: str,
    term: str,
    portal_codes: set[str],
    done_codes: set[str],
) -> tuple[list[SectionIn], dict[str, str]]:
    """Offered disciplines on the course page that are neither wanted (grade)
    nor already done — candidate electives for the target term (ADR-016)."""
    header_term, opt_sections, names = parse_curso_optativas(curso_html)
    if header_term != term:
        return [], {}
    # keep only electives the user has not done and that are not already grade
    # courses handled as faltantes (portal_codes)
    keep = {
        s.course_code for s in opt_sections
        if s.course_code not in portal_codes and s.course_code not in done_codes
    }
    sections = [s for s in opt_sections if s.course_code in keep]
    return sections, {c: names[c] for c in keep if c in names}


def _sweep_professores(
    curso_html: str,
    term: str,
    portal_codes: set[str],
    discover_codes: set[str],
    portal_course_code: str,
    sleep: float,
    fetch,
    exhaustive: bool,
) -> tuple[dict[str, SectionIn], dict[str, SectionIn], int, int]:
    """Level-2 loop over the professor pages of an already-downloaded course page.

    Returns ``(grade_by_id, discovered_by_id, pages_fetched, pages_failed)``.
    A section whose course is in ``portal_codes`` is a GRADE offer; one in
    ``discover_codes`` (catalog electives — Part B) is a DISCOVERY. Dedupe is
    by section id, first occurrence wins (same rule as before Part B).

    ``exhaustive=False`` keeps the legacy early-stop (break once every portal
    code was found); exhaustive sweeps visit EVERY professor so an elective
    offered only by a late professor is never lost.
    """
    wanted = portal_codes | discover_codes
    grade_by_id: dict[str, SectionIn] = {}
    disc_by_id: dict[str, SectionIn] = {}
    found: set[str] = set()
    fetched = failed = 0
    for servidor_id in parse_curso_professores(curso_html):
        if not exhaustive and portal_codes <= found:
            break  # early-stop: every wanted code already found
        if sleep:
            time.sleep(sleep)
        try:
            html = fetch(SERVIDOR_URL.format(id=servidor_id))
        except Exception:  # noqa: BLE001 - one page must not abort the batch
            failed += 1
            continue
        fetched += 1
        for sin in parse_servidor_disciplinas(html, term, wanted, portal_course_code):
            if sin.course_code in portal_codes:
                if sin.id not in grade_by_id:
                    grade_by_id[sin.id] = sin
                    found.add(sin.course_code)
            elif sin.id not in disc_by_id:
                disc_by_id[sin.id] = sin
    return grade_by_id, disc_by_id, fetched, failed


def _merge_discoveries(
    level1_sections: list[SectionIn],
    level1_names: dict[str, str],
    level2_by_id: dict[str, SectionIn],
    catalog: list[MatrixRow],
) -> tuple[list[SectionIn], dict[str, str]]:
    """Fuse level-1 (course page) and level-2 (professor pages) discoveries.

    Dedupe by section id — level 1 wins ties (its rows carry capacity/enrolled,
    which the servidor table never has). Names of level-2-only codes resolve
    from the CATALOG (``MatrixRow.name``): the servidor row carries no course
    name, and the response must never show a bare code.
    """
    by_id: dict[str, SectionIn] = {}
    for sec in level1_sections:
        by_id.setdefault(sec.id, sec)
    for sid, sec in level2_by_id.items():
        by_id.setdefault(sid, sec)
    names = dict(level1_names)
    catalog_names = {r.code: r.name for r in catalog}
    for sec in by_id.values():
        if sec.course_code not in names and sec.course_code in catalog_names:
            names[sec.course_code] = catalog_names[sec.course_code]
    return list(by_id.values()), names


def scrape_offers(
    portal_course_code: str,
    term: str,
    wanted_codes: set[str],
    sleep: float = _RATE_LIMIT_S,
    fetch=_fetch_url,
    done_codes: set[str] | None = None,
    discover: bool = False,
) -> ScrapeResult:
    """Collect the offer of `term` for `wanted_codes` (2-level strategy).

    Synthetic codes (anything not fully numeric, e.g. ``MERGE:…``) NEVER
    produce a GET: they go straight to ``missing`` with the reform reason (C4).

    ``discover``/``done_codes`` drive ADR-016 elective discovery from the course
    page PLUS the Part-B exhaustive professor sweep scoped to the catalog's
    optativa codes (see module docstring). In mixed mode (level 1 resolved the
    grade faltantes AND the sweep ran for discovery) ``level_used`` stays 1 and
    ``payload.source`` stays ``SOURCE_CURSO``: the criterion is WHERE THE GRADE
    OFFER CAME FROM — discovery GETs are visible in ``pages_fetched``/``failed``.
    """
    done_codes = done_codes or set()
    portal_codes = {c for c in wanted_codes if _PORTAL_CODE_RE.fullmatch(c)}
    synthetic = sorted(set(wanted_codes) - portal_codes)
    missing = [{"code": c, "reason": REASON_NO_PORTAL_CODE} for c in synthetic]

    if not portal_codes and not discover:
        # nothing scrapeable — empty result, zero requests (200 upstream, not an error)
        return ScrapeResult(
            payload=SectionsPayload(term=term, sections=[], source=SOURCE_CURSO),
            level_used=1,
            missing=missing,
        )

    # ----- level 1: course page (exceptions propagate -> 502 in the router)
    curso_html = fetch(CURSO_URL.format(code=portal_course_code))
    pages_fetched = 1
    header_term, sections = parse_curso_turmas(curso_html, portal_codes)

    disc_sections: list[SectionIn] = []
    disc_names: dict[str, str] = {}
    catalog: list[MatrixRow] = []
    catalog_unknown: list[dict] = []
    catalog_error: str | None = None
    if discover:
        disc_sections, disc_names = _discover_from_curso(
            curso_html, term, portal_codes, done_codes
        )
        # v5 (ADR-025): the CATALOG comes from the same HTML — isolated
        # try/except so a broken matrix tab never aborts the OFFER collection.
        try:
            catalog, catalog_unknown = parse_curso_curriculo(curso_html)
            if not catalog:
                catalog_error = "aba #curriculo não encontrada ou sem linhas"
        except Exception as exc:  # noqa: BLE001 - matrix parse must not kill the scrape
            catalog, catalog_unknown = [], []
            catalog_error = f"falha no parse da matriz curricular: {exc}"

    # Part B: catalog electives worth hunting on professor pages — never grade
    # faltantes (those are `wanted`), never done. Scoping to the CATALOG (the
    # matrix parsed above, from the level-1 HTML) keeps the sweep honest: only
    # real electives of this course, no cross-course noise. Empty when the
    # catalog parse failed/was skipped — then the legacy behaviour applies.
    l2_discover: set[str] = set()
    if discover:
        l2_discover = {
            r.code for r in catalog if r.kind == "optativa"
        } - portal_codes - done_codes

    # Level 1 wins only when it found at least one WANTED section of the target
    # term (ADR-011: "zero turmas do alvo -> nível 2"). Discovered optativas
    # alone never satisfy level 1 — a faltante may be offered to another course
    # and only visible on professor pages (ADR-014). Discoveries survive either way.
    if header_term == term and sections:
        found = {s.course_code for s in sections}
        missing += [
            {"code": c, "reason": f"nenhuma turma encontrada para {term}"}
            for c in sorted(portal_codes - found)
        ]
        pages_failed = 0
        if l2_discover:
            # Part B mixed mode: the grade offer is settled (level 1), but
            # electives offered ONLY on professor pages still need the
            # exhaustive sweep (empty portal scope: it collects discoveries only).
            _grade, disc2_by_id, swept, pages_failed = _sweep_professores(
                curso_html, term, set(), l2_discover, portal_course_code,
                sleep, fetch, exhaustive=True,
            )
            pages_fetched += swept
            disc_sections, disc_names = _merge_discoveries(
                disc_sections, disc_names, disc2_by_id, catalog
            )
        return ScrapeResult(
            payload=SectionsPayload(term=term, sections=sections, source=SOURCE_CURSO),
            level_used=1,
            found_codes=found,
            missing=missing,
            pages_fetched=pages_fetched,
            pages_failed=pages_failed,
            discovered_sections=disc_sections,
            discovered_names=disc_names,
            catalog=catalog,
            catalog_unknown=catalog_unknown,
            catalog_error=catalog_error,
        )

    if not portal_codes:
        # discover-only run with no target-term data on the course page —
        # professor pages may still offer catalog electives for the term (Part B)
        pages_failed = 0
        if l2_discover:
            _grade, disc2_by_id, swept, pages_failed = _sweep_professores(
                curso_html, term, set(), l2_discover, portal_course_code,
                sleep, fetch, exhaustive=True,
            )
            pages_fetched += swept
            disc_sections, disc_names = _merge_discoveries(
                disc_sections, disc_names, disc2_by_id, catalog
            )
        return ScrapeResult(
            payload=SectionsPayload(term=term, sections=[], source=SOURCE_CURSO),
            level_used=1,
            missing=missing,
            pages_fetched=pages_fetched,
            pages_failed=pages_failed,
            discovered_sections=disc_sections,
            discovered_names=disc_names,
            catalog=catalog,
            catalog_unknown=catalog_unknown,
            catalog_error=catalog_error,
        )

    # ----- level 2: professor pages (ids from the page already downloaded).
    # Grade scope AND (Part B) elective discovery in one sweep; exhaustive
    # exactly when there is something to discover, legacy early-stop otherwise.
    grade_by_id, disc2_by_id, swept, pages_failed = _sweep_professores(
        curso_html, term, portal_codes, l2_discover, portal_course_code,
        sleep, fetch, exhaustive=bool(l2_discover),
    )
    pages_fetched += swept
    found = {s.course_code for s in grade_by_id.values()}
    if l2_discover:
        disc_sections, disc_names = _merge_discoveries(
            disc_sections, disc_names, disc2_by_id, catalog
        )

    missing += [
        {"code": c, "reason": f"nenhuma turma encontrada para {term}"}
        for c in sorted(portal_codes - found)
    ]
    return ScrapeResult(
        payload=SectionsPayload(
            term=term, sections=list(grade_by_id.values()), source=SOURCE_PROFESSORES
        ),
        level_used=2,
        found_codes=found,
        missing=missing,
        pages_fetched=pages_fetched,
        pages_failed=pages_failed,
        discovered_sections=disc_sections,
        discovered_names=disc_names,
        # v5: the catalog rode along from the level-1 page — level 2 keeps it
        catalog=catalog,
        catalog_unknown=catalog_unknown,
        catalog_error=catalog_error,
    )


# ===== legacy utility (v2) — kept for manual use, no longer API-exposed =====


def _fetch_disciplina(code: str) -> str:
    return _fetch_url(DISCIPLINA_URL.format(code=code))


def scrape_term(term: str, codes: list[str], sleep: float = _RATE_LIMIT_S) -> SectionsPayload:
    """Fetch every course page (rate-limited) and collect the term's sections."""
    all_sections: list[SectionIn] = []
    for i, code in enumerate(codes):
        if i:
            time.sleep(sleep)
        try:
            html = _fetch_disciplina(code)
        except Exception:  # noqa: BLE001 - a single page failing must not abort the batch
            continue
        all_sections.extend(parse_disciplina(html, code, term))
    return SectionsPayload(term=term, sections=all_sections, source="scraper:ufpel")
