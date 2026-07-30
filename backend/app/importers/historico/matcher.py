"""ParsedHistorico × catalog -> ImportProposal. Match by numeric code."""
from __future__ import annotations

from datetime import datetime, timezone

from .parser import APROVADA, CURSANDO, REPROVADA, ParsedHistorico, ParsedRow


def _resolve_per_code(rows: list[ParsedRow]) -> tuple[dict[str, ParsedRow], int]:
    """One row per code: aprovada > cursando > (reprovada dropped). Returns
    (resolved, reproved_only_count)."""
    by_code: dict[str, list[ParsedRow]] = {}
    for r in rows:
        by_code.setdefault(r.code, []).append(r)

    resolved: dict[str, ParsedRow] = {}
    reproved_only = 0
    for code, rs in by_code.items():
        apr = [r for r in rs if r.status == APROVADA]
        cur = [r for r in rs if r.status == CURSANDO]
        if apr:
            # prefer the one carrying a term
            resolved[code] = next((r for r in apr if r.term), apr[0])
        elif cur:
            resolved[code] = cur[0]
        elif all(r.status == REPROVADA for r in rs):
            reproved_only += 1  # A5: stays 'falta', no match
    return resolved, reproved_only


def build_proposal(
    parsed: ParsedHistorico,
    catalog_codes: set[str],
    current_states: dict[str, tuple[str, str | None]],
    filename: str | None = None,
) -> dict:
    resolved, reproved_only = _resolve_per_code(parsed.rows)

    matches: list[dict] = []
    unmatched: list[dict] = []
    for code, row in sorted(resolved.items()):
        if code in catalog_codes:
            cur_status, cur_term = current_states.get(code, ("falta", None))
            conflict = cur_status != row.status or (
                row.status == APROVADA and row.term is not None and cur_term != row.term
            )
            matches.append(
                {
                    "code": code,
                    "name_in_pdf": row.name,
                    "term": row.term,
                    "status_inferred": row.status,
                    "grade_in_pdf": row.grade,
                    "current_status": cur_status,
                    "current_completed_term": cur_term,
                    "conflict": conflict,
                    "apply": True,
                }
            )
        else:
            unmatched.append(
                {
                    "code_in_pdf": code,
                    "name_in_pdf": row.name,
                    "term": row.term,
                    "status_inferred": row.status,
                    "hint": "código fora da grade ativa; pode ser equivalência, optativa ou atividade",
                }
            )

    warnings: list[str] = []
    if reproved_only:
        warnings.append(
            f"{reproved_only} disciplina(s) reprovada(s) sem reaprovação ficam como 'falta'."
        )
    missing_term = sum(1 for m in matches if m["status_inferred"] == APROVADA and not m["term"])
    if missing_term:
        warnings.append(
            f"{missing_term} aprovada(s) sem semestre (ex.: equivalência) — ficam na coluna 'Concluídas'."
        )

    return {
        "source": {
            "filename": filename,
            "parser": parsed.fmt,
            "parser_version": "1",
            "parsed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "inferred": {
            "start_term": parsed.start_term,
            "student_name": parsed.student_name,
            "student_id": parsed.student_id,
        },
        "matches": matches,
        "unmatched": unmatched,
        "unparsed_lines": [],
        "warnings": warnings,
    }
