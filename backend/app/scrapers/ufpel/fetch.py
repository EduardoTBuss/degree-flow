"""Network layer of the UFPel scraper — the ONLY place that touches the wire.

Kept apart from the parsers so every parse function stays pure and testable
offline (ARCHITECTURE-v2 §9 / ARCHITECTURE-v3 ADR-011).
"""
from __future__ import annotations

import urllib.request

DISCIPLINA_URL = "https://institucional.ufpel.edu.br/disciplinas/cod/{code}"
CURSO_URL = "https://institucional.ufpel.edu.br/cursos/cod/{code}"
SERVIDOR_URL = "https://institucional.ufpel.edu.br/servidores/id/{id}"

USER_AGENT = "degree-flow/0.1 (graduation planner; contact via repository)"
_RATE_LIMIT_S = 0.7  # pause between requests (be polite to the portal)


def _fetch_url(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed host
        return resp.read().decode("utf-8", "replace")
