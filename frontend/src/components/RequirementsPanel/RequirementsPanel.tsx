import { useState } from "react";
import type { RequirementProgress } from "../../api/types";

export interface RequirementsPanelProps {
  categories: RequirementProgress[];
  busy: boolean;
  onAddEntry: (key: string, body: { description: string; hours: number; entry_date?: string | null }) => void;
  onDeleteEntry: (entryId: number) => void;
}

function ProgressBar({ cat }: { cat: RequirementProgress }) {
  const pct = cat.min_hours > 0 ? Math.min(100, (cat.total_hours / cat.min_hours) * 100) : 100;
  return (
    <div className="rq-bar-wrap" title={`${cat.total_hours}h de ${cat.min_hours}h`}>
      <div className={`rq-bar ${cat.satisfied ? "done" : ""}`} style={{ width: `${pct}%` }} />
      <span className="rq-bar-label">
        {cat.total_hours}h / {cat.min_hours}h
        {cat.satisfied ? " ✓" : ` · faltam ${cat.remaining_hours}h`}
      </span>
    </div>
  );
}

function CategoryCard({ cat, busy, onAddEntry, onDeleteEntry }: {
  cat: RequirementProgress;
  busy: boolean;
  onAddEntry: RequirementsPanelProps["onAddEntry"];
  onDeleteEntry: RequirementsPanelProps["onDeleteEntry"];
}) {
  const [open, setOpen] = useState(false);
  const [desc, setDesc] = useState("");
  const [hours, setHours] = useState("");

  const canAdd = desc.trim().length > 0 && Number(hours) > 0;

  return (
    <div className={`rq-card ${cat.satisfied ? "is-done" : ""}`}>
      <div className="rq-head">
        <span className="rq-title">{cat.label}</span>
        {cat.counts_courses && <span className="chip" title="Horas de optativas aprovadas/cursando entram aqui">+ optativas</span>}
      </div>
      <ProgressBar cat={cat} />
      {cat.rule_note && <p className="rq-note">{cat.rule_note}</p>}
      {cat.counts_courses && cat.course_hours > 0 && (
        <p className="rq-subline">{cat.course_hours}h de disciplinas optativas · {cat.logged_hours}h lançadas</p>
      )}

      <button type="button" className="rq-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? "▾" : "▸"} Lançamentos ({cat.entries?.length ?? 0})
      </button>

      {open && (
        <div className="rq-entries">
          {(cat.entries ?? []).map((e) => (
            <div key={e.id} className="rq-entry">
              <span className="rq-entry-desc">{e.description}</span>
              <span className="rq-entry-hours">{e.hours}h</span>
              <button
                type="button"
                className="rq-entry-del"
                aria-label={`Remover ${e.description}`}
                disabled={busy}
                onClick={() => onDeleteEntry(e.id)}
              >×</button>
            </div>
          ))}
          <form
            className="rq-add"
            onSubmit={(ev) => {
              ev.preventDefault();
              if (!canAdd) return;
              onAddEntry(cat.key, { description: desc.trim(), hours: Number(hours) });
              setDesc("");
              setHours("");
            }}
          >
            <input
              className="rq-add-desc"
              placeholder="Descrição (ex.: Monitoria 2025)"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
            <input
              className="rq-add-hours"
              type="number"
              min={1}
              placeholder="h"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
            />
            <button type="submit" className="btn btn-sm" disabled={!canAdd || busy}>＋</button>
          </form>
        </div>
      )}
    </div>
  );
}

export function RequirementsPanel({ categories, busy, onAddEntry, onDeleteEntry }: RequirementsPanelProps) {
  if (categories.length === 0) {
    return <div className="panel-empty"><p>Sem categorias de requisito.</p></div>;
  }
  return (
    <div className="requirements-panel">
      {categories.map((cat) => (
        <CategoryCard key={cat.key} cat={cat} busy={busy} onAddEntry={onAddEntry} onDeleteEntry={onDeleteEntry} />
      ))}
    </div>
  );
}
