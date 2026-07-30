import { useEffect, useState } from "react";
import type {
  Campaign,
  CampaignQueueEntry,
  CampaignRound,
  CurriculumCourse,
  Section,
} from "../../api/types";
import { useCampaignQueue, useCampaignRequestMutations } from "../../state/queries";
import { friendlyCampaignError, sectionLabelFromId } from "./format";

export interface CriticalityQueueProps {
  planId: string;
  campaign: Campaign;
  round: CampaignRound;
  /** Offer of the campaign term, for human section labels. */
  sectionById: Map<string, Section>;
  courseByCode: Map<string, CurriculumCourse>;
  onOpenCourse: (code: string) => void;
}

/** Same tooltip pattern as the V4.2 "bloqueada" badge in OfferList (D6). */
function blockedTitle(entry: CampaignQueueEntry): string {
  if (entry.missing_prereqs.length === 0) return "bloqueada por pré-requisito";
  return `faltam: ${entry.missing_prereqs.map((p) => `${p.name} (${p.status})`).join(", ")}`;
}

export function CriticalityQueue(props: CriticalityQueueProps) {
  const { planId, campaign, round } = props;
  const queueQ = useCampaignQueue(planId, campaign.id, round.key);
  const mut = useCampaignRequestMutations(planId, campaign.id);

  const [picked, setPicked] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPicked({});
    setNotice(null);
    setError(null);
  }, [campaign.id, round.key]);

  function ask(entry: CampaignQueueEntry) {
    setError(null);
    setNotice(null);
    const sectionId =
      picked[entry.course_code] ?? (entry.sections.length === 1 ? entry.sections[0] : "");
    mut.create.mutate(
      {
        round: round.key,
        kind: "add",
        course_code: entry.course_code,
        section_id: sectionId === "" ? undefined : sectionId,
      },
      {
        onSuccess: (res) => {
          // Scope warnings are informational, never blocking (spec 5.1).
          if (res.warnings.length > 0) setNotice(res.warnings.join(" · "));
        },
        onError: (err) => setError(friendlyCampaignError(err)),
      },
    );
  }

  return (
    <section className="camp-queue sched-card" aria-label="Fila de criticidade">
      <div className="sched-card-head">
        <h3>Fila de criticidade · {round.label}</h3>
      </div>
      <p className="muted">
        Ordem sugerida pelo servidor: caminho crítico e o que mais destrava primeiro; bloqueadas por
        pré-requisito vão para o fim, mas continuam pedíveis (quebra autorizada).
      </p>

      {notice && (
        <p className="sp-diag sev-warning" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="camp-error" role="alert">
          {error}
          <button
            type="button"
            className="camp-dismiss"
            aria-label="Fechar aviso"
            onClick={() => setError(null)}
          >
            ×
          </button>
        </p>
      )}

      {queueQ.isLoading && (
        <div className="panel-empty">
          <span className="spinner" aria-label="Carregando fila" />
        </div>
      )}

      {queueQ.isError && (
        <div role="alert">
          <p className="sp-diag sev-error">Falha ao carregar a fila de criticidade.</p>
          <button type="button" className="btn btn-sm" onClick={() => void queueQ.refetch()}>
            Tentar de novo
          </button>
        </div>
      )}

      {queueQ.data && queueQ.data.queue.length === 0 && (
        <p className="muted">
          Nenhuma cadeira faltante com oferta em {campaign.term} — importe ou atualize a oferta na aba
          Horários.
        </p>
      )}

      {queueQ.data &&
        queueQ.data.queue.map((entry) => {
          const course = props.courseByCode.get(entry.course_code);
          const pickedId =
            picked[entry.course_code] ?? (entry.sections.length === 1 ? entry.sections[0] : "");
          const disabled = mut.create.isPending || entry.requested_in_round;
          return (
            <div key={entry.course_code} className="camp-queue-row">
              <div className="camp-queue-main">
                <span className="camp-rank" aria-hidden>
                  #{entry.rank}
                </span>
                <button
                  type="button"
                  className="camp-queue-name"
                  title="Ver no fluxograma"
                  onClick={() => props.onOpenCourse(entry.course_code)}
                >
                  {course?.short ?? entry.course_code}
                  <span className="muted"> {entry.name}</span>
                </button>
                {entry.critical && (
                  <span className="chip chip-primary" title="Pertence ao caminho crítico da formatura">
                    caminho crítico
                  </span>
                )}
                {entry.blocked && (
                  <span
                    className="chip chip-blocked"
                    title={blockedTitle(entry)}
                    aria-label={`Bloqueada por pré-requisito. ${blockedTitle(entry)}`}
                  >
                    bloqueada
                  </span>
                )}
                {entry.already_accepted && (
                  <span className="chip camp-chip-ok" title="Já tem adição aceita nesta campanha">
                    aceita
                  </span>
                )}
              </div>
              <div className="camp-queue-meta">
                <span className="muted">
                  {entry.credits}cr · destrava {entry.unlocks} · dif {entry.difficulty}
                </span>
                {entry.sections.length > 1 && (
                  <select
                    aria-label={`Turma de ${entry.name}`}
                    value={pickedId}
                    onChange={(e) =>
                      setPicked((p) => ({ ...p, [entry.course_code]: e.target.value }))
                    }
                  >
                    <option value="">turma…</option>
                    {entry.sections.map((id) => (
                      <option key={id} value={id}>
                        {props.sectionById.get(id)?.label ?? sectionLabelFromId(id)}
                      </option>
                    ))}
                  </select>
                )}
                {entry.sections.length === 1 && (
                  <span className="chip">
                    {props.sectionById.get(entry.sections[0])?.label ??
                      sectionLabelFromId(entry.sections[0])}
                  </span>
                )}
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={disabled}
                  title={
                    entry.requested_in_round
                      ? "Já pedida nesta rodada (re-pedir = outra rodada)"
                      : `Cria o pedido de ${entry.course_code} na rodada ${round.label}`
                  }
                  onClick={() => ask(entry)}
                >
                  {entry.requested_in_round ? "pedida ✓" : "pedir"}
                </button>
              </div>
            </div>
          );
        })}
    </section>
  );
}
