"""UFPel institutional portal scraper — isolated adapter (ADR-008, ADR-011).

Produces the SAME `SectionsPayload` accepted by the manual import endpoint, so
the core never depends on it. If the portal HTML changes, only this package
breaks — everything else keeps working with manually imported data.

Layout (ARCHITECTURE-v2 §9 / ARCHITECTURE-v3 §7):
- ``parse.py``       pure parsers (offline-testable, zero I/O)
- ``fetch.py``       the only module touching the network
- ``orchestrate.py`` 2-level strategy (``scrape_offers``) + legacy ``scrape_term``
"""
from __future__ import annotations

from .fetch import _RATE_LIMIT_S, CURSO_URL, DISCIPLINA_URL, SERVIDOR_URL, USER_AGENT
from .parse import (
    MatrixRow,
    _merge_slots,
    _parse_grade_horarios,
    parse_curso_curriculo,
    parse_curso_optativas,
    parse_curso_professores,
    parse_curso_turmas,
    parse_disciplina,
    parse_servidor_disciplinas,
)
from .orchestrate import (
    REASON_NO_PORTAL_CODE,
    SOURCE_CURSO,
    SOURCE_PROFESSORES,
    ScrapeResult,
    scrape_offers,
    scrape_term,
)

__all__ = [
    "_RATE_LIMIT_S",
    "CURSO_URL",
    "DISCIPLINA_URL",
    "SERVIDOR_URL",
    "USER_AGENT",
    "MatrixRow",
    "_merge_slots",
    "_parse_grade_horarios",
    "parse_curso_curriculo",
    "parse_curso_optativas",
    "parse_curso_professores",
    "parse_curso_turmas",
    "parse_disciplina",
    "parse_servidor_disciplinas",
    "REASON_NO_PORTAL_CODE",
    "SOURCE_CURSO",
    "SOURCE_PROFESSORES",
    "ScrapeResult",
    "scrape_offers",
    "scrape_term",
]
