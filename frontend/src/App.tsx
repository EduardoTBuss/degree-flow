import { useEffect, useMemo, useState } from "react";
import type { AutoplanResult, Diagnostic } from "./api/types";
import { buildGraph } from "./lib/graph";
import { FlowBoard, type DropTarget, type FocusToken } from "./components/FlowBoard/FlowBoard";
import { CoursePanel } from "./components/CoursePanel/CoursePanel";
import { DiagnosticsPanel } from "./components/DiagnosticsPanel/DiagnosticsPanel";
import { RequirementsPanel } from "./components/RequirementsPanel/RequirementsPanel";
import { SchedulePage } from "./components/SchedulePage/SchedulePage";
import { CampaignPage } from "./components/CampaignPage/CampaignPage";
import { PlanToolbar, type TopView } from "./components/PlanToolbar/PlanToolbar";
import { ImportReview } from "./components/ImportReview/ImportReview";
import {
  useAutoplan,
  useCourseStatePatch,
  useCurriculum,
  useGradeVersions,
  useLockToggle,
  useMoveCourse,
  usePlan,
  usePlanCreate,
  usePlanDelete,
  usePlanPatch,
  usePlans,
  useRequirementMutations,
  useRequirements,
  useSetSection,
} from "./state/queries";

type SideTab = "diagnostics" | "course" | "requirements";

export function App() {
  const gradeVersionsQ = useGradeVersions();
  const plansQ = usePlans();

  const [planId, setPlanId] = useState<string | undefined>();
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [showCriticalPath, setShowCriticalPath] = useState(true);
  const [preview, setPreview] = useState<AutoplanResult | null>(null);
  const [highlight, setHighlight] = useState<{ codes: string[]; related: string[] } | null>(null);
  const [focusToken, setFocusToken] = useState<FocusToken | null>(null);
  const [activeDiagnosticId, setActiveDiagnosticId] = useState<string | null>(null);
  const [tab, setTab] = useState<SideTab>("diagnostics");
  const [view, setView] = useState<TopView>("flow"); // top-level nav (ADR-015/023, no router)
  const [showImport, setShowImport] = useState(false);

  useEffect(() => {
    if (!planId && plansQ.data && plansQ.data.length > 0) setPlanId(plansQ.data[0].id);
  }, [planId, plansQ.data]);

  const planQ = usePlan(planId);
  const plan = planQ.data;
  const gvId = plan?.grade_version_id;
  const curriculumQ = useCurriculum(gvId);
  const requirementsQ = useRequirements(planId);

  const moveCourse = useMoveCourse(planId);
  const lockToggle = useLockToggle(planId);
  const statePatch = useCourseStatePatch(planId, gvId);
  const setSection = useSetSection(planId);
  const patchPlan = usePlanPatch(planId);
  const createPlan = usePlanCreate();
  const deletePlan = usePlanDelete();
  const autoplan = useAutoplan(planId);
  const reqMut = useRequirementMutations();

  const courses = curriculumQ.data?.courses ?? [];
  const byCode = useMemo(() => new Map(courses.map((c) => [c.code, c])), [courses]);
  const graph = useMemo(() => buildGraph(courses), [courses]);

  const diagnostics: Diagnostic[] = preview ? preview.diagnostics : plan?.diagnostics ?? [];
  const criticalPath = preview ? preview.critical_path : plan?.critical_path ?? null;
  const blockedCodes = useMemo(() => new Set(plan?.blocked_codes ?? []), [plan?.blocked_codes]);

  function focusDiagnostic(d: Diagnostic) {
    setHighlight({ codes: d.course_codes, related: d.related_codes });
    setActiveDiagnosticId(d.id);
    const codes = [...d.course_codes, ...d.related_codes];
    if (codes.length > 0) setFocusToken({ nonce: Date.now(), codes });
    if (d.course_codes[0]) { setSelectedCode(d.course_codes[0]); setTab("course"); }
  }

  function selectCourse(code: string | null) {
    setSelectedCode(code);
    setHighlight(null);
    setActiveDiagnosticId(null);
    if (code) setTab("course");
  }

  function openCourseFromSchedule(code: string) {
    setView("flow");
    selectCourse(code);
    setFocusToken({ nonce: Date.now(), codes: [code] });
  }

  function runAutoplan(target: string, mode: "preview" | "apply") {
    autoplan.mutate(
      { target_term: target, mode, respect_existing: false },
      { onSuccess: (res) => { if (mode === "preview") setPreview(res); else setPreview(null); } },
    );
  }

  function applyPreview() {
    const target = plan?.target_term ?? undefined;
    if (!target) return;
    runAutoplan(target, "apply");
  }

  /**
   * Every card is draggable, so the drop target is what carries the intent.
   * The column the card lands on decides what happens:
   *
   *   past column   -> "I did it in that term"  : aprovada + completed_term
   *   "Concluídas"  -> "I did it, no idea when" : aprovada + completed_term = null
   *   term column   -> "I'll take it then"      : falta + allocated in that term
   *   "Não alocadas"-> "not planned yet"        : falta + no allocation
   *
   * Two things happen before the drop is resolved:
   *  - dragging a card while an autoplan preview is on materializes the preview
   *    (what you see becomes the real plan) and then applies your adjustment;
   *  - dragging a locked (🔒) card unlocks it — the drag *is* the intent to move.
   */
  async function handleDropCourse(code: string, target: DropTarget) {
    if (!plan) return;
    const course = byCode.get(code);
    if (!course) return;

    if (preview) {
      const targetTerm = plan.target_term;
      if (targetTerm) {
        try {
          await autoplan.mutateAsync({ target_term: targetTerm, mode: "apply", respect_existing: false });
        } catch {
          /* preview couldn't be applied; the drop below is still worth doing */
        }
      }
      setPreview(null);
    }

    const item = plan.items.find((i) => i.course_code === code);
    if (item?.locked) {
      try {
        await lockToggle.mutateAsync({ code, lock: false });
      } catch {
        return; // couldn't unlock -> don't half-apply the move
      }
    }

    const wasCompleted = course.status === "aprovada" || course.status === "cursando";

    if (target.kind === "past" || target.kind === "done") {
      // "Já fiz" também significa "não está mais planejada": sem isto o plan_item
      // antigo sobrevive invisível (a coluna passa a ser decidida pelo status) e
      // segue contando créditos/diagnósticos no semestre futuro em que estava.
      if (item) {
        try {
          await moveCourse.mutateAsync({ code, term: null });
        } catch {
          return; // não conseguiu desalocar -> não aplica meio caminho
        }
      }
      await statePatch.mutateAsync({
        code,
        patch: { status: "aprovada", completed_term: target.kind === "past" ? target.term : null },
      });
      return;
    }
    // "term" (current/future) and "tray" both mean "not done yet".
    if (wasCompleted) {
      await statePatch.mutateAsync({ code, patch: { status: "falta", completed_term: null } });
    }
    await moveCourse.mutateAsync({ code, term: target.kind === "tray" ? null : target.term });
  }

  if (gradeVersionsQ.isError || plansQ.isError) {
    return <FullError message="Falha ao carregar dados iniciais. O backend está no ar em localhost:8000?" />;
  }
  if (!plan || !gvId || plansQ.data === undefined) return <FullLoading />;

  const selectedCourse = selectedCode ? byCode.get(selectedCode) ?? null : null;
  const selectedAlloc = selectedCode ? plan.items.find((i) => i.course_code === selectedCode) ?? null : null;
  const dependents = selectedCode ? graph.dependents.get(selectedCode) ?? [] : [];
  const errorCount = diagnostics.filter((d) => d.severity === "error").length;
  const warnCount = diagnostics.filter((d) => d.severity === "warning").length;

  return (
    <div className="app">
      <PlanToolbar
        plans={plansQ.data}
        plan={plan}
        gradeVersions={gradeVersionsQ.data ?? []}
        criticalPath={criticalPath}
        showCriticalPath={showCriticalPath}
        autoplanBusy={autoplan.isPending}
        hasPreview={preview !== null}
        view={view}
        onChangeView={setView}
        onSelectPlan={(id) => { setPlanId(id); setPreview(null); setSelectedCode(null); }}
        onCreatePlan={(name) => {
          createPlan.mutate(
            { name, grade_version_id: plan.grade_version_id, current_term: plan.current_term, start_term: plan.start_term ?? undefined },
            { onSuccess: (p) => setPlanId(p.id) },
          );
        }}
        onDeletePlan={(id) => {
          deletePlan.mutate(id, {
            onSuccess: () => { const rest = (plansQ.data ?? []).filter((p) => p.id !== id); setPlanId(rest[0]?.id); setPreview(null); },
          });
        }}
        onPatchPlan={(patch, opts) => { setPreview(null); patchPlan.mutate(patch, { onError: opts?.onError }); }}
        onToggleCriticalPath={setShowCriticalPath}
        onRunAutoplan={runAutoplan}
        onClearPreview={() => setPreview(null)}
        onApplyPreview={applyPreview}
        onOpenImport={() => setShowImport(true)}
      />

      {view === "flow" && preview && (
        <div className={`preview-banner ${preview.feasible ? "ok" : "bad"}`}>
          {preview.feasible
            ? "✅ Plano viável para o alvo — revise no grafo e clique em Aplicar (ou arraste uma cadeira para ajustar já aplicando)."
            : "⚠️ Alvo inviável com as restrições atuais — veja o gargalo nos avisos."}
        </div>
      )}

      {/* Flow view stays mounted (display:none) so React Flow keeps zoom/pan state. */}
      <div className="app-body" style={{ display: view === "flow" ? "flex" : "none" }}>
        <main className="board-area">
          {curriculumQ.isLoading ? (
            <FullLoading />
          ) : (
            <FlowBoard
              courses={courses}
              plan={plan}
              previewItems={preview ? preview.proposal : null}
              blockedCodes={blockedCodes}
              criticalPath={criticalPath?.course_codes ?? []}
              showCriticalPath={showCriticalPath}
              diagnostics={diagnostics}
              selectedCode={selectedCode}
              highlight={highlight}
              focusToken={focusToken}
              onSelectCourse={selectCourse}
              onDropCourse={(code, target) => { void handleDropCourse(code, target); }}
              onToggleDone={(code, done) => statePatch.mutate({ code, patch: { status: done ? "aprovada" : "falta" } })}
              onToggleLock={(code, lock) => lockToggle.mutate({ code, lock })}
            />
          )}
        </main>

        <aside className="sidebar">
          <nav className="side-tabs" role="tablist">
            <button role="tab" aria-selected={tab === "diagnostics"} className={tab === "diagnostics" ? "on" : ""} onClick={() => setTab("diagnostics")}>
              Avisos
              {errorCount > 0 && <span className="badge badge-error">{errorCount}</span>}
              {errorCount === 0 && warnCount > 0 && <span className="badge badge-warn">{warnCount}</span>}
            </button>
            <button role="tab" aria-selected={tab === "course"} className={tab === "course" ? "on" : ""} onClick={() => setTab("course")}>Cadeira</button>
            <button role="tab" aria-selected={tab === "requirements"} className={tab === "requirements" ? "on" : ""} onClick={() => setTab("requirements")}>Requisitos</button>
          </nav>

          <div className="side-content">
            {tab === "diagnostics" && (
              <DiagnosticsPanel
                diagnostics={diagnostics}
                valid={errorCount === 0}
                courseByCode={byCode}
                activeDiagnosticId={activeDiagnosticId}
                onFocusDiagnostic={focusDiagnostic}
              />
            )}
            {tab === "course" && (
              <CoursePanel
                course={selectedCourse}
                alloc={selectedAlloc}
                blocked={selectedCode ? blockedCodes.has(selectedCode) : false}
                diagnostics={diagnostics}
                courseByCode={byCode}
                dependents={dependents}
                saving={statePatch.isPending || lockToggle.isPending}
                onPatch={(code, patch) => statePatch.mutate({ code, patch })}
                onSelectCourse={(code) => selectCourse(code)}
                onDeallocate={(code) => { setPreview(null); moveCourse.mutate({ code, term: null }); }}
                onToggleLock={(code, lock) => lockToggle.mutate({ code, lock })}
              />
            )}
            {tab === "requirements" && (
              <RequirementsPanel
                categories={requirementsQ.data?.categories ?? []}
                busy={reqMut.add.isPending || reqMut.remove.isPending}
                onAddEntry={(key, body) => reqMut.add.mutate({ key, body })}
                onDeleteEntry={(id) => reqMut.remove.mutate(id)}
              />
            )}
          </div>
        </aside>
      </div>

      {view === "schedule" && (
        <SchedulePage
          plan={plan}
          courses={courses}
          courseByCode={byCode}
          savingSection={setSection.isPending}
          onSetSection={(code, term, sectionId) => setSection.mutate({ code, term, sectionId })}
          onOpenCourse={openCourseFromSchedule}
        />
      )}

      {/* v4 (C1, ADR-023): enrollment campaign view — mounts conditionally like Horários. */}
      {view === "enrollment" && (
        <CampaignPage plan={plan} courseByCode={byCode} onOpenCourse={openCourseFromSchedule} />
      )}

      {showImport && (
        <ImportReview
          planId={planId}
          gvId={gvId}
          onClose={() => setShowImport(false)}
          onApplied={() => setPreview(null)}
        />
      )}
    </div>
  );
}

function FullLoading() {
  return <div className="fullscreen-msg"><div className="spinner" aria-label="Carregando" /></div>;
}

function FullError({ message }: { message: string }) {
  return (
    <div className="fullscreen-msg">
      <div className="err-card"><strong>Ops.</strong><p>{message}</p></div>
    </div>
  );
}
