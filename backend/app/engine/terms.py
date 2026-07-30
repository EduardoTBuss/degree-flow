"""Pure helpers for absolute term strings "YYYY/S" (ADR-004). No I/O."""
from __future__ import annotations

import re

TERM_RE = re.compile(r"^\d{4}/[12]$")


def is_valid_term(term: str | None) -> bool:
    return bool(term) and bool(TERM_RE.match(term))


def term_key(term: str) -> tuple[int, int]:
    year, sem = term.split("/")
    return (int(year), int(sem))


def term_lt(a: str, b: str) -> bool:
    return term_key(a) < term_key(b)


def term_le(a: str, b: str) -> bool:
    return term_key(a) <= term_key(b)


def term_gt(a: str, b: str) -> bool:
    return term_key(a) > term_key(b)


def term_ge(a: str, b: str) -> bool:
    return term_key(a) >= term_key(b)


def term_parity(term: str) -> str:
    """Returns '/1' or '/2'."""
    return "/1" if term.endswith("/1") else "/2"


def add_terms(term: str, n: int) -> str:
    """Term n semesters after `term` (n may be negative)."""
    year, sem = term_key(term)
    idx = year * 2 + (sem - 1) + n
    return f"{idx // 2}/{idx % 2 + 1}"


def terms_until(a: str, b: str) -> int:
    """Number of semester steps from a to b (b - a); negative if b < a."""
    ya, sa = term_key(a)
    yb, sb = term_key(b)
    return (yb * 2 + sb) - (ya * 2 + sa)


def next_term(term: str) -> str:
    return add_terms(term, 1)
