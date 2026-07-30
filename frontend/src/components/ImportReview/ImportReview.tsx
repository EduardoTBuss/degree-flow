import { useState } from "react";
import type { ImportProposal } from "../../api/types";
import { useApplyHistorico, useImportHistorico } from "../../state/queries";
import { ApiError } from "../../api/client";

export interface ImportReviewProps {
  planId: string | undefined;
  gvId: string | undefined;
  onClose: () => void;
  onApplied: () => void;
}

export function ImportReview({ planId, gvId, onClose, onApplied }: ImportReviewProps) {
  const importMut = useImportHistorico();
  const applyMut = useApplyHistorico(planId, gvId);
  const [proposal, setProposal] = useState<ImportProposal | null>(null);
  const [setStart, setSetStart] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function onFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    importMut.mutate(file, {
      onSuccess: (p) => setProposal(p),
      onError: (e) => setError(e instanceof ApiError ? e.message : "Falha ao ler o PDF."),
    });
  }

  function toggle(idx: number) {
    if (!proposal) return;
    const matches = proposal.matches.map((m, i) => (i === idx ? { ...m, apply: !m.apply } : m));
    setProposal({ ...proposal, matches });
  }

  function apply() {
    if (!proposal) return;
    applyMut.mutate(
      { proposal, setStart },
      { onSuccess: () => { onApplied(); onClose(); } },
    );
  }

  const selected = proposal?.matches.filter((m) => m.apply).length ?? 0;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>Importar histórico (PDF)</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Fechar">×</button>
        </header>

        {!proposal && (
          <div className="import-drop">
            <p>Envie o PDF do seu histórico ou integralização exportado do Cobalto.</p>
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => onFile(e.target.files?.[0])}
              disabled={importMut.isPending}
            />
            {importMut.isPending && <p className="muted">Lendo o PDF…</p>}
            {error && <p className="import-error">{error}</p>}
          </div>
        )}

        {proposal && (
          <div className="import-review">
            <div className="import-summary">
              <span className="chip">{proposal.source.parser}</span>
              {proposal.inferred.start_term && <span className="chip">ingresso {proposal.inferred.start_term}</span>}
              <span className="chip chip-primary">{selected}/{proposal.matches.length} selecionadas</span>
            </div>
            {proposal.warnings.map((w, i) => (
              <p key={i} className="import-warn">⚠ {w}</p>
            ))}

            <label className="import-startline">
              <input type="checkbox" checked={setStart} onChange={(e) => setSetStart(e.target.checked)} />
              definir "comecei em" como {proposal.inferred.start_term ?? "—"}
            </label>

            <div className="import-table" role="table">
              <div className="import-row import-header" role="row">
                <span>✓</span><span>Código</span><span>Cadeira</span><span>Status</span><span>Semestre</span>
              </div>
              {proposal.matches.map((m, i) => (
                <div key={m.code} className={`import-row ${m.conflict ? "is-conflict" : ""}`} role="row">
                  <span><input type="checkbox" checked={m.apply} onChange={() => toggle(i)} /></span>
                  <span className="mono">{m.code}</span>
                  <span className="import-name">{m.name_in_pdf}</span>
                  <span>
                    {m.status_inferred}
                    {m.conflict && <em className="import-was"> (atual: {m.current_status})</em>}
                  </span>
                  <span>{m.term ?? "—"}</span>
                </div>
              ))}
            </div>

            {proposal.unmatched.length > 0 && (
              <details className="import-unmatched">
                <summary>{proposal.unmatched.length} não reconhecidas (fora da grade)</summary>
                <ul>
                  {proposal.unmatched.map((u) => (
                    <li key={u.code_in_pdf}><span className="mono">{u.code_in_pdf}</span> {u.name_in_pdf} — {u.hint}</li>
                  ))}
                </ul>
              </details>
            )}

            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setProposal(null)}>Trocar PDF</button>
              <button type="button" className="btn btn-primary" disabled={applyMut.isPending || selected === 0} onClick={apply}>
                {applyMut.isPending ? "Aplicando…" : `Aplicar ${selected} cadeira(s)`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
