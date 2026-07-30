"""Pure graph helpers: Kahn topo-sort, cycle extraction, levels and heights."""
from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Iterable, Mapping


def internal_prereqs(
    codes: Iterable[str], prereqs_of: Mapping[str, Iterable[str]]
) -> dict[str, tuple[str, ...]]:
    """Prereq edges restricted to the given node set."""
    code_set = set(codes)
    return {
        c: tuple(sorted(p for p in prereqs_of.get(c, ()) if p in code_set))
        for c in code_set
    }


def topo_sort(
    codes: Iterable[str], prereqs_of: Mapping[str, Iterable[str]]
) -> tuple[list[str], list[str] | None]:
    """Kahn's algorithm on the subgraph induced by `codes`.

    Returns (topological_order, cycle). `cycle` is None when acyclic, otherwise a
    list of codes forming one cycle (first == last is NOT repeated).
    """
    pre = internal_prereqs(codes, prereqs_of)
    dependents: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {}
    for c, ps in pre.items():
        indeg[c] = len(ps)
        for p in ps:
            dependents[p].append(c)

    heap = [c for c, d in indeg.items() if d == 0]
    heapq.heapify(heap)  # deterministic order
    order: list[str] = []
    while heap:
        c = heapq.heappop(heap)
        order.append(c)
        for d in sorted(dependents.get(c, ())):
            indeg[d] -= 1
            if indeg[d] == 0:
                heapq.heappush(heap, d)

    if len(order) == len(pre):
        return order, None

    # extract one cycle among the remaining nodes for reporting
    remaining = set(pre) - set(order)
    start = min(remaining)
    seen: list[str] = []
    seen_set: set[str] = set()
    cur = start
    while cur not in seen_set:
        seen.append(cur)
        seen_set.add(cur)
        cur = min(p for p in pre[cur] if p in remaining)
    cycle = seen[seen.index(cur):]
    return order, cycle


def earliest_levels(
    order: list[str], pre: Mapping[str, tuple[str, ...]]
) -> dict[str, int]:
    """earliest_level(c) = 1 + max(earliest_level(prereqs within set)), min 1."""
    lvl: dict[str, int] = {}
    for c in order:
        lvl[c] = 1 + max((lvl[p] for p in pre[c]), default=0)
    return lvl


def heights(order: list[str], pre: Mapping[str, tuple[str, ...]]) -> dict[str, int]:
    """height(c) = length of the longest dependent chain starting at c (>= 1)."""
    dependents: dict[str, list[str]] = defaultdict(list)
    for c, ps in pre.items():
        for p in ps:
            dependents[p].append(c)
    h: dict[str, int] = {}
    for c in reversed(order):
        h[c] = 1 + max((h[d] for d in dependents.get(c, ())), default=0)
    return h
