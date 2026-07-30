// Shared display helpers for the schedule view (F6). Pure functions only.
import type { Section } from "../../api/types";

/** Weekday labels — backend contract: 0=Monday … 6=Sunday. */
export const WD = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

export function slotText(s: Section): string {
  return s.timeslots.map((t) => `${WD[t.weekday] ?? t.weekday} ${t.start}–${t.end}`).join(", ");
}

/** "HH:MM" -> minutes since midnight; NaN-safe (returns null on garbage). */
export function toMinutes(hhmm: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
  if (!m) return null;
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

export function sourceLabel(source: string): string {
  if (source === "scraper:ufpel-curso") return "portal (aba do curso)";
  if (source === "scraper:ufpel-professores") return "portal (páginas dos professores)";
  if (source === "scraper:ufpel-optativa") return "portal (optativas)";
  if (source.startsWith("scraper")) return "portal";
  if (source.includes("manual") || source.includes("admin")) return "import manual";
  return source;
}

export function freshness(s: Section): string | null {
  if (!s.fetched_at) return null;
  const d = new Date(s.fetched_at);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("pt-BR");
}
