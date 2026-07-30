import { useEffect, useState } from "react";
import type {
  Campaign,
  CampaignRound,
  CurriculumCourse,
  Section,
  SwapChoice,
  SwapSuggestion,
} from "../../api/types";
import { useCampaignRequestMutations, useSwapSuggestions } from "../../state/queries";
import { WD } from "../SchedulePage/format";
import { friendlyCampaignError, sectionLabelFromId } from "./format";

export interface SwapPanelProps {
  planId: string;
  campaign: Campaign;
  /** The correction round — the panel is mounted only for the round with the
   * two-waves semantics (drops_before_adds, ADR-018 data, never a name check). */
  round: CampaignRound;
  /** Offer of the campaign term, for human section labels. */
  sectionById: Map<string, Section>;
  courseByCode: Map<string, CurriculumCourse>;
  onOpenCourse: (code: string) => void;
}

function deltaText(delta: number): string {
  return Number.isInteger(delta) ? String(delta) : delta.toFixed(1);
}

function breakdownText(s: SwapSuggestion): string {
  const b = s.score_breakdown;
  const gain: string[] = [];
  if (b.gained_critical > 0) gain.push(`${b.gained_critical} do caminho crítico`);
  if (b.gained_unlocks > 0) gain.push(`destrava ${b.gained_unlocks}`);
  gain.push(`${b.gained_credits}cr`);
  const loss: string[] = [];
  if (b.lost_critical > 0) loss.push(`${b.lost_critical} do caminho crítico`);
  if (b.lost_unlocks > 0) loss.push(`destrava ${b.lost_unlocks}`);
  loss.push(`${b.lost_credits}cr`);
  return `ganha ${gain.join(", ")} · perde ${loss.join(", ")}`;
}

export function SwapPanel(props: SwapPanelProps) {
  const { planId, campaign, round, courseByCode } = props;
  const swaps = useSwapSuggestions(planId, campaign.id);
  const mut = useCampaignRequestMutations(planId, campaign.id);

  const [applying, setApplying] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Results from another campaign/round would mislead — drop them on switch.
  useEffect(() => {
    swaps.reset();
    setError(null);
    setNotice(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign.id, round.key]);

  // "Applied" = every leg already has a matching request (kind+course) in this
  // round. Presentation only — real duplication is the server's 409 CONFLICT.
  const existing = new Set(
    campaign.requests.filter((r) => r.round === round.key).map((r) => `${r.kind}:${r.course_code}`),
  );
  const isApplied = (s: SwapSuggestion) =>
    s.drop.every((d) => existing.has(`drop:${d.course_code}`)) &&
    s.add.every((a) => existing.has(`add:${a.course_code}`));

  const short = (code: string) => courseByCode.get(code)?.short ?? code;
  const sectionLabel = (c: SwapChoice) =>
    props.sectionById.get(c.section_id)?.label ?? sectionLabelFromId(c.section_id);
  const freedText = (s: SwapSuggestion) =>
    s.freed_slots.map((t) => `${WD[t.weekday] ?? t.weekday} ${t.start}–${t.end}`).join(", ");

  async function apply(s: SwapSuggestion) {
    setError(null);
    setNotice(null);
    setApplying(s.rank);
    const warnings: string[] = [];
    try {
      // Creation mirrors the two waves: drop requests first, then adds.
      // Applying a swap ONLY creates requests — the plan changes on accept.
      for (const d of s.drop) {
        if (existing.has(`drop:${d.course_code}`)) continue;
        const res = await mut.create.mutateAsync({
          round: round.key,
          kind: "drop",
          course_code: d.course_code,
          section_id: d.section_id,
        });
        warnings.push(...res.warnings);
      }
      for (const a of s.add) {
        if (existing.has(`add:${a.course_code}`)) continue;
        const res = await mut.create.mutateAsync({
          round: round.key,
          kind: "add",
          course_code: a.course_code,
          section_id: a.section_id,
        });
        warnings.push(...res.warnings);
      }
      setNotice(
        ["Pedidos criados na rodada — acompanhe e marque o resultado no quadro de pedidos.", ...warnings].join(" · "),
      );
    } catch (err) {
      setError(friendlyCampaignError(err));
    } finally {
      setApplying(null);
    }
  }

  const data = swaps.data;
  const busy = applying !== null || mut.create.isPending;

  return (
    <section className="swap-panel sched-card" aria-label="Assistente de troca da correção">
      <div className="sched-card-head">
        <h3>Vale a troca? · {round.label}</h3>
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={swaps.isPending || busy}
          onClick={() => swaps.mutate(undefined)}
        >
          {swaps.isPending ? "Calculando…" : data ? "Recalcular" : "Buscar trocas vantajosas"}
        </button>
      </div>
      <p className="muted">
        A correção processa em <strong>2 ondas: remoções primeiro, adições depois</strong> — o
        horário liberado por um “largar” conta como livre para o “pegar” do mesmo pedido. As
        sugestões vêm do servidor; aplicar cria os pedidos <em>drop</em>+<em>add</em> nesta rodada
        (o plano só muda quando você marcar o aceite).
      </p>

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
      {notice && (
        <p className="sp-diag sev-warning" role="status">
          {notice}
        </p>
      )}

      {swaps.isError && (
        <div role="alert">
          <p className="sp-diag sev-error">Falha ao buscar sugestões de troca.</p>
          <button type="button" className="btn btn-sm" onClick={() => swaps.mutate(undefined)}>
            Tentar de novo
          </button>
        </div>
      )}

      {data && (
        <p className="swap-held">
          Base considerada em {data.term}: {data.held_sections.length} turma(s) mantida(s) (aceitas
          da campanha + itens do semestre com turma).
        </p>
      )}

      {data && data.suggestions.length === 0 && (
        <p className="muted">
          Nenhuma troca vantajosa — o servidor só sugere combinações com ganho líquido de
          criticidade (delta &gt; 0) e sem conflito de horário após as 2 ondas.
        </p>
      )}

      {data && data.truncated && (
        <p className="sp-diag sev-warning">
          Busca truncada pelo limite de combinações — estas são as melhores encontradas até o corte.
        </p>
      )}

      {data?.suggestions.map((s) => {
        const applied = isApplied(s);
        return (
          <div key={s.rank} className="swap-sug">
            <div className="swap-sug-head">
              <span className="camp-rank" aria-hidden>
                #{s.rank}
              </span>
              <span
                className="swap-delta"
                title="valor(adições) − valor(remoções), pela criticidade das cadeiras (servidor)"
              >
                Δ +{deltaText(s.score_delta)}
              </span>
              <span className="muted">{breakdownText(s)}</span>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={busy || applied}
                title={
                  applied
                    ? "Os pedidos desta troca já existem na rodada"
                    : "Cria os pedidos na rodada: remoções processam antes das adições — nunca mexe no plano direto"
                }
                onClick={() => void apply(s)}
              >
                {applied ? "aplicada ✓" : applying === s.rank ? "aplicando…" : "aplicar"}
              </button>
            </div>
            <div className="swap-waves">
              <div className="swap-wave is-drop">
                <h5>1ª onda · largar</h5>
                {s.drop.length === 0 ? (
                  <span className="muted">nada — adição pura</span>
                ) : (
                  s.drop.map((c) => (
                    <div key={c.section_id} className="swap-choice">
                      <button
                        type="button"
                        className="swap-choice-name"
                        title="Ver no fluxograma"
                        onClick={() => props.onOpenCourse(c.course_code)}
                      >
                        {short(c.course_code)}
                      </button>
                      <span className="chip">{sectionLabel(c)}</span>
                    </div>
                  ))
                )}
                {s.freed_slots.length > 0 && (
                  <p className="swap-freed" title="Por que as adições cabem: estes horários ficam livres na 2ª onda">
                    libera: {freedText(s)}
                  </p>
                )}
              </div>
              <div className="swap-wave is-add">
                <h5>2ª onda · pegar</h5>
                {s.add.map((c) => (
                  <div key={c.section_id} className="swap-choice">
                    <button
                      type="button"
                      className="swap-choice-name"
                      title="Ver no fluxograma"
                      onClick={() => props.onOpenCourse(c.course_code)}
                    >
                      {short(c.course_code)}
                    </button>
                    <span className="chip">{sectionLabel(c)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </section>
  );
}
