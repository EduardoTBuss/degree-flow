// Absolute term "AAAA/S" helpers (ADR-004).

export const TERM_RE = /^\d{4}\/[12]$/;

export function isValidTerm(t: string): boolean {
  return TERM_RE.test(t);
}

/** "2026/2" -> monotonic integer for ordering/arithmetic. */
export function termToIndex(term: string): number {
  const [y, s] = term.split("/");
  return parseInt(y, 10) * 2 + (parseInt(s, 10) - 1);
}

export function indexToTerm(idx: number): string {
  const y = Math.floor(idx / 2);
  const s = (idx % 2) + 1;
  return `${y}/${s}`;
}

export function compareTerms(a: string, b: string): number {
  return termToIndex(a) - termToIndex(b);
}

export function nextTerm(term: string, steps = 1): string {
  return indexToTerm(termToIndex(term) + steps);
}

/** Term parity as offer suffix: "2026/2" -> "/2". */
export function termParity(term: string): "/1" | "/2" {
  return term.endsWith("/1") ? "/1" : "/2";
}

/** Fallback used only when the backend has no plan yet (seed normally creates one). */
export function currentTermFromDate(d = new Date()): string {
  const half = d.getMonth() + 1 >= 7 ? 2 : 1;
  return `${d.getFullYear()}/${half}`;
}
