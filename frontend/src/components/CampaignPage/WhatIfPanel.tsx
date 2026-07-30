import { useEffect, useState } from "react";
import type {
  Campaign,
  CurriculumCourse,
  Diagnostic,
  WhatIfOutcome,
  WhatIfResponse,
} from "../../api/types";
import { compareTerms } from "../../lib/terms";
import { useWhatIf } from "../../state/queries";
import { friendlyCampaignError } from "./format";

export interface WhatIfPanelProps {
  planId: string;
  campaign: Campaign;
  courseByCode: Map<string, CurriculumCourse>;
}

/** Diagnostic ids are run-scoped (each validate generates fresh ones) — the
 * appeared/gone diff compares semantic identity instead: type + term + courses. */
function diagKey(d: Diagnostic): string {
  return `${d.type}|${d.term ?? ""}|${[...d.course_codes].sort().join(",")}`;
}

export function WhatIfPanel(props: WhatIfPanelProps) {
  const { planId, campaign, courseByCode } = props;
  const whatIf = useWhatIf(planId, campaign.id);

  // Scenario: request id -> assumed outcome; absent = left out of the simulation.
  const [picks, setPicks] = useState<Record<number, WhatIfOutcome>>({});

  useEffect(() => {
    setPicks({});
    whatIf.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign.id]);

  // Spec 8.4: toggles cover the PENDING requests of the whole campaign.
  const orderOf = new Map(campaign.rounds.map((r) => [r.key, r.order]));
  const pending = campaign.requests
    .filter((r) => r.status === "desejada" || r.status === "pedida")
    .sort((a, b) => (orderOf.get(a.round) ?? 9) - (orderOf.get(b.round) ?? 9) || a.id - b.id);
  const roundLabel = (key: string) => campaign.rounds.find((r) => r.key === key)?.label ?? key;

  function setPick(id: number, outcome: WhatIfOutcome | null) {
    setPicks((p) => {
      const next = { ...p };
      if (outcome === null) delete next[id];
      else next[id] = outcome;
      return next;
    });
    // The scenario changed — a projection computed for the old one no longer applies.
    whatIf.reset();
  }

  const assumed = pending.filter((r) => picks[r.id] !== undefined);

  function simulate() {
    whatIf.mutate(assumed.map((r) => ({ request_id: r.id, outcome: picks[r.id]! })));
  }

  return (
    <section className="whatif-panel sched-card" aria-label="Simulador e-se">
      <div className="sched-card-head">
        <h3>E se aceitarem? · simulador</h3>
        <span
          className="whatif-unsaved"
          title="ADR-024: a simulação roda em memória no servidor e nunca é persistida"
        >
          nada é salvo
        </span>
      </div>
      <p className="muted">
        Assuma o resultado dos pedidos pendentes e veja o efeito na linha do tempo do plano. A
        projeção é hipotética — <strong>não muda o plano nem os pedidos</strong>; para valer, marque
        o resultado real no quadro de pedidos.
      </p>

      {pending.length === 0 && (
        <p className="muted">
          Nenhum pedido pendente (desejada/pedida) nesta campanha — crie pedidos nas rodadas para
          simular resultados.
        </p>
      )}

      {pending.map((req) => {
        const pick = picks[req.id];
        const course = courseByCode.get(req.course_code);
        return (
          <div key={req.id} className="whatif-req">
            <span className="whatif-req-name">{course?.short ?? req.course_code}</span>
            {req.kind === "drop" && (
              <span className="chip chip-danger" title="Aceite simulado limpa a turma do item no termo">
                remoção
              </span>
            )}
            {req.section_label && <span className="chip">{req.section_label}</span>}
            <span className="chip">{roundLabel(req.round)}</span>
            <div
              className="whatif-outcomes"
              role="group"
              aria-label={`Resultado assumido para ${req.course_code}`}
            >
              <button
                type="button"
                className={pick === undefined ? "on-skip" : ""}
                aria-pressed={pick === undefined}
                title="Deixa este pedido fora da simulação"
                onClick={() => setPick(req.id, null)}
              >
                fora
              </button>
              <button
                type="button"
                className={pick === "aceita" ? "on-aceita" : ""}
                aria-pressed={pick === "aceita"}
                onClick={() => setPick(req.id, "aceita")}
              >
                aceita
              </button>
              <button
                type="button"
                className={pick === "negada" ? "on-negada" : ""}
                aria-pressed={pick === "negada"}
                onClick={() => setPick(req.id, "negada")}
              >
                negada
              </button>
            </div>
          </div>
        );
      })}

      {pending.length > 0 && (
        <div className="whatif-actions">
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={assumed.length === 0 || whatIf.isPending}
            onClick={simulate}
          >
            {whatIf.isPending ? "Simulando…" : `Simular ${assumed.length} resultado(s)`}
          </button>
          <span className="muted">
            negada = nenhum efeito; aceita de adição entra no termo da campanha, de remoção limpa a
            turma do item.
          </span>
        </div>
      )}

      {whatIf.isError && (
        <p className="camp-error" role="alert">
          {friendlyCampaignError(whatIf.error)}
        </p>
      )}

      {whatIf.data && (
        <WhatIfResult res={whatIf.data} campaign={campaign} courseByCode={courseByCode} />
      )}
    </section>
  );
}

interface WhatIfResultProps {
  res: WhatIfResponse;
  campaign: Campaign;
  courseByCode: Map<string, CurriculumCourse>;
}

/** Same timeline material of the v2 term columns (term · credits · courses),
 * rendered side-by-side "hoje × e-se". The engine keeps earliest_completion_term
 * identical on both sides — the real diff lives in the columns and diagnostics. */
function WhatIfResult({ res, campaign, courseByCode }: WhatIfResultProps) {
  const b = res.baseline;
  const p = res.projection;
  const bByTerm = new Map(b.term_summaries.map((s) => [s.term, s]));
  const pByTerm = new Map(p.term_summaries.map((s) => [s.term, s]));
  const terms = [...new Set([...bByTerm.keys(), ...pByTerm.keys()])].sort(compareTerms);

  const bKeys = new Set(b.diagnostics.map(diagKey));
  const pKeys = new Set(p.diagnostics.map(diagKey));
  const appeared = p.diagnostics.filter((d) => !bKeys.has(diagKey(d)));
  const gone = b.diagnostics.filter((d) => !pKeys.has(diagKey(d)));

  const short = (code: string) => courseByCode.get(code)?.short ?? code;
  const sameCompletion = b.earliest_completion_term === p.earliest_completion_term;

  return (
    <div className="whatif-result" role="status">
      <div className="whatif-summary">
        <span className="tb-cp-badge">hoje: formatura até {b.earliest_completion_term ?? "—"}</span>
        <span className="tb-cp-badge">e-se: até {p.earliest_completion_term ?? "—"}</span>
        {sameCompletion && (
          <span className="muted">
            a projeção de formatura não se move — a diferença aparece nas colunas e nos avisos abaixo
          </span>
        )}
      </div>

      <div className="whatif-grid" aria-label="Comparação por semestre: hoje × simulado">
        {terms.map((term) => {
          const bs = bByTerm.get(term);
          const ps = pByTerm.get(term);
          const bCodes = new Set(bs?.course_codes ?? []);
          const pCodes = new Set(ps?.course_codes ?? []);
          const added = [...pCodes].filter((c) => !bCodes.has(c)).sort();
          const removed = [...bCodes].filter((c) => !pCodes.has(c)).sort();
          const changed =
            added.length > 0 || removed.length > 0 || (bs?.credits ?? 0) !== (ps?.credits ?? 0);
          return (
            <div key={term} className={`whatif-col ${changed ? "is-changed" : ""}`}>
              <span className="whatif-col-term">
                {term}
                {term === campaign.term && <span className="chip chip-primary">campanha</span>}
              </span>
              <span className="whatif-row">
                <span className="whatif-row-tag">hoje</span>
                {bs ? `${bs.credits}cr · ${bs.course_codes.length} cadeira(s)` : "—"}
              </span>
              <span className="whatif-row is-proj">
                <span className="whatif-row-tag">e-se</span>
                {ps ? `${ps.credits}cr · ${ps.course_codes.length} cadeira(s)` : "—"}
              </span>
              {(added.length > 0 || removed.length > 0) && (
                <span className="whatif-diffchips">
                  {added.map((c) => (
                    <span key={`+${c}`} className="chip chip-gain" title={`entra no termo: ${c}`}>
                      + {short(c)}
                    </span>
                  ))}
                  {removed.map((c) => (
                    <span key={`-${c}`} className="chip chip-loss" title={`sai do termo: ${c}`}>
                      − {short(c)}
                    </span>
                  ))}
                </span>
              )}
            </div>
          );
        })}
      </div>

      <div className="whatif-diags">
        <h5>Avisos que aparecem ({appeared.length})</h5>
        {appeared.length === 0 ? (
          <p className="sp-ok">✓ nenhum aviso novo no cenário simulado</p>
        ) : (
          appeared.map((d) => (
            <p key={d.id} className={`sp-diag sev-${d.severity}`}>
              {d.message}
            </p>
          ))
        )}
        <h5>Avisos que somem ({gone.length})</h5>
        {gone.length === 0 ? (
          <p className="muted">nenhum</p>
        ) : (
          gone.map((d) => (
            <p key={d.id} className="sp-diag whatif-gone">
              {d.message}
            </p>
          ))
        )}
      </div>

      <p className="muted">
        Simulação não persistida (persisted: {String(res.persisted)}) — nada foi salvo no plano nem
        nos pedidos.
      </p>
    </div>
  );
}
