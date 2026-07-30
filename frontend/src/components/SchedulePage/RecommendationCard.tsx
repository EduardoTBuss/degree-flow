import { useEffect } from "react";
import { ApiError } from "../../api/client";
import type { CurriculumCourse, PinnedInfo, PinnedStatus } from "../../api/types";
import { useRecommend } from "../../state/queries";

export interface RecommendationCardProps {
  planId: string;
  term: string;
  courseByCode: Map<string, CurriculumCourse>;
  /** v4 (C1, spec 5.3): accepted section ids of the term's open campaign — kept in every suggestion. */
  lockedChoices?: string[];
  /** v5 (F2b, ADR-029): course_codes pinned by the user — the engine picks the section. */
  pinnedCourses?: string[];
  applying: boolean;
  onApply: (choices: { course_code: string; section_id: string }[]) => void;
  onOpenCourse: (code: string) => void;
}

/** Server vocabulary → badge label + tone. The front only renders (principle 3). */
const PIN_BADGE: Record<PinnedStatus, { label: string; cls: string }> = {
  ok: { label: "fixada", cls: "chip-pin" },
  sem_oferta: { label: "sem oferta", cls: "chip-pin-warn" },
  bloqueada: { label: "pré-requisito pendente", cls: "chip-pin-warn" },
  turma_ja_fixada: { label: "turma já travada", cls: "chip-pin" },
  conflito: { label: "conflito", cls: "chip-pin-err" },
  estoura_limite: { label: "estoura limite", cls: "chip-pin-err" },
};

/** "2026-2:22000273:M2" -> "M2" (best-effort; falls back to the full id). */
function sectionLabel(sectionId: string): string {
  const parts = sectionId.split(":");
  return parts.length >= 3 ? parts[parts.length - 1] : sectionId;
}

export function RecommendationCard(props: RecommendationCardProps) {
  const { planId, term, courseByCode } = props;
  const locked = props.lockedChoices ?? [];
  const lockedKey = locked.join(",");
  const pins = props.pinnedCourses ?? [];
  const pinnedKey = pins.join(",");
  const rec = useRecommend(planId);
  const { reset } = rec;

  // Recommendations are term-specific and depend on the campaign's accepted set and on
  // the pinned courses: drop stale results when any of them changes.
  useEffect(() => {
    reset();
  }, [term, lockedKey, pinnedKey, reset]);

  function run(withPins: boolean) {
    rec.mutate({
      term,
      top_n: 3,
      locked_choices: locked.length > 0 ? locked : undefined,
      pinned_courses: withPins && pins.length > 0 ? pins : undefined,
    });
  }

  let errorMsg: string | null = null;
  if (rec.error) {
    if (rec.error instanceof ApiError && rec.error.status === 400) {
      errorMsg = `Sem dados de turmas para ${term}. Importe a oferta ou use "Buscar ofertas" acima.`;
    } else if (rec.error instanceof Error) {
      errorMsg = rec.error.message;
    } else {
      errorMsg = "Falha ao calcular recomendações.";
    }
  }

  const courseName = (code: string) => courseByCode.get(code)?.short ?? code;
  const pinnedResult: PinnedInfo[] = rec.data?.pinned ?? [];
  const infeasible = rec.data?.pinned_infeasible === true;

  return (
    <div className="sched-card">
      <div className="sched-card-head">
        <h3>Melhor combinação</h3>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={rec.isPending}
          onClick={() => run(true)}
        >
          {rec.isPending ? "Calculando…" : "Recomendar combinação"}
        </button>
      </div>

      {locked.length > 0 && (
        <p className="muted">
          🔒 Mantém {locked.length} turma(s) aceita(s) na campanha de matrícula em toda sugestão.
        </p>
      )}

      {pins.length > 0 && (
        <p className="muted">
          📌 {pins.length} cadeira(s) fixada(s) — toda sugestão vai contê-la(s):{" "}
          {pins.map(courseName).join(", ")}. O motor escolhe a turma.
        </p>
      )}

      {!rec.data && !errorMsg && !rec.isPending && (
        <p className="muted">O motor monta as 3 melhores combinações de turmas para {term} (créditos, caminho crítico, vagas).</p>
      )}

      {errorMsg && <p className="sp-diag sev-warning" role="alert">{errorMsg}</p>}

      {/* v5 (F2b, D5): per-pinned-course outcome, straight from the server. */}
      {pinnedResult.length > 0 && (
        <ul className="sp-pinned-list">
          {pinnedResult.map((p) => {
            const badge = PIN_BADGE[p.status] ?? { label: p.status, cls: "chip-pin-warn" };
            return (
              <li key={p.course_code} className="sp-pin-row">
                <button
                  type="button"
                  className="chip chip-link"
                  title="Ver no fluxograma"
                  onClick={() => props.onOpenCourse(p.course_code)}
                >
                  <span aria-hidden>📌 </span>
                  {courseName(p.course_code)}
                </button>
                <span className={`chip ${badge.cls}`}>{badge.label}</span>
                {p.section_id && (
                  <span className="muted">
                    turma {sectionLabel(p.section_id)}
                    {p.alternatives.length > 0 &&
                      ` (alternativas: ${p.alternatives.map(sectionLabel).join(", ")})`}
                  </span>
                )}
                {p.reason && <span className="sp-pin-reason">{p.reason}</span>}
              </li>
            );
          })}
        </ul>
      )}

      {/* D5: never silently dropped — releasing the pins is an explicit user action. */}
      {infeasible && (
        <div className="sp-diag sev-error" role="alert">
          <p className="sp-pin-infeasible-msg">
            Nenhuma combinação viável com as cadeiras fixadas — os motivos estão acima, por cadeira.
            Desafixe alguma na lista abaixo ou:
          </p>
          <button
            type="button"
            className="btn btn-sm"
            disabled={rec.isPending}
            title="Roda a recomendação ignorando as fixações (elas continuam marcadas na lista)"
            onClick={() => run(false)}
          >
            Recomendar sem as fixações
          </button>
        </div>
      )}

      {rec.data && (
        <div className="sp-recs">
          {rec.data.recommendations.length === 0 && !infeasible && (
            <p className="muted">Nenhuma combinação viável encontrada para {term}.</p>
          )}
          {rec.data.recommendations.map((r) => (
            <div key={r.rank} className="sp-rec">
              <div className="sp-rec-head">
                <span className="chip chip-primary">#{r.rank}</span>
                <span className="muted">
                  {r.score_breakdown.credits}cr · {r.score_breakdown.critical_path_courses} do caminho crítico
                  {r.score_breakdown.full_sections > 0 && ` · ${r.score_breakdown.full_sections} sem vaga`}
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={props.applying}
                  onClick={() => props.onApply(r.choices)}
                >
                  Aplicar
                </button>
              </div>
              <div className="sp-rec-courses">
                {r.choices.map((c) => {
                  const isLocked = locked.includes(c.section_id);
                  const isPinned = pinnedResult.some(
                    (p) => p.course_code === c.course_code && p.status !== "sem_oferta",
                  );
                  return (
                    <button
                      key={c.course_code}
                      type="button"
                      className="chip chip-link"
                      title={
                        isLocked
                          ? "Turma aceita na campanha (mantida em toda sugestão)"
                          : isPinned
                            ? "Cadeira fixada por você (presente em toda sugestão)"
                            : "Ver no fluxograma"
                      }
                      onClick={() => props.onOpenCourse(c.course_code)}
                    >
                      {isLocked && <span aria-hidden>🔒 </span>}
                      {!isLocked && isPinned && <span aria-hidden>📌 </span>}
                      {courseByCode.get(c.course_code)?.short ?? c.course_code}
                    </button>
                  );
                })}
              </div>
              {r.left_out.length > 0 && (
                <p className="muted sp-leftout">
                  Fora desta combinação: {r.left_out.map((l) => `${courseByCode.get(l.course_code)?.short ?? l.course_code} (${l.reason})`).join("; ")}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
