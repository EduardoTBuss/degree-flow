import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/client";
import type { CampaignRound, CurriculumCourse, PlanDetail, Section } from "../../api/types";
import { nextTerm } from "../../lib/terms";
import {
  useCampaign,
  useCampaignPatch,
  useCampaigns,
  useCreateCampaign,
  useSections,
} from "../../state/queries";
import { CriticalityQueue } from "./CriticalityQueue";
import { RoundBoard } from "./RoundBoard";
import { SpecialAdvisor } from "./SpecialAdvisor";
import { SwapPanel } from "./SwapPanel";
import { WhatIfPanel } from "./WhatIfPanel";

export interface CampaignPageProps {
  plan: PlanDetail;
  courseByCode: Map<string, CurriculumCourse>;
  /** Jumps back to the flow view with the course selected. */
  onOpenCourse: (code: string) => void;
}

/** Presentation of the round scope — known values get a label, unknown pass through (data). */
function scopeLabel(scope: string): string {
  if (scope === "proprio_curso") return "só cadeiras do próprio curso";
  if (scope === "qualquer_curso") return "cadeiras de qualquer curso";
  return scope;
}

export function CampaignPage(props: CampaignPageProps) {
  const { plan, courseByCode } = props;
  // Same derivation as the scraper/schedule view (ADR-011): campaign targets next_term.
  const targetTerm = nextTerm(plan.current_term);

  const campaignsQ = useCampaigns(plan.id);
  const summaries = campaignsQ.data?.campaigns ?? [];

  // null = follow the target-term campaign; a string pins a specific one (history browsing).
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => setSelectedId(null), [plan.id]);

  const targetSummary = summaries.find((c) => c.term === targetTerm);
  const activeId = selectedId ?? targetSummary?.id;

  const campaignQ = useCampaign(plan.id, activeId);
  const campaign = campaignQ.data;

  const createCampaign = useCreateCampaign(plan.id);
  const patchCampaign = useCampaignPatch(plan.id, activeId);
  const [createError, setCreateError] = useState<string | null>(null);

  // Rounds stepper: keys/labels/limits come from the campaign response (ADR-018) — never hard-coded.
  const rounds: CampaignRound[] = campaign?.rounds ?? [];
  const [roundKey, setRoundKey] = useState<string | null>(null);
  useEffect(() => setRoundKey(null), [activeId]);
  const activeRound = rounds.find((r) => r.key === roundKey) ?? rounds[0] ?? null;

  // One offer fetch for the whole view: section pickers (board) + section labels (queue).
  const sectionsQ = useSections(campaign?.term);
  const { sectionsByCourse, sectionById } = useMemo(() => {
    const byCourse = new Map<string, Section[]>();
    const byId = new Map<string, Section>();
    for (const s of sectionsQ.data?.sections ?? []) {
      const list = byCourse.get(s.course_code) ?? [];
      list.push(s);
      byCourse.set(s.course_code, list);
      byId.set(s.id, s);
    }
    return { sectionsByCourse: byCourse, sectionById: byId };
  }, [sectionsQ.data]);

  function handleCreate() {
    setCreateError(null);
    createCampaign.mutate(undefined, {
      onSuccess: (c) => setSelectedId(c.id),
      onError: (err) => {
        // 409 CAMPAIGN_EXISTS: it is already there — just open the existing one (spec 6.3).
        if (err instanceof ApiError && err.code === "CAMPAIGN_EXISTS") {
          const cid = err.details?.campaign_id;
          if (typeof cid === "string") {
            setSelectedId(cid);
            return;
          }
        }
        setCreateError(err instanceof Error ? err.message : "Falha ao criar a campanha.");
      },
    });
  }

  if (campaignsQ.isLoading) {
    return (
      <main className="campaign-page">
        <div className="panel-empty">
          <span className="spinner" aria-label="Carregando campanhas" />
        </div>
      </main>
    );
  }

  if (campaignsQ.isError) {
    return (
      <main className="campaign-page">
        <div className="sched-card" role="alert">
          <p className="sp-diag sev-error">Falha ao carregar as campanhas do plano.</p>
          <button type="button" className="btn btn-sm" onClick={() => void campaignsQ.refetch()}>
            Tentar de novo
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="campaign-page">
      <header className="camp-head">
        <div>
          <h2>Matrícula</h2>
          <p className="muted">
            Registre os pedidos de cada rodada e marque o resultado do Cobalto — o aceite entra no
            plano automaticamente; a alocação real continua sendo o fluxograma.
          </p>
        </div>
        <div className="camp-head-controls">
          {summaries.length > 0 && (
            <>
              <label className="tb-label" htmlFor="camp-select">Campanha</label>
              <select
                id="camp-select"
                value={activeId ?? ""}
                onChange={(e) => setSelectedId(e.target.value === "" ? null : e.target.value)}
              >
                {activeId === undefined && <option value="">—</option>}
                {summaries.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.term}
                    {c.term === targetTerm ? " (alvo)" : ""} · {c.status} · {c.requests_total} pedido(s)
                  </option>
                ))}
              </select>
            </>
          )}
          {!targetSummary && (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={createCampaign.isPending}
              onClick={handleCreate}
            >
              {createCampaign.isPending ? "Criando…" : `Criar campanha ${targetTerm}`}
            </button>
          )}
          {campaign && (
            <button
              type="button"
              className="btn btn-sm"
              disabled={patchCampaign.isPending}
              title="Só organização de UI — encerrar não tem efeito no plano"
              onClick={() =>
                patchCampaign.mutate({
                  status: campaign.status === "aberta" ? "encerrada" : "aberta",
                })
              }
            >
              {campaign.status === "aberta" ? "Encerrar" : "Reabrir"}
            </button>
          )}
        </div>
      </header>

      {createError && (
        <p className="camp-error" role="alert">
          {createError}
          <button
            type="button"
            className="camp-dismiss"
            aria-label="Fechar aviso"
            onClick={() => setCreateError(null)}
          >
            ×
          </button>
        </p>
      )}

      {!activeId && (
        <div className="panel-empty">
          <span className="empty-icon" aria-hidden>▤</span>
          <p>Nenhuma campanha de matrícula para {targetTerm}.</p>
          <p className="muted">
            A campanha organiza os pedidos por rodada (os nomes e limites vêm das regras da grade) e
            guarda o histórico por semestre.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            disabled={createCampaign.isPending}
            onClick={handleCreate}
          >
            {createCampaign.isPending ? "Criando…" : `Criar campanha ${targetTerm}`}
          </button>
        </div>
      )}

      {activeId && campaignQ.isLoading && (
        <div className="panel-empty">
          <span className="spinner" aria-label="Carregando campanha" />
        </div>
      )}

      {activeId && campaignQ.isError && (
        <div className="sched-card" role="alert">
          <p className="sp-diag sev-error">Falha ao carregar a campanha.</p>
          <button type="button" className="btn btn-sm" onClick={() => void campaignQ.refetch()}>
            Tentar de novo
          </button>
        </div>
      )}

      {campaign && activeRound && (
        <>
          <nav className="camp-stepper" role="tablist" aria-label="Rodadas da campanha">
            {rounds.map((r) => {
              const n = campaign.requests.filter((q) => q.round === r.key).length;
              const on = activeRound.key === r.key;
              return (
                <button
                  key={r.key}
                  type="button"
                  role="tab"
                  aria-selected={on}
                  className={`camp-step ${on ? "on" : ""}`}
                  onClick={() => setRoundKey(r.key)}
                >
                  <span className="camp-step-order" aria-hidden>{r.order}</span>
                  {r.label}
                  {n > 0 && <span className="camp-step-count">{n}</span>}
                </button>
              );
            })}
          </nav>

          <p className="muted camp-round-meta">
            {campaign.term} · {scopeLabel(activeRound.scope)}
            {activeRound.max_adds != null && ` · máx. ${activeRound.max_adds} adições`}
            {activeRound.allows_drops && " · aceita remoções"}
            {activeRound.note && ` · ${activeRound.note}`}
            {campaign.status === "encerrada" && " · campanha encerrada (histórico)"}
          </p>

          <div className="camp-cols">
            <RoundBoard
              plan={plan}
              campaign={campaign}
              round={activeRound}
              sectionsByCourse={sectionsByCourse}
              courseByCode={courseByCode}
              onOpenCourse={props.onOpenCourse}
            />
            <CriticalityQueue
              planId={plan.id}
              campaign={campaign}
              round={activeRound}
              sectionById={sectionById}
              courseByCode={courseByCode}
              onOpenCourse={props.onOpenCourse}
            />
          </div>

          {/* C2 (spec 8.4): swap assistant only on the round with the two-waves
              semantics (drops_before_adds is DATA from the grade rules — ADR-018,
              never a round-name check); i.e. the correction round. */}
          {activeRound.drops_before_adds && (
            <SwapPanel
              planId={plan.id}
              campaign={campaign}
              round={activeRound}
              sectionById={sectionById}
              courseByCode={courseByCode}
              onOpenCourse={props.onOpenCourse}
            />
          )}

          {/* C3 (spec 8.4): special-enrollment advisor only on the round whose
              max_adds limits the choices (ADR-018 DATA from the grade rules —
              never a round-name check); i.e. the special round. */}
          {activeRound.max_adds != null && (
            <SpecialAdvisor
              plan={plan}
              campaign={campaign}
              round={activeRound}
              sectionById={sectionById}
              courseByCode={courseByCode}
              onOpenCourse={props.onOpenCourse}
            />
          )}

          {/* C2 (ADR-024): campaign-wide stateless simulator — pending requests of ALL rounds. */}
          <WhatIfPanel planId={plan.id} campaign={campaign} courseByCode={courseByCode} />
        </>
      )}
    </main>
  );
}
