"""Cobalto 'situação' -> app status (declarative)."""
from __future__ import annotations

APROVADA = "aprovada"
CURSANDO = "cursando"
REPROVADA = "reprovada"  # internal: never becomes a match (A5 -> stays 'falta')

_MAP = {
    "APR": APROVADA,
    "APROVADO": APROVADA,
    "APROVADA": APROVADA,
    "CURSANDO": CURSANDO,
    "MATRICULADO": CURSANDO,
    "REPR": REPROVADA,
    "REPROVADO": REPROVADA,
    "REPROVADA": REPROVADA,
    "RM": REPROVADA,   # reprovado por média
    "RF": REPROVADA,   # reprovado por falta
}


def situacao_to_status(token: str | None) -> str | None:
    if not token:
        return None
    return _MAP.get(token.strip().upper())
