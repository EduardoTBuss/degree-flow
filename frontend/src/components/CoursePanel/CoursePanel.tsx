import { useEffect, useState } from "react";
import type {
  CourseStatePatch,
  CourseStatus,
  CurriculumCourse,
  Diagnostic,
  Offer,
  PlanItem,
} from "../../api/types";
import { TERM_RE } from "../../lib/terms";

const STATUS_OPTIONS: { value: CourseStatus; label: string }[] = [
  { value: "aprovada", label: "Aprovada" },
  { value: "cursando", label: "Cursando" },
  { value: "falta", label: "Falta" },
  { value: "pendente", label: "Pendente" },
];

export interface CoursePanelProps {
  course: CurriculumCourse | null;
  alloc: PlanItem | null;
  blocked: boolean;
  diagnostics: Diagnostic[];
  courseByCode: Map<string, CurriculumCourse>;
  dependents: string[];
  saving: boolean;
  onPatch: (code: string, patch: CourseStatePatch) => void;
  onSelectCourse: (code: string) => void;
  onDeallocate: (code: string) => void;
  onToggleLock: (code: string, lock: boolean) => void;
}

export function CoursePanel(props: CoursePanelProps) {
  const { course, alloc, blocked, diagnostics, courseByCode, dependents, saving } = props;
  const [note, setNote] = useState(course?.note ?? "");
  const [completed, setCompleted] = useState(course?.completed_term ?? "");

  useEffect(() => {
    setNote(course?.note ?? "");
    setCompleted(course?.completed_term ?? "");
  }, [course?.code, course?.note, course?.completed_term]);

  if (!course) {
    return (
      <div className="panel-empty">
        <span className="empty-icon" aria-hidden>◇</span>
        <p>Selecione uma cadeira no grafo para editar status, oferta e dificuldade.</p>
      </div>
    );
  }

  const offerValue: string = course.offer_source === "user" ? course.offer : "default";
  const courseDiags = diagnostics.filter(
    (d) => d.course_codes.includes(course.code) || d.related_codes.includes(course.code),
  );
  const completedValid = completed === "" || TERM_RE.test(completed);

  return (
    <div className="course-panel">
      <header className="cp-header">
        <h3>{course.name}</h3>
        <div className="cp-meta">
          <span className="chip">{course.code}</span>
          <span className="chip">{course.credits}cr · {course.hours}h</span>
          <span className="chip">{course.kind}</span>
          {blocked && <span className="chip chip-danger">bloqueada</span>}
          {alloc && (
            <span className="chip chip-primary">{alloc.term} {alloc.locked ? "🔒" : ""}</span>
          )}
        </div>
      </header>

      <fieldset className="cp-field" disabled={saving}>
        <legend>Status</legend>
        <div className="cp-status-group" role="radiogroup" aria-label="Status da cadeira">
          {STATUS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={course.status === o.value}
              className={`status-pill sp-${o.value} ${course.status === o.value ? "on" : ""}`}
              onClick={() => props.onPatch(course.code, { status: o.value })}
            >
              {o.label}
            </button>
          ))}
        </div>
      </fieldset>

      {course.status === "aprovada" && (
        <label className="cp-field">
          <span className="cp-label">Concluída em (semestre real)</span>
          <input
            className={`tb-term ${completedValid ? "" : "invalid"}`}
            value={completed}
            placeholder="AAAA/S (opcional)"
            onChange={(e) => setCompleted(e.target.value)}
            onBlur={() => {
              if (!completedValid) return;
              const v = completed || null;
              if (v !== (course.completed_term ?? null)) props.onPatch(course.code, { completed_term: v });
            }}
          />
          <em className="cp-note">deixe vazio se não lembra — vai para a coluna "Concluídas".</em>
        </label>
      )}

      <div className="cp-row">
        <label className="cp-field">
          <span className="cp-label">
            Oferta{" "}
            {course.offer_source === "seed" && (
              <em className="cp-note" title="Valor do seed, ainda não confirmado">(seed, não confirmada)</em>
            )}
          </span>
          <select
            value={offerValue}
            disabled={saving}
            onChange={(e) => {
              const v = e.target.value;
              props.onPatch(course.code, { offer: v === "default" ? null : (v as Offer) });
            }}
          >
            <option value="default">padrão do seed</option>
            <option value="both">/1 e /2</option>
            <option value="/1">só /1</option>
            <option value="/2">só /2</option>
          </select>
        </label>

        <div className="cp-field">
          <span className="cp-label" id={`diff-label-${course.code}`}>Dificuldade</span>
          <div className="cp-difficulty" role="radiogroup" aria-labelledby={`diff-label-${course.code}`}>
            {[1, 2, 3, 4, 5].map((d) => (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={course.difficulty === d}
                aria-label={`Dificuldade ${d}`}
                className={`diff-dot ${course.difficulty >= d ? "filled" : ""} ${course.difficulty === d ? "on" : ""}`}
                disabled={saving}
                onClick={() => props.onPatch(course.code, { difficulty: d })}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      </div>

      {alloc && (
        <div className="cp-alloc">
          <span>Alocada em <strong>{alloc.term}</strong></span>
          <div className="cp-alloc-actions">
            <button type="button" className="btn btn-sm" onClick={() => props.onToggleLock(course.code, !alloc.locked)}>
              {alloc.locked ? "🔓 Destravar" : "🔒 Travar aqui"}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={alloc.locked}
              title={alloc.locked ? "Destrave antes de desalocar" : "Volta para a bandeja"}
              onClick={() => props.onDeallocate(course.code)}
            >
              ⏏ Desalocar
            </button>
          </div>
        </div>
      )}

      <label className="cp-field">
        <span className="cp-label">Nota</span>
        <textarea
          rows={2}
          value={note}
          placeholder="Anotações livres…"
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => {
            if (note !== (course.note ?? "")) props.onPatch(course.code, { note: note || null });
          }}
        />
      </label>

      {course.prereqs.length > 0 && (
        <div className="cp-field">
          <span className="cp-label">Pré-requisitos</span>
          <div className="cp-chips">
            {course.prereqs.map((p) => (
              <button key={p} type="button" className="chip chip-link" onClick={() => props.onSelectCourse(p)}>
                {courseByCode.get(p)?.short ?? p}
              </button>
            ))}
          </div>
        </div>
      )}

      {dependents.length > 0 && (
        <div className="cp-field">
          <span className="cp-label">Destravaria (dependem dela)</span>
          <div className="cp-chips">
            {dependents.map((p) => (
              <button key={p} type="button" className="chip chip-link" onClick={() => props.onSelectCourse(p)}>
                {courseByCode.get(p)?.short ?? p}
              </button>
            ))}
          </div>
        </div>
      )}

      {courseDiags.length > 0 && (
        <div className="cp-field">
          <span className="cp-label">Avisos desta cadeira</span>
          <ul className="cp-diags">
            {courseDiags.map((d) => (
              <li key={d.id} className={`cp-diag sev-${d.severity}`}>{d.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
