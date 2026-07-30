import type { CurriculumCourse, PlanDetail, PlanItem } from "../../api/types";
import { compareTerms, indexToTerm, termToIndex } from "../../lib/terms";

export const COL_WIDTH = 232;
export const COL_GAP = 18;
export const COL_STRIDE = COL_WIDTH + COL_GAP;
export const NODE_W = 200;
export const NODE_H = 96;
export const ROW_GAP = 12;
export const HEADER_H = 60;
export const NODE_X_PAD = (COL_WIDTH - NODE_W) / 2;

export type ColumnKind = "tray" | "done" | "term" | "past";

export interface BoardColumn {
  id: string;
  kind: ColumnKind;
  label: string;
  term: string | null;
  x: number;
  codes: string[];
}

export interface BoardLayout {
  columns: BoardColumn[];
  colOfCode: Map<string, string>;
  height: number;
}

const KIND_RANK: Record<string, number> = {
  obrigatoria: 0,
  optativa: 1,
  atividade: 2,
  fora_curriculo: 3,
};

function sortCodes(codes: string[], byCode: Map<string, CurriculumCourse>): string[] {
  return [...codes].sort((a, b) => {
    const ca = byCode.get(a)!;
    const cb = byCode.get(b)!;
    const k = (KIND_RANK[ca.kind] ?? 9) - (KIND_RANK[cb.kind] ?? 9);
    if (k !== 0) return k;
    const sa = ca.suggested_term_index ?? 99;
    const sb = cb.suggested_term_index ?? 99;
    if (sa !== sb) return sa - sb;
    return a.localeCompare(b);
  });
}

/**
 * Columns: tray, "Concluídas" (undated), then a contiguous timeline from
 * start_term (F1) through the horizon. Past terms (< current) are non-droppable
 * and hold aprovadas by their completed_term; gaps render as empty columns.
 */
export function buildBoard(
  courses: CurriculumCourse[],
  plan: PlanDetail,
  previewItems: PlanItem[] | null,
): BoardLayout {
  const byCode = new Map(courses.map((c) => [c.code, c]));
  const items = previewItems ?? plan.items;
  const itemByCode = new Map(items.map((i) => [i.course_code, i]));

  const currentIdx = termToIndex(plan.current_term);
  const startIdx = plan.start_term ? Math.min(termToIndex(plan.start_term), currentIdx) : currentIdx;
  let horizonIdx = currentIdx + 5;
  if (plan.target_term) horizonIdx = Math.max(horizonIdx, termToIndex(plan.target_term));
  for (const it of items) horizonIdx = Math.max(horizonIdx, termToIndex(it.term));
  horizonIdx += 1;

  const columns: BoardColumn[] = [
    { id: "tray", kind: "tray", label: "Não alocadas", term: null, x: 0, codes: [] },
    { id: "done", kind: "done", label: "Concluídas", term: null, x: 0, codes: [] },
  ];
  const termCols = new Map<string, BoardColumn>();
  for (let idx = startIdx; idx <= horizonIdx; idx++) {
    const term = indexToTerm(idx);
    const col: BoardColumn = {
      id: term,
      kind: idx < currentIdx ? "past" : "term",
      label: term,
      term,
      x: 0,
      codes: [],
    };
    columns.push(col);
    termCols.set(term, col);
  }
  // assign x positions in array order
  columns.forEach((col, i) => { col.x = i * COL_STRIDE; });

  for (const c of courses) {
    if (c.status === "aprovada") {
      const ct = c.completed_term;
      if (ct && termCols.has(ct) && termToIndex(ct) < currentIdx) {
        termCols.get(ct)!.codes.push(c.code);
      } else {
        columns[1].codes.push(c.code); // undated -> "Concluídas"
      }
      continue;
    }
    if (c.status === "cursando") {
      termCols.get(plan.current_term)?.codes.push(c.code);
      continue;
    }
    const item = itemByCode.get(c.code);
    if (item && compareTerms(item.term, plan.current_term) >= 0 && termCols.has(item.term)) {
      termCols.get(item.term)!.codes.push(c.code);
    } else {
      columns[0].codes.push(c.code);
    }
  }

  const colOfCode = new Map<string, string>();
  let maxRows = 0;
  for (const col of columns) {
    col.codes = sortCodes(col.codes, byCode);
    maxRows = Math.max(maxRows, col.codes.length);
    for (const code of col.codes) colOfCode.set(code, col.id);
  }

  const height = Math.max(560, HEADER_H + maxRows * (NODE_H + ROW_GAP) + 24);
  return { columns, colOfCode, height };
}

export function columnAtX(columns: BoardColumn[], x: number): BoardColumn | null {
  for (const col of columns) {
    if (x >= col.x - COL_GAP / 2 && x < col.x + COL_WIDTH + COL_GAP / 2) return col;
  }
  return null;
}

export function nodePosition(col: BoardColumn, row: number): { x: number; y: number } {
  return { x: col.x + NODE_X_PAD, y: HEADER_H + row * (NODE_H + ROW_GAP) };
}
