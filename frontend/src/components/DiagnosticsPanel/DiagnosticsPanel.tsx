import { useMemo } from "react";
import type { CurriculumCourse, Diagnostic } from "../../api/types";

const SEVERITY_ORDER: Record<string, number> = { error: 0, warning: 1, info: 2 };
const SEVERITY_LABEL: Record<string, string> = { error: "erro", warning: "aviso", info: "info" };

const TYPE_LABEL: Record<string, string> = {
  PREREQ_VIOLATION: "Pré-requisito",
  OFFER_MISMATCH: "Oferta",
  TERM_OVERLOADED: "Sobrecarga",
  TARGET_UNREACHABLE: "Alvo inviável",
  LOCK_CONFLICT: "Trava",
  UNALLOCATED_REQUIRED: "Não alocadas",
  REQUIREMENT_SHORTFALL: "Horas",
  PREREQ_CYCLE: "Ciclo",
  TRANSITION_NOTE: "Transição",
};

export interface DiagnosticsPanelProps {
  diagnostics: Diagnostic[];
  valid: boolean | null;
  courseByCode: Map<string, CurriculumCourse>;
  activeDiagnosticId: string | null;
  onFocusDiagnostic: (d: Diagnostic) => void;
}

export function DiagnosticsPanel({
  diagnostics,
  valid,
  courseByCode,
  activeDiagnosticId,
  onFocusDiagnostic,
}: DiagnosticsPanelProps) {
  const sorted = useMemo(
    () =>
      [...diagnostics].sort(
        (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) || a.id.localeCompare(b.id),
      ),
    [diagnostics],
  );

  if (sorted.length === 0) {
    return (
      <div className="panel-empty">
        <span className="empty-icon ok" aria-hidden>✓</span>
        <p>Nenhum aviso{valid === true ? " — plano válido" : ""}.</p>
      </div>
    );
  }

  return (
    <div className="diagnostics-panel">
      <p className="panel-hint">Clique num aviso para localizar as cadeiras no grafo.</p>
      <ul className="diag-list">
        {sorted.map((d) => (
          <li key={d.id}>
            <button
              type="button"
              className={`diag-item sev-${d.severity} ${activeDiagnosticId === d.id ? "is-active" : ""}`}
              onClick={() => onFocusDiagnostic(d)}
            >
              <span className="diag-head">
                <span className={`diag-sev sev-${d.severity}`}>{SEVERITY_LABEL[d.severity] ?? d.severity}</span>
                <span className="diag-type">{TYPE_LABEL[d.type] ?? d.type}</span>
                {d.term && <span className="diag-term">{d.term}</span>}
              </span>
              <span className="diag-msg">{d.message}</span>
              {(d.course_codes.length > 0 || d.related_codes.length > 0) && (
                <span className="diag-courses">
                  {d.course_codes.map((c) => (
                    <span key={c} className="chip chip-primary">
                      {courseByCode.get(c)?.short ?? c}
                    </span>
                  ))}
                  {d.related_codes.map((c) => (
                    <span key={c} className="chip">
                      {courseByCode.get(c)?.short ?? c}
                    </span>
                  ))}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
