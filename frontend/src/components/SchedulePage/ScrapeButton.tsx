import { useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import { useScrapeSections } from "../../state/queries";
import { sourceLabel } from "./format";

export interface ScrapeButtonProps {
  planId: string;
  /** Always next_term(plan.current_term) — server derives the same term (ADR-011). */
  targetTerm: string;
}

function errorLines(err: unknown, targetTerm: string): string[] {
  const manualPath = `PUT /api/v1/admin/terms/${targetTerm.replace("/", "-")}/sections`;
  if (err instanceof ApiError) {
    if (err.code === "SCRAPER_FAILED") {
      return [
        err.message,
        `Nada foi alterado no banco. O caminho primário continua sendo o import manual da oferta (${manualPath}).`,
      ];
    }
    if (err.code === "PORTAL_CODE_MISSING") {
      return [
        err.message,
        "Falta o código do curso no portal: preencha meta.codigo_curso no seed e suba o backend de novo — ou use o import manual.",
      ];
    }
    return [err.message];
  }
  return ["Falha inesperada ao buscar ofertas. Tente novamente."];
}

export function ScrapeButton({ planId, targetTerm }: ScrapeButtonProps) {
  const scrape = useScrapeSections(planId);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!scrape.isPending) return;
    setElapsed(0);
    const id = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, [scrape.isPending]);

  const result = scrape.data;

  return (
    <div className="sched-card sched-scrape">
      <div className="scrape-head">
        <div>
          <h3>Oferta de turmas</h3>
          <p className="muted">
            Busca no portal da UFPel as turmas de {targetTerm} das cadeiras que faltam. Termo-alvo derivado do plano.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={scrape.isPending}
          onClick={() => scrape.mutate()}
        >
          {scrape.isPending ? "Buscando…" : `Buscar ofertas ${targetTerm}`}
        </button>
      </div>

      {scrape.isPending && (
        <div className="scrape-progress" role="status" aria-live="polite">
          <span className="spinner" aria-hidden />
          <span>
            Consultando o portal… a varredura completa das páginas dos professores pode levar alguns minutos ({elapsed}s).
            Pode deixar esta aba aberta — o resultado aparece quando terminar.
          </span>
        </div>
      )}

      {scrape.isError && (
        <div className="scrape-error" role="alert">
          {errorLines(scrape.error, targetTerm).map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      {result && (
        <div className="scrape-summary" aria-live="polite">
          <p>
            <strong>{result.upserted}</strong> turma(s) importada(s) para {result.term} via{" "}
            {result.level_used === 1 ? "página do curso (nível 1)" : "páginas dos professores (nível 2)"} —{" "}
            {sourceLabel(result.source)}. {result.pages_fetched} página(s) lida(s)
            {result.pages_failed > 0 ? `, ${result.pages_failed} falhou/falharam e foram puladas` : ""}.
          </p>
          {(result.removed > 0 || result.orphaned_choices > 0) && (
            <p className="muted">
              {result.removed > 0 && `${result.removed} turma(s) removida(s) por terem sumido do portal. `}
              {result.orphaned_choices > 0 &&
                `${result.orphaned_choices} escolha(s) de turma ficaram órfãs — veja os avisos (SECTION_STALE).`}
            </p>
          )}
          {result.rejected.length > 0 && (
            <p className="muted">
              Rejeitadas: {result.rejected.map((r) => `${r.course_code} (${r.reason})`).join("; ")}
            </p>
          )}
          {result.missing_without_offer.length > 0 ? (
            <div>
              <p>Cadeiras faltantes sem oferta encontrada em {result.term}:</p>
              <ul className="scrape-missing">
                {result.missing_without_offer.map((m) => (
                  <li key={m.code}>
                    <strong>{m.name}</strong> <span className="mono">{m.code}</span> — {m.reason}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            result.upserted > 0 && <p className="sp-ok">Todas as cadeiras faltantes têm oferta em {result.term}.</p>
          )}
          {result.discovered_optativas.length > 0 && (
            <p className="sp-ok">
              {result.discovered_optativas.length} optativa(s) ofertada(s) descoberta(s) no portal — aparecem
              na lista de cadeiras abaixo com o selo "optativa".
            </p>
          )}
          {result.upserted === 0 && result.missing_without_offer.length === 0 && result.discovered_optativas.length === 0 && (
            <p className="muted">Nada para importar: nenhuma cadeira faltante no escopo deste termo.</p>
          )}
        </div>
      )}
    </div>
  );
}
