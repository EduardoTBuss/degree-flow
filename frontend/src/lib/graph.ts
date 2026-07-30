import type { CurriculumCourse } from "../api/types";

export interface CourseGraph {
  /** code -> codes that directly depend on it */
  dependents: Map<string, string[]>;
  /** code -> direct prereq codes */
  prereqs: Map<string, string[]>;
}

export function buildGraph(courses: CurriculumCourse[]): CourseGraph {
  const dependents = new Map<string, string[]>();
  const prereqs = new Map<string, string[]>();
  const known = new Set(courses.map((c) => c.code));
  for (const c of courses) {
    const ps = c.prereqs.filter((p) => known.has(p));
    prereqs.set(c.code, ps);
    for (const p of ps) {
      const list = dependents.get(p) ?? [];
      list.push(c.code);
      dependents.set(p, list);
    }
  }
  return { dependents, prereqs };
}

function collect(start: string, edges: Map<string, string[]>): Set<string> {
  const seen = new Set<string>();
  const stack = [...(edges.get(start) ?? [])];
  while (stack.length) {
    const cur = stack.pop()!;
    if (seen.has(cur)) continue;
    seen.add(cur);
    stack.push(...(edges.get(cur) ?? []));
  }
  return seen;
}

/** Everything downstream: the chain that depends on `code`. */
export function collectDescendants(graph: CourseGraph, code: string): Set<string> {
  return collect(code, graph.dependents);
}

/** Everything upstream: the prereq chain feeding `code`. */
export function collectAncestors(graph: CourseGraph, code: string): Set<string> {
  return collect(code, graph.prereqs);
}
