import { useEffect, useState } from "react";
import type {
  Campaign,
  CampaignRound,
  CurriculumCourse,
  Diagnostic,
  EnrollmentRequest,
  EnrollmentRequestStatus,
  PlanDetail,
  Section,
} from "../../api/types";
import { useCampaignRequestMutations } from "../../state/queries";
import { friendlyCampaignError, sectionLabelFromId } from "./format";

export interface RoundBoardProps {
  plan: PlanDetail;
  campaign: Campaign;
  round: CampaignRound;
  /** Offer of the campaign term, for the section picker of non-terminal adds. */
  sectionsByCourse: Map<string, Section[]>;
  courseByCode: Map<string, CurriculumCourse>;
  onOpenCourse: (code: string) => void;
}

const STATUS_GROUPS: { status: EnrollmentRequestStatus; label: string }[] = [
  { status: "desejada", label: "Desejadas" },
  { status: "pedida", label: "Pedidas" },
  { status: "aceita", label: "Aceitas" },
  { status: "negada", label: "Negadas" },
];

// UI affordance only — the server owns the transition graph (spec 5.2) and
// answers 409 INVALID_TRANSITION for anything outside it.
const NEXT_STATUSES: Record<EnrollmentRequestStatus, EnrollmentRequestStatus[]> = {
  desejada: ["pedida", "aceita", "negada"],
  pedida: ["aceita", "negada"],
  aceita: [],
  negada: [],
};

const TRANSITION_LABEL: Partial<Record<EnrollmentRequestStatus, string>> = {
  pedida: "pedir",
  aceita: "aceita ✓",
  negada: "negada ✕",
};

const TRANSITION_TITLE: Partial<Record<EnrollmentRequestStatus, string>> = {
  pedida: "Marca como pedida no Cobalto",
  aceita: "Registra o aceite e sincroniza a turma com o plano",
  negada: "Registra a negativa (re-pedir = novo pedido em outra rodada)",
};

interface SyncNotice {
  courseCode: string;
  diagnostics: Diagnostic[];
}

export function RoundBoard(props: RoundBoardProps) {
  const { plan, campaign, round, courseByCode } = props;
  const mut = useCampaignRequestMutations(plan.id, campaign.id);

  const [actionError, setActionError] = useState<string | null>(null);
  const [sync, setSync] = useState<SyncNotice | null>(null);
  const [noteEditing, setNoteEditing] = useState<number | null>(null);
  const [noteDraft, setNoteDraft] = useState("");

  useEffect(() => {
    setActionError(null);
    setSync(null);
    setNoteEditing(null);
  }, [campaign.id, round.key]);

  const requests = campaign.requests.filter((r) => r.round === round.key);
  const busy = mut.patch.isPending || mut.remove.isPending;

  // D8: credit overload of the campaign term is a WARNING from the revalidated
  // plan — always visible while it exists, and it never blocks any request.
  const overload = plan.diagnostics.filter(
    (d) => d.type === "TERM_OVERLOADED" && d.term === campaign.term,
  );

  function transition(req: EnrollmentRequest, status: EnrollmentRequestStatus) {
    setActionError(null);
    const body: { status: EnrollmentRequestStatus; note?: string } = { status };
    if (status === "negada") {
      const v = window.prompt("Nota sobre a negativa (opcional):", req.note ?? "");
      if (v === null) return; // cancelled
      if (v.trim() !== "") body.note = v.trim();
    }
    mut.patch.mutate(
      { requestId: req.id, body },
      {
        onSuccess: (res) => {
          if (res.plan_synced && res.plan) {
            // Reflect the fresh diagnostics of the revalidated plan (accept synced plan_item).
            const diags = res.plan.diagnostics.filter(
              (d) => d.term === campaign.term || d.course_codes.includes(req.course_code),
            );
            setSync({ courseCode: req.course_code, diagnostics: diags });
          }
        },
        onError: (err) => setActionError(friendlyCampaignError(err)),
      },
    );
  }

  function setSection(req: EnrollmentRequest, sectionId: string | null) {
    setActionError(null);
    mut.patch.mutate(
      { requestId: req.id, body: { section_id: sectionId } },
      { onError: (err) => setActionError(friendlyCampaignError(err)) },
    );
  }

  function saveNote(req: EnrollmentRequest) {
    setActionError(null);
    mut.patch.mutate(
      { requestId: req.id, body: { note: noteDraft.trim() === "" ? null : noteDraft.trim() } },
      {
        onSuccess: () => setNoteEditing(null),
        onError: (err) => setActionError(friendlyCampaignError(err)),
      },
    );
  }

  function removeRequest(req: EnrollmentRequest) {
    if (!window.confirm(`Apagar o pedido de ${req.course_code}?`)) return;
    setActionError(null);
    mut.remove.mutate(req.id, { onError: (err) => setActionError(friendlyCampaignError(err)) });
  }

  function renderRequest(req: EnrollmentRequest) {
    const course = courseByCode.get(req.course_code);
    const terminal = req.status === "aceita" || req.status === "negada";
    const offers = props.sectionsByCourse.get(req.course_code) ?? [];
    const staleChoice = req.section_id != null && !offers.some((s) => s.id === req.section_id);
    const acceptNeedsSection = req.kind === "add" && !req.section_id;

    return (
      <div key={req.id} className={`camp-req st-${req.status}`}>
        <div className="camp-req-main">
          <button
            type="button"
            className="camp-req-name"
            title="Ver no fluxograma"
            onClick={() => props.onOpenCourse(req.course_code)}
          >
            {course?.short ?? req.course_code}
            <span className="muted"> {req.course_name ?? course?.name ?? ""}</span>
          </button>
          {req.kind === "drop" && (
            <span
              className="chip chip-danger"
              title="Pedido de remoção — o aceite limpa a turma no plano (a alocação permanece)"
            >
              remoção
            </span>
          )}
          {req.section_label && <span className="chip">{req.section_label}</span>}
          {req.section_id && req.section_alive === false && (
            // ADR-017: re-scrape removed the section; the snapshot keeps the history readable.
            <span
              className="chip chip-danger"
              title="A turma pedida não existe mais na oferta (re-scrape) — o rótulo vem do snapshot"
            >
              turma extinta
            </span>
          )}
          {req.kind === "add" &&
            req.status === "aceita" &&
            (req.in_plan ? (
              <span className="chip camp-chip-ok" title="O plano aponta exatamente esta turma">
                no plano ✓
              </span>
            ) : (
              // Desync is SHOWN, never auto-fixed (ADR-017): plan_item is the source of truth.
              <span
                className="chip chip-blocked"
                title="O plano não aponta mais esta turma — confira o fluxograma (o pedido é registro histórico)"
              >
                difere do plano
              </span>
            ))}
        </div>

        {!terminal && req.kind === "add" && (offers.length > 0 || req.section_id) && (
          <div className="camp-req-section">
            <label className="tb-label" htmlFor={`camp-sec-${req.id}`}>Turma</label>
            <select
              id={`camp-sec-${req.id}`}
              value={req.section_id ?? ""}
              disabled={busy}
              onChange={(e) => setSection(req, e.target.value === "" ? null : e.target.value)}
            >
              <option value="">— sem turma —</option>
              {staleChoice && req.section_id && (
                <option value={req.section_id}>
                  {req.section_label ?? sectionLabelFromId(req.section_id)} (extinta)
                </option>
              )}
              {offers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label ?? sectionLabelFromId(s.id)}
                  {s.professor ? ` · ${s.professor}` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        {noteEditing === req.id ? (
          <div className="camp-note-edit">
            <input
              value={noteDraft}
              placeholder="ex.: coordenador aprovou"
              onChange={(e) => setNoteDraft(e.target.value)}
            />
            <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => saveNote(req)}>
              salvar
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setNoteEditing(null)}>
              cancelar
            </button>
          </div>
        ) : (
          req.note && <p className="camp-note">“{req.note}”</p>
        )}

        <div className="camp-req-actions">
          {NEXT_STATUSES[req.status].map((to) => {
            const disabled = busy || (to === "aceita" && acceptNeedsSection);
            return (
              <button
                key={to}
                type="button"
                className={`btn btn-sm ${to === "aceita" ? "btn-primary" : ""}`}
                disabled={disabled}
                title={
                  to === "aceita" && acceptNeedsSection
                    ? "Escolha a turma aceita antes de marcar (o aceite cria o item no plano)"
                    : TRANSITION_TITLE[to]
                }
                onClick={() => transition(req, to)}
              >
                {TRANSITION_LABEL[to]}
              </button>
            );
          })}
          <button
            type="button"
            className="btn btn-sm"
            title="Editar nota (livre — D4, editável em qualquer estado)"
            onClick={() => {
              setNoteEditing(req.id);
              setNoteDraft(req.note ?? "");
            }}
          >
            nota
          </button>
          {!terminal && (
            <button
              type="button"
              className="btn btn-sm btn-danger-ghost"
              disabled={busy}
              title="Apagar pedido (aceita/negada são histórico e não podem ser apagados)"
              onClick={() => removeRequest(req)}
            >
              🗑
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <section className="camp-board sched-card" aria-label={`Pedidos da rodada ${round.label}`}>
      <div className="sched-card-head">
        <h3>Pedidos · {round.label}</h3>
        <span className="muted">{requests.length} pedido(s)</span>
      </div>

      {overload.length > 0 && (
        <div className="camp-warn-banner" role="status">
          {overload.map((d) => (
            <p key={d.id}>{d.message}</p>
          ))}
          <p className="camp-warn-note">
            Aviso apenas: o limite de créditos nunca bloqueia pedidos — negativas ainda podem abrir espaço.
          </p>
        </div>
      )}

      {actionError && (
        <p className="camp-error" role="alert">
          {actionError}
          <button
            type="button"
            className="camp-dismiss"
            aria-label="Fechar aviso"
            onClick={() => setActionError(null)}
          >
            ×
          </button>
        </p>
      )}

      {sync && (
        <div className="camp-sync" role="status">
          <div className="camp-sync-head">
            <strong>Plano revalidado</strong>
            <span className="muted">
              aceite de {courseByCode.get(sync.courseCode)?.short ?? sync.courseCode} aplicado em {campaign.term}
            </span>
            <button
              type="button"
              className="camp-dismiss"
              aria-label="Fechar"
              onClick={() => setSync(null)}
            >
              ×
            </button>
          </div>
          {sync.diagnostics.length === 0 ? (
            <p className="sp-ok">✓ Sem novos avisos no plano.</p>
          ) : (
            sync.diagnostics.map((d) => (
              <p key={d.id} className={`sp-diag sev-${d.severity}`}>
                {d.message}
              </p>
            ))
          )}
        </div>
      )}

      {requests.length === 0 && (
        <p className="muted">
          Nenhum pedido nesta rodada — use a fila de criticidade ao lado para pedir as cadeiras mais importantes.
        </p>
      )}

      {STATUS_GROUPS.map(({ status, label }) => {
        const group = requests.filter((r) => r.status === status);
        if (group.length === 0) return null;
        return (
          <div key={status} className="camp-group">
            <h4>
              {label} ({group.length})
            </h4>
            {group.map(renderRequest)}
          </div>
        );
      })}
    </section>
  );
}
