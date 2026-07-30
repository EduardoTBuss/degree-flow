import type { CourseEligibility, CurriculumCourse, Elective, Section } from "../../api/types";
import { freshness, slotText, sourceLabel } from "./format";

export interface OfferListProps {
  term: string;
  /** Courses still missing (status "falta"), pre-sorted by the page (offer > blocked > term). */
  missing: CurriculumCourse[];
  sectionsByCourse: Map<string, Section[]>;
  /** course_code -> chosen section_id (null = allocated without section). */
  chosen: Map<string, string | null>;
  /** Section ids in server-reported SCHEDULE_CONFLICT diagnostics. */
  conflictIds: Set<string>;
  /** v4 (A2, ADR-021): server-computed eligibility; missing entry = no badge. */
  eligibilityByCode: Map<string, CourseEligibility>;
  /** v4 (A2): the eligibility request failed — badges may be missing. */
  eligibilityUnavailable?: boolean;
  /** v5 (F2b, ADR-029/030): course_codes pinned for the recommendation. */
  pinned: Set<string>;
  /** v5 (F2b): max pins reached — new pins disabled (server rejects > 6). */
  pinLimitReached: boolean;
  onTogglePin: (code: string) => void;
  saving: boolean;
  onSetSection: (code: string, sectionId: string | null) => void;
  onOpenCourse: (code: string) => void;
  // ===== v5 F1c (ADR-028): catalog electives merged into the same list =====
  /** Catalog electives NOT yet in the grade (adopted/in-seed excluded by the page). */
  catalogElectives: Elective[];
  /** elective code -> pending section id (front-only until "selecionar semestre"). */
  pendingElectives: Map<string, string>;
  /** Pending pick — NEVER onSetSection: the course is not in the grade yet (would 404). */
  onPickElectiveSection: (code: string, sectionId: string | null) => void;
  /** Grade courses that are adopted electives — droppable ("não quero mais"). */
  adoptedElectives: Set<string>;
  droppingElective: boolean;
  onDropElective: (code: string) => void;
  dropElectiveError: string | null;
}

// Unified list: grade courses and catalog electives render in the SAME list,
// same turma style (shared <SectionRow>). The row's ORIGIN decides the choose
// callback — grade rows allocate immediately (onSetSection creates the
// plan_item), catalog elective rows only stage a PENDING pick that
// "Selecionar semestre" materializes (F1b/v5: no flowchart node before commit).

/** Tooltip for the "bloqueada" badge, e.g. "faltam: Cálculo 2 (falta), Física 1 (falta)". */
function blockedTitle(e: CourseEligibility): string {
  if (e.missing_prereqs.length === 0) return "bloqueada por pré-requisito";
  return `faltam: ${e.missing_prereqs.map((p) => `${p.name} (${p.status})`).join(", ")}`;
}

/** One turma line — identical visuals for grade and elective rows; only the
 *  choose semantics differ (labels/titles/handler come from the caller). The
 *  "optativa" flag sits in the SAME slot/style as the "outro curso" badge. */
function SectionRow(props: {
  section: Section;
  isElective: boolean;
  isChosen: boolean;
  inConflict: boolean;
  disabled: boolean;
  chooseLabel: string;
  chosenLabel: string;
  chooseTitle: string;
  unchooseTitle: string;
  onToggle: () => void;
}) {
  const { section: s, isElective, isChosen, inConflict } = props;
  const vac = s.capacity != null && s.enrolled != null ? s.capacity - s.enrolled : null;
  const fresh = freshness(s);
  return (
    <div className={`sp-turma ${isChosen ? "is-chosen" : ""} ${inConflict ? "has-conflict" : ""}`}>
      <div className="sp-turma-main">
        <span className="sp-turma-label">
          {s.label ?? s.id}
          {inConflict && <span className="chip chip-danger">conflito</span>}
        </span>
        <span className="sp-turma-time">{slotText(s)}</span>
      </div>
      {/* Item 2: "optativa" flag in the very same position/style as "outro curso". */}
      {isElective && (
        <span className="chip chip-optativa" title="Optativa da matriz do curso">optativa</span>
      )}
      {s.foreign_offer ? (
        // A3 (ADR-020): structured badge; the human note is redundant here.
        <span
          className="chip chip-foreign"
          title={`Candidata a matrícula especial — ofertada para: ${(s.offered_to ?? []).join(", ") || "outro curso"}`}
        >
          outro curso
        </span>
      ) : (
        // Fallback for old data: only the scraped note exists (offered_to = null).
        s.note && <span className="chip offer-note" title={s.note}>{s.note}</span>
      )}
      <div className="sp-turma-meta">
        {s.professor && <span className="muted">{s.professor}</span>}
        {vac != null ? (
          <span className={vac > 0 ? "sp-vaga" : "sp-novaga"}>{vac > 0 ? `${vac} vagas` : "sem vaga"}</span>
        ) : (
          <span className="muted" title="O portal não informou vagas para esta turma">vagas: ?</span>
        )}
        <span className="offer-fresh" title={`Origem: ${s.source}`}>
          {sourceLabel(s.source)}{fresh ? ` · ${fresh}` : ""}
        </span>
        <button
          type="button"
          className={`btn btn-sm ${isChosen ? "btn-primary" : ""}`}
          disabled={props.disabled}
          title={isChosen ? props.unchooseTitle : props.chooseTitle}
          onClick={props.onToggle}
        >
          {isChosen ? props.chosenLabel : props.chooseLabel}
        </button>
      </div>
    </div>
  );
}

export function OfferList(props: OfferListProps) {
  const {
    term, missing, sectionsByCourse, chosen, conflictIds, eligibilityByCode, pinned, saving,
    catalogElectives, pendingElectives, adoptedElectives,
  } = props;
  const offered = missing.filter((c) => (sectionsByCourse.get(c.code) ?? []).length > 0);
  const unoffered = missing.filter((c) => (sectionsByCourse.get(c.code) ?? []).length === 0);
  // Only electives actually offered in the term join the list; unoffered
  // electives are simply not shown (item 3: no "sem oferta conhecida" footer).
  const electivesOffered = catalogElectives.filter((e) => e.sections.length > 0);

  return (
    <div className="sched-card">
      <h3>Cadeiras faltantes e optativas · {term}</h3>
      {missing.length === 0 && electivesOffered.length === 0 && (
        <p className="muted">Nenhuma cadeira com status "falta" — nada para alocar neste termo.</p>
      )}

      {offered.length === 0 && missing.length > 0 && (
        <p className="muted">
          Nenhuma das cadeiras faltantes tem turma cadastrada em {term}. Use "Buscar ofertas" acima ou o import manual.
        </p>
      )}

      {props.eligibilityUnavailable && (
        <p className="muted">Não foi possível consultar os pré-requisitos deste termo — selos "bloqueada" podem faltar.</p>
      )}

      {props.dropElectiveError && <p className="sp-diag sev-warning">{props.dropElectiveError}</p>}

      <div className="sp-sections">
        {/* ===== Grade courses (obrigatórias + optativas já no fluxograma) ===== */}
        {offered.map((course) => {
          const list = sectionsByCourse.get(course.code) ?? [];
          const chosenId = chosen.get(course.code) ?? null;
          const elig = eligibilityByCode.get(course.code);
          const blocked = elig != null && !elig.eligible;
          const isPinned = pinned.has(course.code);
          const pinDisabled = !isPinned && props.pinLimitReached;
          const droppable = adoptedElectives.has(course.code);
          const isElective = course.kind === "optativa";
          return (
            <div key={course.code} className={`sp-course ${blocked ? "is-blocked" : ""}`}>
              <div className="sp-course-head">
                <button
                  type="button"
                  className="sp-course-name"
                  title="Ver no fluxograma"
                  onClick={() => props.onOpenCourse(course.code)}
                >
                  {course.short ?? course.code}
                  <span className="muted"> {course.name}</span>
                </button>
                {droppable && (
                  <span className="chip chip-pin" title="Optativa já materializada no fluxograma">no fluxograma</span>
                )}
                {/* v5 (F2b): pin the COURSE — the engine picks the section (ADR-029).
                    Pin only exists for grade courses (the recommender ranks the grade). */}
                <button
                  type="button"
                  className={`sp-pin ${isPinned ? "is-pinned" : ""}`}
                  aria-pressed={isPinned}
                  disabled={pinDisabled}
                  title={
                    pinDisabled
                      ? "Máximo de 6 cadeiras fixadas"
                      : isPinned
                        ? `Desafixar ${course.name} da recomendação`
                        : `Fixar ${course.name} na recomendação (o motor escolhe a turma)`
                  }
                  aria-label={
                    isPinned
                      ? `Desafixar ${course.name} da recomendação`
                      : `Fixar ${course.name} na recomendação`
                  }
                  onClick={() => props.onTogglePin(course.code)}
                >
                  <span aria-hidden>📌</span>
                </button>
                {isPinned && <span className="chip chip-pin">fixada</span>}
                {blocked && (
                  // A2 (D6): badge only — choosing stays enabled; the server answers
                  // with PREREQ_VIOLATION on PUT /items (authorized break).
                  <span className="chip chip-blocked" title={blockedTitle(elig)} aria-label={`Bloqueada por pré-requisito. ${blockedTitle(elig)}`}>
                    bloqueada
                  </span>
                )}
                {droppable && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={props.droppingElective}
                    title="Remover a optativa do fluxograma (só se não estiver alocada)"
                    aria-label={`Remover a optativa ${course.name} do fluxograma`}
                    onClick={() => props.onDropElective(course.code)}
                  >
                    Não quero mais
                  </button>
                )}
              </div>
              <div className="sp-turmas">
                {list.map((s) => {
                  const isChosen = chosenId === s.id;
                  return (
                    <SectionRow
                      key={s.id}
                      section={s}
                      isElective={isElective}
                      isChosen={isChosen}
                      inConflict={isChosen && conflictIds.has(s.id)}
                      disabled={saving}
                      chooseLabel="escolher"
                      chosenLabel="escolhida ✕"
                      chooseTitle={`Aloca ${course.code} em ${term} com esta turma`}
                      unchooseTitle="Remove a escolha desta turma"
                      onToggle={() => props.onSetSection(course.code, isChosen ? null : s.id)}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}

        {/* ===== Catalog electives with an offer this term (F1c, ADR-028) =====
            Choosing a turma here only STAGES the pick (localStorage); nothing is
            created on the server until "Selecionar semestre". The pending pick
            still shows on the WeekGrid and is conflict-checked (item 4). */}
        {electivesOffered.map((e) => {
          const pendingId = pendingElectives.get(e.code) ?? null;
          const pendingStale = pendingId !== null && !e.sections.some((s) => s.id === pendingId);
          return (
            <div key={e.code} className="sp-course">
              <div className="sp-course-head">
                {/* Not in the flowchart yet — no jump button, plain heading. */}
                <div className="sp-course-name" title={`${e.code} · ${e.credits}cr · ${e.hours}h`}>
                  <span className="mono">{e.code}</span>
                  <span className="muted"> {e.name}</span>
                </div>
                {pendingId && !pendingStale && (
                  <span
                    className="chip chip-pin"
                    title='Turma escolhida — será trazida ao fluxograma em "Selecionar semestre"'
                  >
                    pendente
                  </span>
                )}
              </div>
              {pendingStale && (
                <p className="sp-diag sev-warning">
                  A turma escolhida não está mais na oferta.{" "}
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => props.onPickElectiveSection(e.code, null)}
                  >
                    limpar escolha
                  </button>
                </p>
              )}
              <div className="sp-turmas">
                {e.sections.map((s) => {
                  const isChosen = pendingId === s.id;
                  return (
                    <SectionRow
                      key={s.id}
                      section={s}
                      isElective
                      isChosen={isChosen}
                      inConflict={isChosen && conflictIds.has(s.id)}
                      disabled={false}
                      chooseLabel="escolher"
                      chosenLabel="pendente ✕"
                      chooseTitle={`Escolhe esta turma de ${e.code} — vira caixinha no fluxograma só ao "Selecionar semestre"`}
                      unchooseTitle="Desfaz a escolha pendente desta turma"
                      onToggle={() => props.onPickElectiveSection(e.code, isChosen ? null : s.id)}
                    />
                  );
                })}
              </div>
              {pendingId && !pendingStale && (
                <p className="muted">Pendente — será materializada ao clicar em "Selecionar semestre {term}".</p>
              )}
            </div>
          );
        })}
      </div>

      {unoffered.length > 0 && (
        <div className="offer-none-wrap">
          <p className="muted">Sem oferta conhecida em {term}:</p>
          <div className="offer-none">
            {unoffered.map((c) => {
              const isPinned = pinned.has(c.code);
              return (
                <span key={c.code} className="offer-none-item">
                  <button
                    type="button"
                    className="chip chip-link"
                    title={`${c.name} — ver no fluxograma`}
                    onClick={() => props.onOpenCourse(c.code)}
                  >
                    {isPinned && <span aria-hidden>📌 </span>}
                    {c.short ?? c.code}
                  </button>
                  {/* Unpin is still possible when a previously pinned course lost its offer
                      (the server would answer "sem_oferta"); pinning here is allowed too —
                      the server explains, the front never decides (principle 3). */}
                  <button
                    type="button"
                    className={`sp-pin sp-pin-sm ${isPinned ? "is-pinned" : ""}`}
                    aria-pressed={isPinned}
                    disabled={!isPinned && props.pinLimitReached}
                    title={
                      !isPinned && props.pinLimitReached
                        ? "Máximo de 6 cadeiras fixadas"
                        : isPinned
                          ? `Desafixar ${c.name} da recomendação`
                          : `Fixar ${c.name} na recomendação (sem oferta conhecida — a recomendação avisará)`
                    }
                    aria-label={
                      isPinned
                        ? `Desafixar ${c.name} da recomendação`
                        : `Fixar ${c.name} na recomendação`
                    }
                    onClick={() => props.onTogglePin(c.code)}
                  >
                    <span aria-hidden>📌</span>
                  </button>
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
