"""Critical path: longest prereq chain among remaining mandatory courses. Pure."""
from __future__ import annotations

from .graph import earliest_levels, internal_prereqs, topo_sort
from .terms import add_terms
from .types import (
    CURSANDO,
    FALTA,
    KIND_OBRIGATORIA,
    PENDENTE,
    CriticalPath,
    CurriculumSnapshot,
    Diagnostic,
    UserState,
)


def remaining_required(curriculum: CurriculumSnapshot, user_state: UserState) -> set[str]:
    """Mandatory courses still to be taken. `cursando` counts as concluded at the
    end of current_term (section 8, note 1), so it is not 'remaining'."""
    return {
        code
        for code, info in curriculum.courses.items()
        if info.kind == KIND_OBRIGATORIA
        and user_state.status_of(code) in (FALTA, PENDENTE)
    }


def cycle_diagnostic(cycle: list[str]) -> Diagnostic:
    chain = " -> ".join(cycle + [cycle[0]])
    return Diagnostic(
        id="PREREQ_CYCLE:" + "->".join(cycle),
        type="PREREQ_CYCLE",
        severity="error",
        course_codes=tuple(cycle),
        related_codes=(),
        term=None,
        message=f"Ciclo de pré-requisitos detectado (dado do seed corrompido): {chain}.",
        details={"cycle": list(cycle)},
    )


def compute_critical_path(
    curriculum: CurriculumSnapshot,
    user_state: UserState,
    current_term: str,
) -> tuple[CriticalPath, list[Diagnostic]]:
    """Longest chain of prereqs among non-approved mandatory courses.

    length_terms = minimum number of semesters still needed;
    earliest_completion_term = current_term + length (first plannable term is
    current_term + 1, so a chain of L finishes at current_term + L).
    Returns (CriticalPath, diagnostics) — diagnostics non-empty only on cycle.
    """
    remaining = remaining_required(curriculum, user_state)
    prereqs_of = {c: curriculum.courses[c].prereqs for c in remaining}
    order, cycle = topo_sort(remaining, prereqs_of)
    if cycle is not None:
        return CriticalPath((), 0, None), [cycle_diagnostic(cycle)]

    if not remaining:
        return CriticalPath((), 0, current_term), []

    pre = internal_prereqs(remaining, prereqs_of)
    lvl = earliest_levels(order, pre)
    length = max(lvl.values())

    # backtrack a chain realizing the max level (deterministic: min code)
    end = min(c for c in remaining if lvl[c] == length)
    chain = [end]
    cur = end
    while lvl[cur] > 1:
        cur = min(p for p in pre[cur] if lvl[p] == lvl[cur] - 1)
        chain.append(cur)
    chain.reverse()

    return (
        CriticalPath(tuple(chain), length, add_terms(current_term, length)),
        [],
    )
