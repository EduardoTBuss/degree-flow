import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/client";
import type { CourseEligibility, CurriculumCourse, Diagnostic, PlanDetail, Section } from "../../api/types";
import { compareTerms, nextTerm } from "../../lib/terms";
import {
  useCampaign,
  useCampaigns,
  useCommitTerm,
  useCourseEligibility,
  useDropElective,
  useElectives,
  useScheduleCheck,
  useSections,
} from "../../state/queries";
import { OfferList } from "./OfferList";
import { RecommendationCard } from "./RecommendationCard";
import { ScrapeButton } from "./ScrapeButton";
import { WeekGrid, type WeekGridBlock } from "./WeekGrid";

export interface SchedulePageProps {
  plan: PlanDetail;
  courses: CurriculumCourse[];
  courseByCode: Map<string, CurriculumCourse>;
  savingSection: boolean;
  onSetSection: (code: string, term: string, sectionId: string | null) => void;
  /** Jumps back to the flow view with the course selected. */
  onOpenCourse: (code: string) => void;
}

// ===== v5 (F2b, ADR-030): pinned courses — ephemeral in the request, remembered per
// (plan, term) in localStorage (same pattern as fluxo.theme). Server holds no state.
const MAX_PINNED = 6;

const pinnedStorageKey = (planId: string, term: string) => `fluxo.pinned.${planId}.${term}`;

/** null = key never stored (lets the campaign seed run); [] = user cleared all pins. */
function readStoredPins(planId: string, term: string): string[] | null {
  try {
    const raw = window.localStorage.getItem(pinnedStorageKey(planId, term));
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((x): x is string => typeof x === "string").slice(0, MAX_PINNED);
  } catch {
    // Storage unavailable or corrupted JSON — behave as "never stored".
    return null;
  }
}

function writeStoredPins(planId: string, term: string, codes: string[]): void {
  try {
    window.localStorage.setItem(pinnedStorageKey(planId, term), JSON.stringify(codes));
  } catch {
    // Best-effort persistence; pins still work for the session.
  }
}

// v5 F1c (ADR-028): pending elective turma choices per (plan, term). They live in
// the front until "selecionar semestre" commits them (nothing persists on the
// server before the commit — "nada aplicado às cegas").
const pendingElectivesKey = (planId: string, term: string) => `fluxo.pending-electives.${planId}.${term}`;

function readPendingElectives(planId: string, term: string): Map<string, string> {
  try {
    const raw = window.localStorage.getItem(pendingElectivesKey(planId, term));
    if (!raw) return new Map();
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return new Map();
    return new Map(
      Object.entries(parsed as Record<string, unknown>).filter(
        (e): e is [string, string] => typeof e[1] === "string",
      ),
    );
  } catch {
    return new Map();
  }
}

function writePendingElectives(planId: string, term: string, map: Map<string, string>): void {
  try {
    window.localStorage.setItem(pendingElectivesKey(planId, term), JSON.stringify(Object.fromEntries(map)));
  } catch {
    // best-effort
  }
}

export function SchedulePage(props: SchedulePageProps) {
  const { plan, courses, courseByCode } = props;

  // Target term is always derived from the plan (ADR-011/F4); the selector only
  // lets the user inspect future terms whose offer was imported manually.
  const targetTerm = nextTerm(plan.current_term);
  const [term, setTerm] = useState(targetTerm);
  useEffect(() => {
    setTerm(nextTerm(plan.current_term));
  }, [plan.id, plan.current_term]);

  const termOptions = useMemo(() => {
    const set = new Set<string>([targetTerm, term]);
    for (const t of plan.timeline?.terms ?? []) {
      if (compareTerms(t, targetTerm) >= 0) set.add(t);
    }
    return [...set].sort(compareTerms);
  }, [plan.timeline, targetTerm, term]);

  const sectionsQ = useSections(term);
  const check = useScheduleCheck(plan.id);

  // v4 (A2, ADR-021): server-computed eligibility for the selected term.
  // Degrades gracefully: while loading / on error the list renders without badges.
  const eligibilityQ = useCourseEligibility(plan.id, term);
  const eligibilityByCode = useMemo(() => {
    const m = new Map<string, CourseEligibility>();
    for (const e of eligibilityQ.data?.courses ?? []) m.set(e.code, e);
    return m;
  }, [eligibilityQ.data]);

  // v4 (C1, spec 5.3): accepted sections of the term's OPEN campaign become locked_choices
  // of the existing recommend endpoint — the engine keeps them in every suggestion.
  const campaignsQ = useCampaigns(plan.id);
  const openCampaignId = useMemo(
    () =>
      (campaignsQ.data?.campaigns ?? []).find((c) => c.term === term && c.status === "aberta")?.id,
    [campaignsQ.data, term],
  );
  const openCampaignQ = useCampaign(plan.id, openCampaignId);
  const lockedChoices = openCampaignQ.data?.accepted_section_ids ?? [];

  // v5 (F2b, ADR-030): pinned courses per (plan, term). Switching plan or term reloads
  // from storage — pins never leak across scopes.
  const [pinned, setPinned] = useState<Set<string>>(() => new Set(readStoredPins(plan.id, term) ?? []));
  useEffect(() => {
    setPinned(new Set(readStoredPins(plan.id, term) ?? []));
  }, [plan.id, term]);

  // Optional seed (ADR-030): when nothing was EVER stored for (plan, term), start from the
  // open campaign's "desejada"/"pedida" add-requests. Never overwrites a stored choice.
  useEffect(() => {
    if (readStoredPins(plan.id, term) !== null) return;
    const codes = (openCampaignQ.data?.requests ?? [])
      .filter((r) => r.kind === "add" && (r.status === "desejada" || r.status === "pedida"))
      .map((r) => r.course_code);
    if (codes.length > 0) setPinned(new Set([...new Set(codes)].slice(0, MAX_PINNED)));
  }, [plan.id, term, openCampaignQ.data]);

  const togglePin = (code: string) => {
    setPinned((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else if (next.size < MAX_PINNED) next.add(code);
      else return prev; // limit reached — button is disabled, this is just a guard
      writeStoredPins(plan.id, term, [...next]);
      return next;
    });
  };

  // v5 F1c: pending elective turma choices per (plan, term); commit materializes them.
  const [pendingElectives, setPendingElectives] = useState<Map<string, string>>(
    () => readPendingElectives(plan.id, term),
  );
  useEffect(() => {
    setPendingElectives(readPendingElectives(plan.id, term));
  }, [plan.id, term]);

  const pickElectiveSection = (code: string, sectionId: string | null) => {
    setPendingElectives((prev) => {
      const next = new Map(prev);
      if (sectionId) next.set(code, sectionId);
      else next.delete(code);
      writePendingElectives(plan.id, term, next);
      return next;
    });
  };

  const commitTerm = useCommitTerm(plan.id, plan.grade_version_id);

  // v5 F1c (ADR-028): the elective catalog is merged into the OfferList (same
  // style as the missing grade courses, "optativa" chip). Empty until a scrape
  // populates it — the list still renders the grade courses on its own.
  const electivesQ = useElectives(plan.id, term);
  const electives = useMemo(() => electivesQ.data?.electives ?? [], [electivesQ.data]);

  // Adopted (or in-seed) electives already live in the grade and show up via
  // `missing` — keep only NOT-yet-materialized ones in the catalog section to
  // avoid duplicate rows.
  const catalogElectives = useMemo(
    () => electives.filter((e) => !e.adopted && !courseByCode.has(e.code)),
    [electives, courseByCode],
  );
  const adoptedElectives = useMemo(
    () => new Set(electives.filter((e) => e.adopted).map((e) => e.code)),
    [electives],
  );

  const dropElective = useDropElective(plan.id, plan.grade_version_id);
  const [dropError, setDropError] = useState<string | null>(null);
  const doDropElective = (code: string) => {
    setDropError(null);
    dropElective.mutate(code, {
      onError: (e) =>
        setDropError(
          e instanceof ApiError && e.status === 409
            ? `${code}: desaloque a cadeira no fluxograma antes de remover.`
            : `Falha ao remover ${code}.`,
        ),
    });
  };

  const sections = useMemo(() => sectionsQ.data?.sections ?? [], [sectionsQ.data]);
  const sectionById = useMemo(() => new Map(sections.map((s) => [s.id, s])), [sections]);
  const sectionsByCourse = useMemo(() => {
    const m = new Map<string, Section[]>();
    for (const s of sections) {
      const list = m.get(s.course_code) ?? [];
      list.push(s);
      m.set(s.course_code, list);
    }
    return m;
  }, [sections]);

  const chosen = useMemo(
    () => new Map(plan.items.filter((i) => i.term === term).map((i) => [i.course_code, i.section_id ?? null])),
    [plan.items, term],
  );

  const missing = useMemo(() => {
    const list = courses.filter(
      (c) => c.status === "falta" && (c.kind === "obrigatoria" || c.kind === "optativa"),
    );
    // Blocked = server says so (A2); unknown (loading/error) counts as not blocked.
    const blockedRank = (c: CurriculumCourse) => {
      const e = eligibilityByCode.get(c.code);
      return e && !e.eligible ? 1 : 0;
    };
    return list.sort((a, b) => {
      const offA = (sectionsByCourse.get(a.code) ?? []).length > 0 ? 0 : 1;
      const offB = (sectionsByCourse.get(b.code) ?? []).length > 0 ? 0 : 1;
      if (offA !== offB) return offA - offB;
      // v4 (A2): blocked courses sink to the end of the "with offer" group.
      const blkA = blockedRank(a);
      const blkB = blockedRank(b);
      if (blkA !== blkB) return blkA - blkB;
      const idxA = a.suggested_term_index ?? 99;
      const idxB = b.suggested_term_index ?? 99;
      if (idxA !== idxB) return idxA - idxB;
      return a.code.localeCompare(b.code);
    });
  }, [courses, sectionsByCourse, eligibilityByCode]);

  // v5 (F2b): only pins of courses still listed as missing are sent — a pin left behind
  // after the course was approved/allocated stays inert in storage instead of erroring.
  const pinnedCourses = useMemo(() => {
    const missingCodes = new Set(missing.map((c) => c.code));
    return [...pinned].filter((code) => missingCodes.has(code)).sort();
  }, [pinned, missing]);

  // The front never decides validity: conflicts come from server diagnostics only
  // (plan revalidation + explicit schedule-check), merged and deduped by id.
  const conflictDiags = useMemo(() => {
    const m = new Map<string, Diagnostic>();
    for (const d of plan.diagnostics) if (d.type === "SCHEDULE_CONFLICT") m.set(d.id, d);
    for (const d of check.data?.diagnostics ?? []) if (d.type === "SCHEDULE_CONFLICT") m.set(d.id, d);
    return [...m.values()].filter((d) => d.term === null || d.term === term);
  }, [plan.diagnostics, check.data, term]);

  const conflictIds = useMemo(() => {
    const ids = new Set<string>();
    for (const d of conflictDiags) {
      const a = d.details["section_a"];
      const b = d.details["section_b"];
      if (typeof a === "string") ids.add(a);
      if (typeof b === "string") ids.add(b);
    }
    return ids;
  }, [conflictDiags]);

  // item 4: a pending elective pick behaves like any course — it renders on the
  // week grid and is conflict-checked. Its section already lives in the term's
  // offer (materialized by the scrape), so it is in `sectionById`; fall back to
  // the /electives payload just in case.
  const electiveSectionById = useMemo(() => {
    const m = new Map<string, Section>();
    for (const e of electives) for (const s of e.sections) m.set(s.id, s);
    return m;
  }, [electives]);

  const pendingSectionIds = useMemo(() => [...pendingElectives.values()], [pendingElectives]);

  // A pending pick whose turma vanished from the offer (re-scrape) would be sent
  // to the commit and 400 the WHOLE semester — and, if the elective lost every
  // turma, its card is not even rendered, so the user could never clear it.
  // Keep those picks visible here and out of the commit payload.
  const stalePending = useMemo(
    () =>
      [...pendingElectives.entries()]
        .filter(([, sid]) => !sectionById.has(sid) && !electiveSectionById.has(sid))
        .map(([code]) => code),
    [pendingElectives, sectionById, electiveSectionById],
  );

  const blocks = useMemo(() => {
    const out: WeekGridBlock[] = [];
    const seen = new Set<string>();
    for (const [code, sid] of chosen) {
      if (!sid || seen.has(sid)) continue;
      const section = sectionById.get(sid);
      if (!section) continue; // stale choice — the server flags it via SECTION_STALE
      seen.add(sid);
      out.push({ section, course: courseByCode.get(code), conflicted: conflictIds.has(sid) });
    }
    // pending electives — same visual + conflict flag; course is undefined (not
    // in the grade yet) so the grid labels it by code.
    for (const sid of pendingSectionIds) {
      if (seen.has(sid)) continue;
      const section = sectionById.get(sid) ?? electiveSectionById.get(sid);
      if (!section) continue;
      seen.add(sid);
      out.push({ section, course: courseByCode.get(section.course_code), conflicted: conflictIds.has(sid) });
    }
    return out.sort((a, b) => a.section.course_code.localeCompare(b.section.course_code));
  }, [chosen, sectionById, electiveSectionById, pendingSectionIds, courseByCode, conflictIds]);

  const staleChoices = useMemo(
    () => [...chosen.entries()].filter(([, sid]) => sid !== null && !sectionById.has(sid)).map(([code]) => code),
    [chosen, sectionById],
  );

  const hasData = sectionsQ.data?.has_data ?? false;

  function applyRecommendation(choices: { course_code: string; section_id: string }[]) {
    for (const c of choices) props.onSetSection(c.course_code, term, c.section_id);
  }

  // v5 F1c: "selecionar semestre" — reaffirm the term. Grade courses already
  // allocated with a chosen turma (idempotent upsert) + pending electives from
  // the catalog (materialized into the flowchart). Clears pending on success.
  const gradeChoices = [...chosen].filter(([, sid]) => sid).length;
  const electiveChoices = pendingElectives.size - stalePending.length;

  const clearPendingElective = (code: string) => pickElectiveSection(code, null);

  function doCommitTerm() {
    const choices: { course_code: string; section_id: string | null }[] = [];
    for (const [code, sid] of chosen) if (sid) choices.push({ course_code: code, section_id: sid });
    for (const [code, sid] of pendingElectives) {
      if (stalePending.includes(code)) continue; // dead turma — never sent
      choices.push({ course_code: code, section_id: sid });
    }
    if (choices.length === 0) return;
    commitTerm.mutate(
      { term, choices },
      {
        onSuccess: () => {
          setPendingElectives(new Map());
          writePendingElectives(plan.id, term, new Map());
        },
      },
    );
  }

  return (
    <main className="schedule-page">
      <header className="sched-head">
        <div>
          <h2>Horários</h2>
          <p className="muted">
            Termo-alvo do plano: <strong>{targetTerm}</strong> (semestre seguinte ao "Estou em" {plan.current_term}).
          </p>
        </div>
        <div className="sched-head-controls">
          <label className="tb-label" htmlFor="sched-term">Semestre</label>
          <select id="sched-term" value={term} onChange={(e) => setTerm(e.target.value)}>
            {termOptions.map((t) => (
              <option key={t} value={t}>{t}{t === targetTerm ? " (alvo)" : ""}</option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-sm"
            disabled={check.isPending}
            title="Pede ao servidor a checagem de conflitos das turmas escolhidas"
            onClick={() => check.mutate(pendingSectionIds)}
          >
            {check.isPending ? "Verificando…" : "Verificar horários"}
          </button>
        </div>
      </header>

      <ScrapeButton planId={plan.id} targetTerm={targetTerm} />

      {check.isError && (
        <div className="sched-card sp-checkresult" role="alert">
          <p className="sp-diag sev-error">Falha ao verificar horários. Tente novamente.</p>
        </div>
      )}
      {check.data && (
        <div className="sched-card sp-checkresult">
          {check.data.diagnostics.length === 0 ? (
            <p className="sp-ok">✓ Sem conflitos de horário nas turmas escolhidas.</p>
          ) : (
            check.data.diagnostics.map((d) => (
              <p key={d.id} className={`sp-diag sev-${d.severity}`}>{d.message}</p>
            ))
          )}
          {check.data.items_without_section.length > 0 && (
            <p className="muted">Sem turma escolhida: {check.data.items_without_section.join(", ")}</p>
          )}
          {check.data.terms_without_data.length > 0 && (
            <p className="muted">Termos sem dados de oferta: {check.data.terms_without_data.join(", ")}</p>
          )}
        </div>
      )}

      {sectionsQ.isLoading && (
        <div className="panel-empty">
          <span className="spinner" aria-label="Carregando turmas" />
          <p className="muted">Carregando turmas de {term}…</p>
        </div>
      )}

      {sectionsQ.isError && (
        <div className="sched-card" role="alert">
          <p className="sp-diag sev-error">Falha ao carregar as turmas de {term}.</p>
          <button type="button" className="btn btn-sm" onClick={() => void sectionsQ.refetch()}>Tentar de novo</button>
        </div>
      )}

      {sectionsQ.data && !hasData && (
        <div className="panel-empty">
          <span className="empty-icon" aria-hidden>▦</span>
          <p>Nenhuma turma cadastrada para {term}.</p>
          <p className="muted">
            Use "Buscar ofertas {targetTerm}" acima, ou importe a oferta manualmente
            (PUT /api/v1/admin/terms/{term.replace("/", "-")}/sections).
          </p>
        </div>
      )}

      {sectionsQ.data && hasData && (
        <div className="sched-cols">
          <section className="sched-grid-col sched-card" aria-label="Grade semanal">
            <h3>Grade semanal · {term}</h3>
            {staleChoices.length > 0 && (
              <p className="sp-diag sev-warning">
                Turma salva não existe mais na oferta: {staleChoices.join(", ")} — veja os avisos (SECTION_STALE).
              </p>
            )}
            <WeekGrid term={term} blocks={blocks} onOpenCourse={props.onOpenCourse} />
          </section>
          <section className="sched-side-col" aria-label="Turmas e recomendação">
            <RecommendationCard
              planId={plan.id}
              term={term}
              courseByCode={courseByCode}
              lockedChoices={lockedChoices}
              pinnedCourses={pinnedCourses}
              applying={props.savingSection}
              onApply={applyRecommendation}
              onOpenCourse={props.onOpenCourse}
            />
            <OfferList
              term={term}
              missing={missing}
              sectionsByCourse={sectionsByCourse}
              chosen={chosen}
              conflictIds={conflictIds}
              eligibilityByCode={eligibilityByCode}
              eligibilityUnavailable={eligibilityQ.isError}
              pinned={pinned}
              pinLimitReached={pinned.size >= MAX_PINNED}
              onTogglePin={togglePin}
              saving={props.savingSection}
              onSetSection={(code, sectionId) => props.onSetSection(code, term, sectionId)}
              onOpenCourse={props.onOpenCourse}
              catalogElectives={catalogElectives}
              pendingElectives={pendingElectives}
              onPickElectiveSection={pickElectiveSection}
              adoptedElectives={adoptedElectives}
              droppingElective={dropElective.isPending}
              onDropElective={doDropElective}
              dropElectiveError={dropError}
            />
            <div className="sched-card sp-commit">
              <h3>Selecionar semestre · {term}</h3>
              <p className="muted">
                Fecha este semestre: as optativas escolhidas viram caixinha no fluxograma e passam a
                contar nas horas. As obrigatórias já escolhidas são reafirmadas.
              </p>
              {stalePending.length > 0 && (
                <p className="sp-diag sev-warning">
                  Escolha pendente de turma que sumiu da oferta ({stalePending.join(", ")}) — não
                  entra na seleção.{" "}
                  {stalePending.map((code) => (
                    <button
                      key={code}
                      type="button"
                      className="btn btn-sm"
                      onClick={() => clearPendingElective(code)}
                    >
                      limpar {code}
                    </button>
                  ))}
                </p>
              )}
              <button
                type="button"
                className="btn"
                disabled={commitTerm.isPending || (gradeChoices === 0 && electiveChoices === 0)}
                onClick={doCommitTerm}
              >
                {commitTerm.isPending
                  ? "Aplicando…"
                  : `Selecionar semestre ${term}` +
                    (electiveChoices > 0 ? ` (+${electiveChoices} optativa${electiveChoices > 1 ? "s" : ""})` : "")}
              </button>
              {commitTerm.isError && (
                <p className="sp-diag sev-error">Falha ao selecionar o semestre. Tente novamente.</p>
              )}
              {commitTerm.data && commitTerm.data.committed.adopted.length > 0 && (
                <p className="sp-ok">
                  ✓ Optativa(s) trazida(s) ao fluxograma: {commitTerm.data.committed.adopted.join(", ")}.
                </p>
              )}
              {/* item 5: the commit pre-fills the rematrícula round. The server
                  decides what goes in — the front only reports its answer. */}
              {commitTerm.data && commitTerm.data.campaign?.created.length ? (
                <p className="sp-ok">
                  ✓ {commitTerm.data.campaign.created.length} pedido(s) de rematrícula criado(s) na
                  aba Matrícula ({commitTerm.data.campaign.created.join(", ")}).
                </p>
              ) : null}
              {commitTerm.data && commitTerm.data.campaign?.special_needed.length ? (
                <p className="sp-diag sev-warning">
                  Turma(s) de outro curso ({commitTerm.data.campaign.special_needed.join(", ")}) não
                  entram na rematrícula — peça na rodada de matrícula especial, na aba Matrícula.
                </p>
              ) : null}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
