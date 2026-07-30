import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Api } from "../api/endpoints";
import type {
  AutoplanRequest,
  CampaignPatchBody,
  CourseStatePatch,
  EnrollmentRequestCreateBody,
  EnrollmentRequestPatchBody,
  ImportProposal,
  PlanCreate,
  PlanPatch,
  RecommendRequest,
  WhatIfAssumption,
} from "../api/types";

export const keys = {
  gradeVersions: ["grade-versions"] as const,
  curriculum: (gvId: string) => ["curriculum", gvId] as const,
  plans: ["plans"] as const,
  plan: (planId: string) => ["plan", planId] as const,
  validation: (planId: string) => ["validation", planId] as const,
  requirements: ["requirements"] as const,
  sections: (term: string) => ["sections", term] as const,
  // v5 F1c: prefix ["electives", planId] allows plan-wide invalidation.
  electives: (planId: string, term: string) => ["electives", planId, term] as const,
  // v4 (A2): prefix ["eligibility", planId] is used for plan-wide invalidation.
  eligibility: (planId: string, term: string) => ["eligibility", planId, term] as const,
  // v4 (C1): prefixes ["campaign", planId] / ["campaign-queue", planId] allow plan-wide invalidation.
  campaigns: (planId: string) => ["campaigns", planId] as const,
  campaign: (planId: string, campaignId: string) => ["campaign", planId, campaignId] as const,
  campaignQueue: (planId: string, campaignId: string, round: string) =>
    ["campaign-queue", planId, campaignId, round] as const,
  // v4 (C3): prefix ["special-candidates", planId] allows plan-wide invalidation.
  specialCandidates: (planId: string, campaignId: string) =>
    ["special-candidates", planId, campaignId] as const,
};

// ---------- queries ----------

export function useGradeVersions() {
  return useQuery({ queryKey: keys.gradeVersions, queryFn: Api.gradeVersions, staleTime: Infinity });
}

export function useCurriculum(gvId: string | undefined) {
  return useQuery({
    queryKey: keys.curriculum(gvId ?? "?"),
    queryFn: () => Api.curriculum(gvId!),
    enabled: !!gvId,
  });
}

export function usePlans() {
  return useQuery({ queryKey: keys.plans, queryFn: Api.plans });
}

export function usePlan(planId: string | undefined) {
  return useQuery({
    queryKey: keys.plan(planId ?? "?"),
    queryFn: () => Api.getPlan(planId!),
    enabled: !!planId,
  });
}

export function useRequirements(planId?: string) {
  return useQuery({
    queryKey: [...keys.requirements, planId ?? null],
    queryFn: () => Api.requirements(planId),
  });
}

export function useSections(term: string | undefined) {
  return useQuery({
    queryKey: keys.sections(term ?? "?"),
    queryFn: () => Api.sections(term!),
    enabled: !!term,
  });
}

// v4 (A2, ADR-021): "blocked by prereq" comes from the server, never computed locally.
export function useCourseEligibility(planId: string | undefined, term: string | undefined) {
  return useQuery({
    queryKey: keys.eligibility(planId ?? "?", term ?? "?"),
    queryFn: () => Api.eligibility(planId!, term!),
    enabled: !!planId && !!term,
  });
}

// v5 F1c (ADR-028): read-only elective catalog of the plan's grade for a term.
export function useElectives(planId: string | undefined, term: string | undefined) {
  return useQuery({
    queryKey: keys.electives(planId ?? "?", term ?? "?"),
    queryFn: () => Api.electives(planId!, term!),
    enabled: !!planId && !!term,
  });
}

// ---------- invalidation helper ----------

function usePlanInvalidator() {
  const qc = useQueryClient();
  return (planId?: string) => {
    if (planId) {
      void qc.invalidateQueries({ queryKey: keys.plan(planId) });
      void qc.invalidateQueries({ queryKey: keys.validation(planId) });
      // v4 (A2): eligibility depends on course statuses + item terms — refresh all terms.
      void qc.invalidateQueries({ queryKey: ["eligibility", planId] });
    }
    void qc.invalidateQueries({ queryKey: keys.plans });
  };
}

// v5 F1c (ADR-028): "selecionar semestre" — commit the term's choices. The plan,
// curriculum (a new node), requirements (hours) and electives (adopted flag) all
// change, so invalidate broadly.
function useElectiveInvalidator(planId: string | undefined, gvId: string | undefined) {
  const qc = useQueryClient();
  const invalidatePlan = usePlanInvalidator();
  return () => {
    invalidatePlan(planId);
    if (gvId) void qc.invalidateQueries({ queryKey: keys.curriculum(gvId) });
    void qc.invalidateQueries({ queryKey: keys.requirements });
    if (planId) void qc.invalidateQueries({ queryKey: ["electives", planId] });
    if (planId) void qc.invalidateQueries({ queryKey: ["sections"] });
    // item 5: the commit also creates/fills the term's enrollment campaign
    // (rematrícula requests) — the Matrícula view must not show a stale list.
    if (planId) {
      void qc.invalidateQueries({ queryKey: keys.campaigns(planId) });
      void qc.invalidateQueries({ queryKey: ["campaign", planId] });
      void qc.invalidateQueries({ queryKey: ["campaign-queue", planId] });
      void qc.invalidateQueries({ queryKey: ["special-candidates", planId] });
    }
  };
}

export function useCommitTerm(planId: string | undefined, gvId: string | undefined) {
  const invalidate = useElectiveInvalidator(planId, gvId);
  return useMutation({
    mutationFn: ({ term, choices }: { term: string; choices: { course_code: string; section_id: string | null }[] }) =>
      Api.commitTerm(planId!, term, choices),
    onSettled: () => invalidate(),
  });
}

export function useDropElective(planId: string | undefined, gvId: string | undefined) {
  const invalidate = useElectiveInvalidator(planId, gvId);
  return useMutation({
    mutationFn: (code: string) => Api.dropElective(planId!, code),
    onSettled: () => invalidate(),
  });
}

// ---------- mutations ----------

export function useMoveCourse(planId: string | undefined) {
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: async ({ code, term }: { code: string; term: string | null }): Promise<void> => {
      if (term === null) await Api.deleteItem(planId!, code);
      else await Api.putItem(planId!, code, { term });
    },
    onSettled: () => invalidate(planId),
  });
}

export function useSetSection(planId: string | undefined) {
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: ({ code, term, sectionId }: { code: string; term: string; sectionId: string | null }) =>
      Api.putItem(planId!, code, { term, section_id: sectionId }),
    onSettled: () => invalidate(planId),
  });
}

export function useLockToggle(planId: string | undefined) {
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: async ({ code, lock }: { code: string; lock: boolean }): Promise<void> => {
      if (lock) await Api.lockItem(planId!, code);
      else await Api.unlockItem(planId!, code);
    },
    onSettled: () => invalidate(planId),
  });
}

export function useCourseStatePatch(planId: string | undefined, gvId: string | undefined) {
  const qc = useQueryClient();
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: ({ code, patch }: { code: string; patch: CourseStatePatch }) =>
      Api.patchCourseState(code, patch),
    onSettled: () => {
      if (gvId) void qc.invalidateQueries({ queryKey: keys.curriculum(gvId) });
      invalidate(planId);
      void qc.invalidateQueries({ queryKey: keys.requirements });
    },
  });
}

export function usePlanPatch(planId: string | undefined) {
  const qc = useQueryClient();
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: (patch: PlanPatch) => Api.patchPlan(planId!, patch),
    onSuccess: (plan) => {
      qc.setQueryData(keys.plan(planId!), plan);
    },
    onSettled: () => invalidate(planId),
  });
}

export function usePlanCreate() {
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: (body: PlanCreate) => Api.createPlan(body),
    onSettled: () => invalidate(),
  });
}

export function usePlanDelete() {
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: (planId: string) => Api.deletePlan(planId),
    onSettled: () => invalidate(),
  });
}

export function useAutoplan(planId: string | undefined) {
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: (body: AutoplanRequest) => Api.autoplan(planId!, body),
    onSuccess: (result) => {
      if (result.applied) invalidate(planId);
    },
  });
}

export function useScheduleCheck(planId: string | undefined) {
  return useMutation({
    mutationFn: (extraSectionIds: string[] = []) => Api.scheduleCheck(planId!, extraSectionIds),
  });
}

export function useRecommend(planId: string | undefined) {
  return useMutation({ mutationFn: (body: RecommendRequest) => Api.recommend(planId!, body) });
}

// v3 (F5): scrape offers for the plan's target term (server derives the term).
export function useScrapeSections(planId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => Api.scrapePlanSections(planId!),
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: keys.sections(result.term) });
      if (planId) {
        // v5 (F1a/Part B): the scrape rewrites the elective CATALOG and the
        // electives' offer — without this the OfferList keeps showing the
        // pre-scrape catalog (the whole point of "Buscar ofertas").
        void qc.invalidateQueries({ queryKey: ["electives", planId] });
        void qc.invalidateQueries({ queryKey: keys.plan(planId) });
        // v4 (C3): the ranking is computed over the term's offer — refresh it after a scrape.
        void qc.invalidateQueries({ queryKey: ["special-candidates", planId] });
        void qc.invalidateQueries({ queryKey: ["campaign-queue", planId] });
      }
    },
  });
}

// ---------- v4 (C1, ADR-017): enrollment campaigns ----------

export function useCampaigns(planId: string | undefined) {
  return useQuery({
    queryKey: keys.campaigns(planId ?? "?"),
    queryFn: () => Api.campaigns(planId!),
    enabled: !!planId,
  });
}

export function useCampaign(planId: string | undefined, campaignId: string | undefined) {
  return useQuery({
    queryKey: keys.campaign(planId ?? "?", campaignId ?? "?"),
    queryFn: () => Api.campaign(planId!, campaignId!),
    enabled: !!planId && !!campaignId,
  });
}

// Queue is server-ranked (spec 6.5): the front never re-orders or filters it.
export function useCampaignQueue(
  planId: string | undefined,
  campaignId: string | undefined,
  round: string | undefined,
) {
  return useQuery({
    queryKey: keys.campaignQueue(planId ?? "?", campaignId ?? "?", round ?? "?"),
    queryFn: () => Api.campaignQueue(planId!, campaignId!, round!),
    enabled: !!planId && !!campaignId && !!round,
  });
}

function useCampaignInvalidator(planId: string | undefined) {
  const qc = useQueryClient();
  return () => {
    if (!planId) return;
    void qc.invalidateQueries({ queryKey: keys.campaigns(planId) });
    void qc.invalidateQueries({ queryKey: ["campaign", planId] });
    void qc.invalidateQueries({ queryKey: ["campaign-queue", planId] });
    // v4 (C3): accepted sections feed the ranking (fit/conflicts_with) — keep it fresh.
    void qc.invalidateQueries({ queryKey: ["special-candidates", planId] });
  };
}

export function useCreateCampaign(planId: string | undefined) {
  const qc = useQueryClient();
  const invalidate = useCampaignInvalidator(planId);
  return useMutation({
    mutationFn: (term?: string) => Api.createCampaign(planId!, term),
    onSuccess: (campaign) => {
      if (planId) qc.setQueryData(keys.campaign(planId, campaign.id), campaign);
    },
    onSettled: () => invalidate(),
  });
}

export function useCampaignPatch(planId: string | undefined, campaignId: string | undefined) {
  const qc = useQueryClient();
  const invalidate = useCampaignInvalidator(planId);
  return useMutation({
    mutationFn: (body: CampaignPatchBody) => Api.patchCampaign(planId!, campaignId!, body),
    onSuccess: (campaign) => {
      if (planId) qc.setQueryData(keys.campaign(planId, campaign.id), campaign);
    },
    onSettled: () => invalidate(),
  });
}

/** Request lifecycle mutations. Accepting an add/drop syncs plan_item server-side
 * (ADR-017), so every settle refreshes campaign + plan + eligibility scopes. */
export function useCampaignRequestMutations(
  planId: string | undefined,
  campaignId: string | undefined,
) {
  const qc = useQueryClient();
  const invalidatePlanScope = usePlanInvalidator();
  const invalidateCampaignScope = useCampaignInvalidator(planId);
  const settle = () => {
    invalidateCampaignScope();
    invalidatePlanScope(planId);
  };
  const create = useMutation({
    mutationFn: (body: EnrollmentRequestCreateBody) =>
      Api.createCampaignRequest(planId!, campaignId!, body),
    onSettled: settle,
  });
  const patch = useMutation({
    mutationFn: ({ requestId, body }: { requestId: number; body: EnrollmentRequestPatchBody }) =>
      Api.patchCampaignRequest(planId!, campaignId!, requestId, body),
    onSuccess: (res) => {
      // Accept returns the fully revalidated plan — adopt it immediately (fresh diagnostics).
      if (res.plan && planId) qc.setQueryData(keys.plan(planId), res.plan);
    },
    onSettled: settle,
  });
  const remove = useMutation({
    mutationFn: (requestId: number) => Api.deleteCampaignRequest(planId!, campaignId!, requestId),
    onSettled: settle,
  });
  return { create, patch, remove };
}

// ---------- v4 (C2, ADR-019/024): correction assistant ----------

/** POST with a body => mutation (on demand). Suggestions are pure server
 * computation over the current base — nothing to invalidate here; applying
 * one goes through the request mutations above (never the plan directly). */
export function useSwapSuggestions(planId: string | undefined, campaignId: string | undefined) {
  return useMutation({
    mutationFn: (maxSuggestions?: number) => Api.swapSuggestions(planId!, campaignId!, maxSuggestions),
  });
}

/** Stateless what-if simulator (ADR-024): the server persists NOTHING
 * (`persisted: false` always), so this mutation invalidates nothing. */
export function useWhatIf(planId: string | undefined, campaignId: string | undefined) {
  return useMutation({
    mutationFn: (assume: WhatIfAssumption[]) => Api.whatIf(planId!, campaignId!, assume),
  });
}

// ---------- v4 (C3, spec 6.7): special-enrollment advisor ----------

/** Server-ranked top-5 (order, score, badges and max_choices all come from the
 * server — the front only renders). Refetched when its inputs change: request
 * mutations (campaign invalidator) and offer scrapes both invalidate the
 * ["special-candidates", planId] prefix. */
export function useSpecialCandidates(
  planId: string | undefined,
  campaignId: string | undefined,
) {
  return useQuery({
    queryKey: keys.specialCandidates(planId ?? "?", campaignId ?? "?"),
    queryFn: () => Api.specialCandidates(planId!, campaignId!),
    enabled: !!planId && !!campaignId,
  });
}

export function useImportHistorico() {
  return useMutation({ mutationFn: (file: File) => Api.importHistorico(file) });
}

export function useApplyHistorico(planId: string | undefined, gvId: string | undefined) {
  const qc = useQueryClient();
  const invalidate = usePlanInvalidator();
  return useMutation({
    mutationFn: ({ proposal, setStart }: { proposal: ImportProposal; setStart: boolean }) =>
      Api.applyHistorico(proposal, setStart && planId ? { plan_id: planId } : undefined),
    onSettled: () => {
      if (gvId) void qc.invalidateQueries({ queryKey: keys.curriculum(gvId) });
      invalidate(planId);
      void qc.invalidateQueries({ queryKey: keys.requirements });
    },
  });
}

export function useRequirementMutations() {
  const qc = useQueryClient();
  const invalidate = () => void qc.invalidateQueries({ queryKey: keys.requirements });
  const add = useMutation({
    mutationFn: ({ key, body }: { key: string; body: { description: string; hours: number; entry_date?: string | null } }) =>
      Api.addRequirementEntry(key, body),
    onSettled: invalidate,
  });
  const patch = useMutation({
    mutationFn: ({ entryId, body }: { entryId: number; body: { description?: string; hours?: number; entry_date?: string | null } }) =>
      Api.patchRequirementEntry(entryId, body),
    onSettled: invalidate,
  });
  const remove = useMutation({
    mutationFn: (entryId: number) => Api.deleteRequirementEntry(entryId),
    onSettled: invalidate,
  });
  return { add, patch, remove };
}
