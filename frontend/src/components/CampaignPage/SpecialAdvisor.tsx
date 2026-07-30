import { useEffect, useState } from "react";
import type {
  Campaign,
  CampaignRound,
  CurriculumCourse,
  PlanDetail,
  Section,
  SpecialCandidate,
  SpecialScoreBreakdown,
} from "../../api/types";
import { nextTerm } from "../../lib/terms";
import { useCampaignRequestMutations, useSpecialCandidates } from "../../state/queries";
import { ScrapeButton } from "../SchedulePage/ScrapeButton";
import { friendlyCampaignError, sectionLabelFromId } from "./format";

export interface SpecialAdvisorProps {
  plan: PlanDetail;
  campaign: Campaign;
  /** The special round — mounted only for the round whose max_adds limits the
   * choices (ADR-018 DATA from the grade rules, never a round-name check). */
  round: CampaignRound;
  /** Offer of the campaign term, for human section labels. */
  sectionById: Map<string, Section>;
  courseByCode: Map<string, CurriculumCourse>;
  onOpenCourse: (code: string) => void;
}

/** PT labels for the fixed breakdown keys of spec 7.3 — auditable in the UI. */
const BREAKDOWN_LABELS: Record<keyof SpecialScoreBreakdown, string> = {
  critical: "caminho crítico",
  unlocks: "destrava",
  credits: "créditos",
  fit: "encaixe de horário",
  vacancy: "vagas abertas",
  full: "lotada",
  blocked: "bloqueada",
  foreign: "outro curso",
};

const BREAKDOWN_ORDER: (keyof SpecialScoreBreakdown)[] = [
  "critical",
  "unlocks",
  "credits",
  "fit",
  "vacancy",
  "full",
  "blocked",
  "foreign",
];

function num(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function signed(v: number): string {
  return v > 0 ? `+${num(v)}` : num(v);
}

/** Full formula as tooltip of the score — every part, including zeros. */
function scoreTitle(c: SpecialCandidate): string {
  const parts = BREAKDOWN_ORDER.map(
    (k) => `${BREAKDOWN_LABELS[k]} ${signed(c.score_breakdown[k])}`,
  );
  return `Pontuação do servidor (pesos da spec 7.3): ${parts.join(" · ")}`;
}

/** Same tooltip pattern as the "bloqueada" badges in OfferList/CriticalityQueue (D6). */
function blockedTitle(c: SpecialCandidate): string {
  if (c.missing_prereqs.length === 0) return "bloqueada por pré-requisito";
  return `faltam: ${c.missing_prereqs.map((p) => `${p.name} (${p.status})`).join(", ")}`;
}

export function SpecialAdvisor(props: SpecialAdvisorProps) {
  const { plan, campaign, round, sectionById, courseByCode } = props;
  const candidatesQ = useSpecialCandidates(plan.id, campaign.id);
  const mut = useCampaignRequestMutations(plan.id, campaign.id);

  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showScrape, setShowScrape] = useState(false);

  useEffect(() => {
    setNotice(null);
    setError(null);
    setShowScrape(false);
  }, [campaign.id, round.key]);

  const data = candidatesQ.data;

  // The scrape endpoint derives its term server-side as next_term(current_term)
  // (ADR-011) — the shortcut only makes sense when it targets THIS campaign's term.
  const canRescrape = campaign.term === nextTerm(plan.current_term);

  // Mirror of the server's ROUND_LIMIT_EXCEEDED count: every 'add' request of
  // the round, regardless of status. The limit itself comes from the RESPONSE
  // (grade rule via max_choices) — never a literal.
  const addsUsed = campaign.requests.filter(
    (r) => r.round === round.key && r.kind === "add",
  ).length;
  const maxChoices = data?.max_choices ?? null;
  const limitReached = maxChoices != null && addsUsed >= maxChoices;

  const requestedCourses = new Set(
    campaign.requests
      .filter((r) => r.round === round.key && r.kind === "add")
      .map((r) => r.course_code),
  );

  const short = (code: string) => courseByCode.get(code)?.short ?? code;
  const sectionText = (id: string) => {
    const s = sectionById.get(id);
    if (!s) return sectionLabelFromId(id);
    return `${short(s.course_code)} ${s.label ?? sectionLabelFromId(id)}`;
  };

  function choose(c: SpecialCandidate) {
    setError(null);
    setNotice(null);
    // Choosing ONLY creates the add request in this round — the plan changes
    // when the accept is marked (ADR-017); never touches plan_item directly.
    mut.create.mutate(
      {
        round: round.key,
        kind: "add",
        course_code: c.course_code,
        section_id: c.section_id,
      },
      {
        onSuccess: (res) => {
          // Scope warnings are informational, never blocking (spec 5.1).
          if (res.warnings.length > 0) setNotice(res.warnings.join(" · "));
        },
        // Includes the friendly ROUND_LIMIT_EXCEEDED message (server is the judge).
        onError: (err) => setError(friendlyCampaignError(err)),
      },
    );
  }

  return (
    <section className="special-advisor sched-card" aria-label="Conselheiro da matrícula especial">
      <div className="sched-card-head">
        <h3>Conselheiro · {round.label}</h3>
        {maxChoices != null && (
          <span
            className={`special-counter ${limitReached ? "is-maxed" : ""}`}
            title="Limite de adições da rodada — regra da grade (o servidor também a impõe)"
          >
            {addsUsed} de {maxChoices} escolha(s)
          </span>
        )}
      </div>
      <p className="muted">
        Top-5 do servidor: impacto na formatura + encaixe com as turmas já aceitas + vagas.
        Cadeiras de qualquer curso (inclusive o próprio); bloqueadas e lotadas entram com selo —
        escolher cria o pedido nesta rodada, o plano só muda no aceite.
      </p>

      {data?.rescrape_hint && (
        <div className="special-hint" role="status">
          <span aria-hidden>⟳</span>
          <span>{data.rescrape_hint} — vagas mudam entre rodadas.</span>
          {canRescrape ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setShowScrape((v) => !v)}
            >
              {showScrape ? "ocultar busca" : "atualizar oferta"}
            </button>
          ) : (
            <span className="muted">
              (campanha de {campaign.term} — a busca automática cobre só o próximo semestre)
            </span>
          )}
        </div>
      )}
      {showScrape && canRescrape && (
        <ScrapeButton planId={plan.id} targetTerm={campaign.term} />
      )}

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

      {candidatesQ.isLoading && (
        <div className="panel-empty">
          <span className="spinner" aria-label="Carregando candidatas" />
        </div>
      )}

      {candidatesQ.isError && (
        <div role="alert">
          <p className="sp-diag sev-error">Falha ao carregar as candidatas da matrícula especial.</p>
          <button type="button" className="btn btn-sm" onClick={() => void candidatesQ.refetch()}>
            Tentar de novo
          </button>
        </div>
      )}

      {data && data.candidates.length === 0 && (
        <>
          <div className="panel-empty">
            <p>Nenhuma candidata: não há oferta conhecida em {campaign.term}.</p>
            {!canRescrape && <p className="muted">Importe a oferta do termo na aba Horários.</p>}
          </div>
          {canRescrape && <ScrapeButton planId={plan.id} targetTerm={campaign.term} />}
        </>
      )}

      {data?.candidates.map((c) => {
        const requested = requestedCourses.has(c.course_code);
        const vac = c.capacity != null && c.enrolled != null ? c.capacity - c.enrolled : null;
        const disabled = mut.create.isPending || requested || limitReached;
        return (
          <div key={c.course_code} className="special-cand">
            <div className="special-cand-head">
              <span className="camp-rank" aria-hidden>
                #{c.rank}
              </span>
              <button
                type="button"
                className="camp-queue-name"
                title="Ver no fluxograma"
                onClick={() => props.onOpenCourse(c.course_code)}
              >
                {short(c.course_code)}
                <span className="muted"> {c.name}</span>
              </button>
              {c.section_label && <span className="chip">{c.section_label}</span>}
              <span className="special-score" title={scoreTitle(c)}>
                {num(c.score)} pts
              </span>
              {/* D6: badge only — the request stays possible (authorized break). */}
              {c.blocked && (
                <span
                  className="chip chip-blocked"
                  title={blockedTitle(c)}
                  aria-label={`Bloqueada por pré-requisito. ${blockedTitle(c)}`}
                >
                  bloqueada
                </span>
              )}
              {/* D5: full is penalized and flagged, never excluded. */}
              {c.full && (
                <span
                  className="chip chip-full"
                  title={`Sem vaga no último dado (${c.enrolled}/${c.capacity}) — negativas podem abrir espaço; atualize a oferta antes de decidir`}
                >
                  lotada
                </span>
              )}
              {c.foreign_offer && (
                <span
                  className="chip chip-foreign"
                  title={`Ofertada para: ${(c.offered_to ?? []).join(", ") || "outro curso"} — aprovação dos dois coordenadores`}
                >
                  outro curso
                </span>
              )}
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={disabled}
                title={
                  requested
                    ? "Já pedida nesta rodada"
                    : limitReached
                      ? `Limite da rodada atingido (${maxChoices} adições) — apague um pedido para trocar`
                      : `Cria o pedido de ${c.course_code} (turma ${c.section_label ?? c.section_id}) na rodada ${round.label}`
                }
                onClick={() => choose(c)}
              >
                {requested ? "pedida ✓" : "pedir esta turma"}
              </button>
            </div>

            {/* Auditable score: non-zero weighted parts as chips (full formula in the tooltip). */}
            <div className="special-breakdown" aria-label="Composição da pontuação">
              {BREAKDOWN_ORDER.filter((k) => c.score_breakdown[k] !== 0).map((k) => (
                <span
                  key={k}
                  className={`chip ${c.score_breakdown[k] > 0 ? "chip-gain" : "chip-loss"}`}
                >
                  {BREAKDOWN_LABELS[k]} {signed(c.score_breakdown[k])}
                </span>
              ))}
            </div>

            <div className="special-meta">
              {vac != null ? (
                <span className={vac > 0 ? "sp-vaga" : "sp-novaga"}>
                  {vac > 0 ? `${vac} vaga(s)` : "sem vaga"} ({c.enrolled}/{c.capacity})
                </span>
              ) : (
                <span className="muted" title="O portal não informou vagas para esta turma (nível 2)">
                  vagas: ?
                </span>
              )}
              {c.alternatives.length > 0 && (
                <span className="special-alts">
                  outras turmas:
                  {c.alternatives.map((id) => (
                    <span
                      key={id}
                      className="chip"
                      title="Outra turma da mesma cadeira — escolha-a no quadro de pedidos após pedir"
                    >
                      {sectionById.get(id)?.label ?? sectionLabelFromId(id)}
                    </span>
                  ))}
                </span>
              )}
            </div>

            {c.conflicts_with.length > 0 && (
              <p
                className="special-conflicts"
                title="Turmas já aceitas nesta campanha cujo horário bate com esta"
              >
                ⚠ conflita com aceitas: {c.conflicts_with.map(sectionText).join(", ")}
              </p>
            )}
          </div>
        );
      })}
    </section>
  );
}
