import { useMemo } from "react";
import type { CurriculumCourse, Section, Timeslot } from "../../api/types";
import { WD, toMinutes } from "./format";

export interface WeekGridBlock {
  section: Section;
  course: CurriculumCourse | undefined;
  /** Painted from server diagnostics only (SCHEDULE_CONFLICT) — never computed here. */
  conflicted: boolean;
}

export interface WeekGridProps {
  term: string;
  blocks: WeekGridBlock[];
  onOpenCourse: (code: string) => void;
}

const PX_PER_MIN = 1; // 60px per hour — keeps the hour-line gradient aligned (see .wg-day)

// Theme tokens (ADR-022): actual colors live in styles.css :root / html[data-theme="dark"].
const COURSE_COLORS = [
  { bg: "var(--wg1-bg)", edge: "var(--wg1-edge)" },
  { bg: "var(--wg2-bg)", edge: "var(--wg2-edge)" },
  { bg: "var(--wg3-bg)", edge: "var(--wg3-edge)" },
  { bg: "var(--wg4-bg)", edge: "var(--wg4-edge)" },
  { bg: "var(--wg5-bg)", edge: "var(--wg5-edge)" },
  { bg: "var(--wg6-bg)", edge: "var(--wg6-edge)" },
  { bg: "var(--wg7-bg)", edge: "var(--wg7-edge)" },
];

interface SlotBox {
  block: WeekGridBlock;
  slot: Timeslot;
  startMin: number;
  endMin: number;
  colorIdx: number;
}

/** Greedy lane assignment so overlapping blocks sit side by side instead of hiding each other. */
function assignLanes(items: SlotBox[]): { placed: { box: SlotBox; lane: number }[]; laneCount: number } {
  const sorted = [...items].sort((a, b) => a.startMin - b.startMin || a.endMin - b.endMin);
  const laneEnds: number[] = [];
  const placed = sorted.map((box) => {
    let lane = laneEnds.findIndex((end) => end <= box.startMin);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(box.endMin);
    } else {
      laneEnds[lane] = box.endMin;
    }
    return { box, lane };
  });
  return { placed, laneCount: Math.max(1, laneEnds.length) };
}

export function WeekGrid({ term, blocks, onOpenCourse }: WeekGridProps) {
  const { boxes, colorByCourse } = useMemo(() => {
    const codes = [...new Set(blocks.map((b) => b.section.course_code))].sort();
    const colorByCourse = new Map(codes.map((c, i) => [c, COURSE_COLORS[i % COURSE_COLORS.length]]));
    const boxes: SlotBox[] = [];
    for (const block of blocks) {
      const colorIdx = codes.indexOf(block.section.course_code);
      for (const slot of block.section.timeslots) {
        const startMin = toMinutes(slot.start);
        const endMin = toMinutes(slot.end);
        if (startMin === null || endMin === null || endMin <= startMin) continue;
        boxes.push({ block, slot, startMin, endMin, colorIdx: colorIdx % COURSE_COLORS.length });
      }
    }
    return { boxes, colorByCourse };
  }, [blocks]);

  if (blocks.length === 0) {
    return (
      <div className="panel-empty">
        <span className="empty-icon" aria-hidden>▦</span>
        <p>Nenhuma turma escolhida para {term} ainda.</p>
        <p className="muted">Escolha turmas na lista de cadeiras faltantes ao lado — a grade se monta sozinha.</p>
      </div>
    );
  }

  const days = boxes.some((b) => b.slot.weekday === 6) ? [0, 1, 2, 3, 4, 5, 6] : [0, 1, 2, 3, 4, 5];
  const minStart = Math.floor(Math.min(...boxes.map((b) => b.startMin)) / 60) * 60;
  const maxEnd = Math.ceil(Math.max(...boxes.map((b) => b.endMin)) / 60) * 60;
  const totalPx = (maxEnd - minStart) * PX_PER_MIN;
  const hours: number[] = [];
  for (let h = minStart; h <= maxEnd; h += 60) hours.push(h);

  const columns = `44px repeat(${days.length}, minmax(0, 1fr))`;

  return (
    <div className="wg" role="img" aria-label={`Grade semanal de ${term}`}>
      <div className="wg-days" style={{ gridTemplateColumns: columns }}>
        <div aria-hidden />
        {days.map((d) => (
          <div key={d} className="wg-dayname">{WD[d]}</div>
        ))}
      </div>
      <div className="wg-body" style={{ gridTemplateColumns: columns, height: totalPx }}>
        <div className="wg-hours" aria-hidden>
          {hours.map((h) => (
            <span key={h} className="wg-hour" style={{ top: (h - minStart) * PX_PER_MIN }}>
              {String(Math.floor(h / 60)).padStart(2, "0")}h
            </span>
          ))}
        </div>
        {days.map((d) => {
          const { placed, laneCount } = assignLanes(boxes.filter((b) => b.slot.weekday === d));
          return (
            <div key={d} className="wg-day">
              {placed.map(({ box, lane }) => {
                const { block, slot, startMin, endMin, colorIdx } = box;
                const code = block.section.course_code;
                const color = colorByCourse.get(code) ?? COURSE_COLORS[colorIdx];
                const label = block.course?.short ?? code;
                return (
                  <button
                    key={`${block.section.id}:${slot.weekday}:${slot.start}`}
                    type="button"
                    className={`wg-block ${block.conflicted ? "is-conflict" : ""}`}
                    style={{
                      top: (startMin - minStart) * PX_PER_MIN,
                      height: Math.max(22, (endMin - startMin) * PX_PER_MIN - 2),
                      left: `${(lane * 100) / laneCount}%`,
                      width: `calc(${100 / laneCount}% - 3px)`,
                      background: block.conflicted ? undefined : color.bg,
                      borderLeftColor: block.conflicted ? undefined : color.edge,
                    }}
                    title={`${block.course?.name ?? code} — turma ${block.section.label ?? block.section.id} · ${slot.start}–${slot.end}${block.conflicted ? " · EM CONFLITO" : ""}`}
                    onClick={() => onOpenCourse(code)}
                  >
                    <strong>{label}</strong>
                    <span>{block.section.label ?? ""} {slot.start}–{slot.end}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
      <div className="wg-legend">
        {[...colorByCourse.entries()].map(([code, color]) => {
          const block = blocks.find((b) => b.section.course_code === code);
          return (
            <span key={code} className="wg-legend-item">
              <span className="wg-dot" style={{ background: color.edge }} aria-hidden />
              {block?.course?.short ?? code}
              {block?.section.label ? ` · ${block.section.label}` : ""}
            </span>
          );
        })}
      </div>
    </div>
  );
}
