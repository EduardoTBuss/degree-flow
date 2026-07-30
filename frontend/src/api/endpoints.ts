import { http } from "./client";
import type {
  AutoplanRequest,
  AutoplanResult,
  Campaign,
  CampaignPatchBody,
  CampaignQueueResponse,
  CampaignsResponse,
  CourseStatePatch,
  CurriculumCourse,
  CurriculumResponse,
  ElectivesResponse,
  EligibilityResponse,
  EnrollmentRequestCreateBody,
  EnrollmentRequestCreateResponse,
  EnrollmentRequestPatchBody,
  EnrollmentRequestPatchResponse,
  GradeVersion,
  ImportApplyResult,
  ImportProposal,
  PlanCreate,
  PlanDetail,
  PlanPatch,
  PlanSummary,
  PutItemResponse,
  RecommendRequest,
  RecommendResult,
  RequirementEntry,
  RequirementsResponse,
  TermChoice,
  TermCommitResult,
  ScheduleCheckResult,
  ScrapeResult,
  SectionsResponse,
  SpecialCandidatesResponse,
  SwapSuggestionsResponse,
  ValidateResult,
  WhatIfAssumption,
  WhatIfResponse,
} from "./types";

function normalizeList<T>(raw: unknown, key: string): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (raw && typeof raw === "object" && Array.isArray((raw as Record<string, unknown>)[key])) {
    return (raw as Record<string, T[]>)[key];
  }
  return [];
}

const termPath = (t: string) => t.replace("/", "-");

export const Api = {
  // 6.1 Grades e catálogo
  gradeVersions: async (): Promise<GradeVersion[]> =>
    normalizeList<GradeVersion>(await http.get<unknown>("/grade-versions"), "grade_versions"),
  curriculum: (gvId: string) =>
    http.get<CurriculumResponse>(`/grade-versions/${encodeURIComponent(gvId)}/curriculum`),

  // 6.2 Estado por cadeira
  patchCourseState: (code: string, body: CourseStatePatch) =>
    http.patch<CurriculumCourse>(`/courses/${encodeURIComponent(code)}/state`, body),

  // 6.3 Planos
  plans: async (): Promise<PlanSummary[]> =>
    normalizeList<PlanSummary>(await http.get<unknown>("/plans"), "plans"),
  createPlan: (body: PlanCreate) => http.post<PlanDetail>("/plans", body),
  getPlan: (planId: string) => http.get<PlanDetail>(`/plans/${planId}`),
  patchPlan: (planId: string, body: PlanPatch) => http.patch<PlanDetail>(`/plans/${planId}`, body),
  deletePlan: (planId: string) => http.delete<void>(`/plans/${planId}`),

  // 6.4 Alocações e travas
  putItem: (planId: string, code: string, body: { term: string; locked?: boolean; section_id?: string | null }) =>
    http.put<PutItemResponse>(`/plans/${planId}/items/${encodeURIComponent(code)}`, body),
  deleteItem: (planId: string, code: string) =>
    http.delete<void>(`/plans/${planId}/items/${encodeURIComponent(code)}`),
  lockItem: (planId: string, code: string) =>
    http.post<unknown>(`/plans/${planId}/items/${encodeURIComponent(code)}/lock`),
  unlockItem: (planId: string, code: string) =>
    http.post<unknown>(`/plans/${planId}/items/${encodeURIComponent(code)}/unlock`),

  // 6.5 Motor
  validate: (planId: string) => http.post<ValidateResult>(`/plans/${planId}/validate`),
  autoplan: (planId: string, body: AutoplanRequest) =>
    http.post<AutoplanResult>(`/plans/${planId}/autoplan`, body),

  // 6.6 Requisitos de horas
  requirements: (planId?: string) =>
    http.get<RequirementsResponse>(
      `/requirements${planId ? `?plan_id=${encodeURIComponent(planId)}` : ""}`,
    ),
  addRequirementEntry: (key: string, body: { description: string; hours: number; entry_date?: string | null }) =>
    http.post<RequirementEntry>(`/requirements/${encodeURIComponent(key)}/entries`, body),
  patchRequirementEntry: (
    entryId: number,
    body: { description?: string; hours?: number; entry_date?: string | null },
  ) => http.patch<RequirementEntry>(`/requirements/entries/${entryId}`, body),
  deleteRequirementEntry: (entryId: number) => http.delete<void>(`/requirements/entries/${entryId}`),

  // ===== v2 (F3): sections / schedule =====
  sections: (term: string, courseCode?: string) =>
    http.get<SectionsResponse>(
      `/terms/${termPath(term)}/sections${courseCode ? `?course_code=${encodeURIComponent(courseCode)}` : ""}`,
    ),
  // v3 (F5): scrape by plan; target term derived server-side (ADR-011). The
  // exhaustive level-2 sweep visits every professor page (~68 x 0.7s, and
  // growing) — NO client timeout on purpose: the user clicks and waits for the
  // result however long it takes. Per-page protection lives server-side
  // (fetch.py timeout=20 per request).
  scrapePlanSections: (planId: string) =>
    http.post<ScrapeResult>(`/admin/plans/${planId}/sections/scrape`),
  // ===== v5 F1b/F1c (ADR-028): elective catalog + semester commit =====
  electives: (planId: string, term: string) =>
    http.get<ElectivesResponse>(`/plans/${planId}/electives?term=${termPath(term)}`),
  commitTerm: (planId: string, term: string, choices: TermChoice[]) =>
    http.post<TermCommitResult>(`/plans/${planId}/terms/${termPath(term)}/commit`, { choices }),
  dropElective: (planId: string, code: string) =>
    http.delete<void>(`/plans/${planId}/electives/${encodeURIComponent(code)}`),
  scheduleCheck: (planId: string, extraSectionIds: string[] = []) =>
    http.post<ScheduleCheckResult>(`/plans/${planId}/schedule-check`, { extra_section_ids: extraSectionIds }),
  recommend: (planId: string, body: RecommendRequest) =>
    http.post<RecommendResult>(`/plans/${planId}/recommend-schedule`, body),

  // ===== v4 (A2, ADR-021): per-(plan, term) eligibility — the server decides "blocked" =====
  eligibility: (planId: string, term: string) =>
    http.get<EligibilityResponse>(`/plans/${planId}/terms/${termPath(term)}/eligibility`),

  // ===== v4 (C1, ADR-017): enrollment campaigns (spec 6.3/6.4/6.5) =====
  campaigns: (planId: string) =>
    http.get<CampaignsResponse>(`/plans/${planId}/campaigns`),
  createCampaign: (planId: string, term?: string) =>
    http.post<Campaign>(`/plans/${planId}/campaigns`, term ? { term } : {}),
  campaign: (planId: string, campaignId: string) =>
    http.get<Campaign>(`/plans/${planId}/campaigns/${campaignId}`),
  patchCampaign: (planId: string, campaignId: string, body: CampaignPatchBody) =>
    http.patch<Campaign>(`/plans/${planId}/campaigns/${campaignId}`, body),
  deleteCampaign: (planId: string, campaignId: string) =>
    http.delete<void>(`/plans/${planId}/campaigns/${campaignId}`),
  createCampaignRequest: (planId: string, campaignId: string, body: EnrollmentRequestCreateBody) =>
    http.post<EnrollmentRequestCreateResponse>(`/plans/${planId}/campaigns/${campaignId}/requests`, body),
  patchCampaignRequest: (
    planId: string,
    campaignId: string,
    requestId: number,
    body: EnrollmentRequestPatchBody,
  ) =>
    http.patch<EnrollmentRequestPatchResponse>(
      `/plans/${planId}/campaigns/${campaignId}/requests/${requestId}`,
      body,
    ),
  deleteCampaignRequest: (planId: string, campaignId: string, requestId: number) =>
    http.delete<void>(`/plans/${planId}/campaigns/${campaignId}/requests/${requestId}`),
  campaignQueue: (planId: string, campaignId: string, round: string) =>
    http.get<CampaignQueueResponse>(
      `/plans/${planId}/campaigns/${campaignId}/queue?round=${encodeURIComponent(round)}`,
    ),

  // ===== v4 (C2, spec 6.6): correction assistant — the server computes, the front renders =====
  swapSuggestions: (planId: string, campaignId: string, maxSuggestions?: number) =>
    http.post<SwapSuggestionsResponse>(
      `/plans/${planId}/campaigns/${campaignId}/swap-suggestions`,
      maxSuggestions != null ? { max_suggestions: maxSuggestions } : {},
    ),
  // Stateless simulator (ADR-024): persisted=false always — nothing to invalidate.
  whatIf: (planId: string, campaignId: string, assume: WhatIfAssumption[]) =>
    http.post<WhatIfResponse>(`/plans/${planId}/campaigns/${campaignId}/what-if`, { assume }),

  // ===== v4 (C3, spec 6.7): special-enrollment advisor — server ranks, the front renders =====
  specialCandidates: (planId: string, campaignId: string) =>
    http.get<SpecialCandidatesResponse>(
      `/plans/${planId}/campaigns/${campaignId}/special-candidates`,
    ),

  // ===== v2 (F2): PDF history import =====
  importHistorico: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.upload<ImportProposal>("/import/historico", form);
  },
  applyHistorico: (proposal: ImportProposal, setPlanStartTerm?: { plan_id: string; start_term?: string }) =>
    http.post<ImportApplyResult>("/import/historico/apply", {
      proposal,
      set_plan_start_term: setPlanStartTerm ?? null,
    }),
};
