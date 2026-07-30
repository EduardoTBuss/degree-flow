import { useEffect, useRef, useState } from "react";
import type { CriticalPath, GradeVersion, OffendingItem, PlanDetail, PlanSummary } from "../../api/types";
import { ApiError } from "../../api/client";
import { isValidTerm, nextTerm } from "../../lib/terms";
import { useTheme, type ThemePreference } from "../../lib/theme";

export type TopView = "flow" | "schedule" | "enrollment"; // 3rd view: ADR-023 (still no router)

export interface PlanToolbarProps {
  plans: PlanSummary[];
  plan: PlanDetail;
  gradeVersions: GradeVersion[];
  criticalPath: CriticalPath | null;
  showCriticalPath: boolean;
  autoplanBusy: boolean;
  hasPreview: boolean;
  view: TopView;
  onChangeView: (view: TopView) => void;
  onSelectPlan: (id: string) => void;
  onCreatePlan: (name: string) => void;
  onDeletePlan: (id: string) => void;
  onPatchPlan: (
    patch: Partial<{
      grade_version_id: string;
      target_term: string | null;
      start_term: string | null;
      current_term: string;
      max_credits_per_term: number;
      max_difficulty_per_term: number | null;
    }>,
    opts?: { onError?: (err: unknown) => void },
  ) => void;
  onToggleCriticalPath: (v: boolean) => void;
  onRunAutoplan: (target: string, mode: "preview" | "apply") => void;
  onClearPreview: () => void;
  onApplyPreview: () => void;
  onOpenImport: () => void;
}

interface CurrentTermError {
  message: string;
  items: OffendingItem[];
}

const THEME_OPTIONS: { value: ThemePreference; label: string; icon: string }[] = [
  { value: "light", label: "Claro", icon: "☀" },
  { value: "dark", label: "Escuro", icon: "☾" },
  { value: "system", label: "Sistema", icon: "◐" },
];

/** A1 (ADR-022): gear button opening a light/dark/system menu. Pure UI — theme state lives in useTheme. */
function ThemeMenu() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Close when clicking anywhere outside the menu.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  // Move focus to the checked option when the menu opens.
  useEffect(() => {
    if (!open) return;
    const idx = Math.max(0, THEME_OPTIONS.findIndex((o) => o.value === theme));
    itemRefs.current[idx]?.focus();
  }, [open, theme]);

  function closeAndRefocus() {
    setOpen(false);
    btnRef.current?.focus();
  }

  function onMenuKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const active = itemRefs.current.findIndex((el) => el === document.activeElement);
    if (e.key === "Escape") {
      e.preventDefault();
      closeAndRefocus();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const dir = e.key === "ArrowDown" ? 1 : -1;
      const next = (active + dir + THEME_OPTIONS.length) % THEME_OPTIONS.length;
      itemRefs.current[next]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      itemRefs.current[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      itemRefs.current[THEME_OPTIONS.length - 1]?.focus();
    } else if (e.key === "Tab") {
      setOpen(false); // let focus leave naturally
    }
  }

  const current = THEME_OPTIONS.find((o) => o.value === theme);

  return (
    <div className="tb-settings" ref={rootRef}>
      <button
        type="button"
        ref={btnRef}
        className="btn btn-sm tb-settings-btn"
        title={`Tema: ${current?.label ?? "Sistema"}`}
        aria-label="Configurações de tema"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        ⚙
      </button>
      {open && (
        <div className="theme-menu" role="menu" aria-label="Tema" onKeyDown={onMenuKeyDown}>
          {THEME_OPTIONS.map((opt, i) => (
            <button
              key={opt.value}
              type="button"
              role="menuitemradio"
              aria-checked={theme === opt.value}
              className="theme-menu-item"
              ref={(el) => { itemRefs.current[i] = el; }}
              onClick={() => { setTheme(opt.value); closeAndRefocus(); }}
            >
              <span className="theme-menu-icon" aria-hidden>{opt.icon}</span>
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Narrow the 400 details.offending_items payload (ADR-010) without trusting the wire. */
function offendingItemsFrom(details: Record<string, unknown> | undefined): OffendingItem[] {
  const raw = details?.offending_items;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (it): it is OffendingItem =>
      typeof it === "object" &&
      it !== null &&
      typeof (it as Record<string, unknown>).course_code === "string" &&
      typeof (it as Record<string, unknown>).term === "string",
  );
}

export function PlanToolbar(props: PlanToolbarProps) {
  const { plan, plans, gradeVersions, criticalPath, view } = props;
  const [target, setTarget] = useState(plan.target_term ?? nextTerm(plan.current_term, 4));
  const [start, setStart] = useState(plan.start_term ?? "");
  const [current, setCurrent] = useState(plan.current_term);
  const [currentError, setCurrentError] = useState<CurrentTermError | null>(null);
  const [credits, setCredits] = useState(String(plan.max_credits_per_term));
  const [maxDiff, setMaxDiff] = useState(plan.max_difficulty_per_term?.toString() ?? "");

  useEffect(() => {
    setTarget(plan.target_term ?? nextTerm(plan.current_term, 4));
    setStart(plan.start_term ?? "");
    setCredits(String(plan.max_credits_per_term));
    setMaxDiff(plan.max_difficulty_per_term?.toString() ?? "");
  }, [plan.id, plan.target_term, plan.start_term, plan.max_credits_per_term, plan.max_difficulty_per_term, plan.current_term]);

  // Server accepted a new current_term (or plan switched): sync field, clear stale error.
  useEffect(() => {
    setCurrent(plan.current_term);
    setCurrentError(null);
  }, [plan.id, plan.current_term]);

  const targetValid = isValidTerm(target);
  const startValid = start === "" || isValidTerm(start);
  const currentValid = isValidTerm(current);

  function submitCurrentTerm() {
    if (!currentValid || current === plan.current_term) return;
    const previous = plan.current_term;
    setCurrentError(null);
    props.onPatchPlan(
      { current_term: current },
      {
        onError: (err) => {
          // Never "fix" the plan client-side: revert the field, show what blocks it.
          setCurrent(previous);
          if (err instanceof ApiError) {
            setCurrentError({ message: err.message, items: offendingItemsFrom(err.details) });
          } else {
            setCurrentError({ message: "Falha ao salvar 'Estou em'. Tente novamente.", items: [] });
          }
        },
      },
    );
  }

  return (
    <header className="toolbar">
      <div className="tb-brand">
        <span className="tb-logo" aria-hidden>◧</span>
        <div>
          <strong>Degree Flow</strong>
          <span className="tb-sub">planejador de formatura</span>
        </div>
      </div>

      <nav className="view-tabs" role="tablist" aria-label="Seções do app">
        <button
          type="button"
          role="tab"
          aria-selected={view === "flow"}
          className={view === "flow" ? "on" : ""}
          onClick={() => props.onChangeView("flow")}
        >
          Fluxograma
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "schedule"}
          className={view === "schedule" ? "on" : ""}
          onClick={() => props.onChangeView("schedule")}
        >
          Horários
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "enrollment"}
          className={view === "enrollment" ? "on" : ""}
          onClick={() => props.onChangeView("enrollment")}
        >
          Matrícula
        </button>
      </nav>

      <div className="tb-group">
        <label className="tb-label" htmlFor="tb-plan">Plano</label>
        <select id="tb-plan" value={plan.id} onChange={(e) => props.onSelectPlan(e.target.value)}>
          {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button type="button" className="btn btn-sm" title="Novo plano" onClick={() => {
          const name = window.prompt("Nome do novo plano:", "Novo cenário");
          if (name) props.onCreatePlan(name);
        }}>＋</button>
        {plans.length > 1 && (
          <button type="button" className="btn btn-sm btn-danger-ghost" title="Excluir plano" onClick={() => {
            if (window.confirm(`Excluir o plano "${plan.name}"?`)) props.onDeletePlan(plan.id);
          }}>🗑</button>
        )}
      </div>

      <div className="tb-group">
        <label className="tb-label" htmlFor="tb-grade">Grade</label>
        <select id="tb-grade" value={plan.grade_version_id} onChange={(e) => props.onPatchPlan({ grade_version_id: e.target.value })}>
          {gradeVersions.map((g) => (
            <option key={g.id} value={g.id}>{g.label}{g.is_default ? " (padrão)" : ""}</option>
          ))}
        </select>
      </div>

      <div className="tb-group">
        <label className="tb-label" htmlFor="tb-start" title="Semestre em que você começou o curso">Comecei em</label>
        <input
          id="tb-start"
          className={`tb-term ${startValid ? "" : "invalid"}`}
          value={start}
          placeholder="AAAA/S"
          onChange={(e) => setStart(e.target.value)}
          onBlur={() => { if (startValid && (start || null) !== (plan.start_term ?? null)) props.onPatchPlan({ start_term: start || null }); }}
        />
      </div>

      <div className="tb-group">
        <label className="tb-label" htmlFor="tb-current" title="Semestre que você está cursando agora">Estou em</label>
        <input
          id="tb-current"
          className={`tb-term ${currentValid ? "" : "invalid"}`}
          value={current}
          placeholder="AAAA/S"
          aria-invalid={!currentValid || currentError !== null}
          aria-describedby={currentError ? "tb-current-error" : undefined}
          onChange={(e) => setCurrent(e.target.value)}
          onBlur={submitCurrentTerm}
        />
      </div>

      <div className="tb-group">
        <label className="tb-label" htmlFor="tb-target">Formar em</label>
        <input
          id="tb-target"
          className={`tb-term ${targetValid ? "" : "invalid"}`}
          value={target}
          placeholder="AAAA/S"
          onChange={(e) => setTarget(e.target.value)}
          onBlur={() => { if (targetValid && target !== plan.target_term) props.onPatchPlan({ target_term: target }); }}
        />
      </div>

      <div className="tb-group">
        <label className="tb-label" htmlFor="tb-credits" title="Máx. créditos por semestre">Máx cr</label>
        <input id="tb-credits" className="tb-num" type="number" min={1} value={credits}
          onChange={(e) => setCredits(e.target.value)}
          onBlur={() => { const n = parseInt(credits, 10); if (n > 0 && n !== plan.max_credits_per_term) props.onPatchPlan({ max_credits_per_term: n }); }} />
        <label className="tb-label" htmlFor="tb-diff" title="Máx. dificuldade por semestre (vazio = sem limite)">Máx dif</label>
        <input id="tb-diff" className="tb-num" type="number" min={1} placeholder="—" value={maxDiff}
          onChange={(e) => setMaxDiff(e.target.value)}
          onBlur={() => { const raw = maxDiff.trim(); const n = raw === "" ? null : parseInt(raw, 10); if (n !== plan.max_difficulty_per_term) props.onPatchPlan({ max_difficulty_per_term: n }); }} />
      </div>

      <div className="tb-group tb-actions">
        <button type="button" className="btn btn-sm" title="Importar histórico do Cobalto (PDF)" onClick={props.onOpenImport}>
          ⭳ Histórico PDF
        </button>
        {view === "flow" && (
          <>
            <label className="tb-check" title="Destaca a maior cadeia de pré-requisitos">
              <input type="checkbox" checked={props.showCriticalPath} onChange={(e) => props.onToggleCriticalPath(e.target.checked)} />
              caminho crítico
              {criticalPath && criticalPath.length_terms > 0 && (
                <span className="tb-cp-badge">{criticalPath.length_terms} sem · até {criticalPath.earliest_completion_term}</span>
              )}
            </label>

            {props.hasPreview ? (
              <>
                <button type="button" className="btn btn-primary" disabled={props.autoplanBusy} onClick={props.onApplyPreview}>✓ Aplicar plano</button>
                <button type="button" className="btn" onClick={props.onClearPreview}>Descartar</button>
              </>
            ) : (
              <button type="button" className="btn btn-primary" disabled={!targetValid || props.autoplanBusy}
                title={targetValid ? "Distribui as cadeiras restantes até o alvo" : "Defina um alvo válido"}
                onClick={() => props.onRunAutoplan(target, "preview")}>
                {props.autoplanBusy ? "Planejando…" : "⚡ Planejar automático"}
              </button>
            )}
          </>
        )}
      </div>

      <ThemeMenu />

      {currentError && (
        <div className="tb-error-row" id="tb-current-error" role="alert">
          <span>{currentError.message}</span>
          {currentError.items.length > 0 && (
            <span className="tb-error-chips">
              {currentError.items.map((it) => (
                <span key={`${it.course_code}:${it.term}`} className="chip chip-danger">
                  {it.course_code} · {it.term}
                </span>
              ))}
            </span>
          )}
          <button type="button" className="tb-error-close" aria-label="Fechar aviso" onClick={() => setCurrentError(null)}>×</button>
        </div>
      )}
    </header>
  );
}
