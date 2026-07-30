import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { CurriculumCourse } from "../../api/types";

// type alias (not interface) so it satisfies xyflow's Record<string, unknown> constraint
export type CourseNodeData = {
  course: CurriculumCourse;
  alloc: { term: string; locked: boolean } | null;
  blocked: boolean;
  onCritical: boolean;
  highlight: "selected" | "dependent" | "ancestor" | "diagnostic" | "related" | null;
  dimmed: boolean;
  isPreview: boolean;
  canDrag: boolean;
  onToggleDone: (code: string, done: boolean) => void;
  onToggleLock: (code: string, lock: boolean) => void;
};

export type CourseFlowNode = Node<CourseNodeData, "course">;

const KIND_LABEL: Record<string, string> = {
  obrigatoria: "obrigatória",
  optativa: "optativa",
  atividade: "atividade",
  fora_curriculo: "fora do currículo",
};

function CourseNodeInner({ data, selected }: NodeProps<CourseFlowNode>) {
  const { course, alloc, blocked, onCritical, highlight, dimmed, isPreview, canDrag } = data;
  const status = course.status;
  const done = status === "aprovada";

  const classes = [
    "course-node",
    `status-${status}`,
    blocked ? "is-blocked" : "",
    alloc?.locked ? "is-locked" : "",
    onCritical ? "is-critical" : "",
    highlight ? `hl-${highlight}` : "",
    selected ? "is-selected" : "",
    dimmed ? "is-dimmed" : "",
    isPreview ? "is-preview" : "",
    canDrag ? "can-drag" : "no-drag",
  ]
    .filter(Boolean)
    .join(" ");

  const statusIcon = done ? "✓" : status === "cursando" ? "▶" : status === "pendente" ? "…" : "";

  return (
    <div className={classes} title={`${course.name} (${course.code}) — ${KIND_LABEL[course.kind] ?? course.kind}`}>
      <Handle type="target" position={Position.Left} className="course-handle" />
      <div className="cn-top">
        <span className="cn-short">{course.short ?? course.code}</span>
        <span className="cn-credits" title={`${course.credits} créditos · ${course.hours}h`}>
          {course.credits}cr
        </span>
      </div>
      <div className="cn-name">{course.name}</div>
      <div className="cn-bottom">
        <span className={`cn-offer ${course.offer_source === "seed" ? "offer-seed" : "offer-user"}`}
          title={course.offer_source === "seed" ? "Oferta do seed (não confirmada)" : "Oferta editada por você"}>
          {course.offer === "both" ? "/1 /2" : course.offer}
        </span>
        <span className="cn-diff" title={`Dificuldade ${course.difficulty}/5`} aria-hidden>
          {"●".repeat(course.difficulty)}
          {"○".repeat(Math.max(0, 5 - course.difficulty))}
        </span>
        <span className="cn-actions">
          {alloc && (
            <button
              type="button"
              className={`cn-btn cn-lock ${alloc.locked ? "on" : ""}`}
              aria-label={alloc.locked ? `Destravar ${course.name} de ${alloc.term}` : `Travar ${course.name} em ${alloc.term}`}
              title={alloc.locked ? `Travada em ${alloc.term} — clique para destravar` : `Travar em ${alloc.term}`}
              onClick={(e) => {
                e.stopPropagation();
                data.onToggleLock(course.code, !alloc.locked);
              }}
            >
              {alloc.locked ? "🔒" : "🔓"}
            </button>
          )}
          <button
            type="button"
            className={`cn-btn cn-done ${done ? "on" : ""}`}
            aria-label={done ? `Desmarcar ${course.name} como feita` : `Marcar ${course.name} como feita`}
            title={done ? "Desmarcar como feita" : "Marcar como feita"}
            onClick={(e) => {
              e.stopPropagation();
              data.onToggleDone(course.code, !done);
            }}
          >
            ✓
          </button>
        </span>
      </div>
      {statusIcon && (
        <span className={`cn-status-icon si-${status}`} aria-hidden>
          {statusIcon}
        </span>
      )}
      {blocked && <span className="cn-blocked-chip">bloqueada</span>}
      <Handle type="source" position={Position.Right} className="course-handle" />
    </div>
  );
}

export const CourseNode = memo(CourseNodeInner);
