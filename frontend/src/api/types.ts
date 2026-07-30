// Types mirror backend/app/domain (contract in docs/ARCHITECTURE.md + v2).

export type Offer = "/1" | "/2" | "both";
export type OfferSource = "seed" | "user";
export type CourseStatus = "aprovada" | "cursando" | "falta" | "pendente";
export type CourseKind = "obrigatoria" | "optativa" | "atividade" | "fora_curriculo";
export type Severity = "error" | "warning" | "info";

export type DiagnosticType =
  | "PREREQ_VIOLATION"
  | "OFFER_MISMATCH"
  | "TERM_OVERLOADED"
  | "TARGET_UNREACHABLE"
  | "LOCK_CONFLICT"
  | "UNALLOCATED_REQUIRED"
  | "REQUIREMENT_SHORTFALL"
  | "PREREQ_CYCLE"
  | "TRANSITION_NOTE"
  | "SCHEDULE_CONFLICT"
  | "NOT_OFFERED"
  | "SECTION_STALE"
  | "INCONSISTENT_COMPLETION"
  | (string & {});

export interface Diagnostic {
  id: string;
  type: DiagnosticType;
  severity: Severity;
  course_codes: string[];
  related_codes: string[];
  term: string | null;
  message: string;
  details: Record<string, unknown>;
}

export interface GradeVersion {
  id: string;
  label: string;
  is_base: boolean;
  is_default: boolean;
  derived_from: string | null;
  reform_id?: string | null;
  transition_policy?: string | null;
}

export interface CurriculumCourse {
  code: string;
  name: string;
  short: string | null;
  credits: number;
  hours: number;
  kind: CourseKind;
  suggested_term_index: number | null;
  prereqs: string[];
  offer: Offer;
  offer_source: OfferSource;
  status: CourseStatus;
  difficulty: number;
  note: string | null;
  completed_term: string | null; // v2 (F1)
}

export interface RequirementCategoryBase {
  key: string;
  label: string;
  min_hours: number;
  rule_note: string | null;
  counts_courses?: boolean;
}

export interface CurriculumResponse {
  grade_version: { id: string; label: string; program?: string };
  courses: CurriculumCourse[];
  requirement_categories: RequirementCategoryBase[];
}

export interface PlanItem {
  course_code: string;
  term: string;
  locked: boolean;
  section_id?: string | null; // v2 (F3)
}

export interface TermSummary {
  term: string;
  credits: number;
  difficulty_sum: number;
  course_codes: string[];
}

export interface CriticalPath {
  course_codes: string[];
  length_terms: number;
  earliest_completion_term: string | null;
}

// v2 (F1)
export interface Timeline {
  terms: string[];
  past_terms: { term: string; course_codes: string[]; credits: number }[];
  undated_completed: string[];
}

export interface PlanSummary {
  id: string;
  name: string;
  grade_version_id: string;
  current_term: string;
  target_term: string | null;
  start_term: string | null; // v2 (F1)
  max_credits_per_term: number;
  max_difficulty_per_term: number | null;
}

export interface PlanDetail extends PlanSummary {
  items: PlanItem[];
  unallocated: string[];
  term_summaries: TermSummary[];
  diagnostics: Diagnostic[];
  requirements_progress?: RequirementProgress[];
  blocked_codes?: string[];
  critical_path?: CriticalPath;
  timeline?: Timeline; // v2 (F1)
}

export interface ValidateResult {
  valid: boolean;
  diagnostics: Diagnostic[];
  critical_path: CriticalPath;
  blocked_codes?: string[];
}

export interface AutoplanRequest {
  target_term?: string;
  mode: "apply" | "preview";
  respect_existing?: boolean;
}

export interface AutoplanResult {
  feasible: boolean;
  proposal: PlanItem[];
  diagnostics: Diagnostic[];
  critical_path: CriticalPath;
  applied: boolean;
}

export interface PutItemResponse {
  item: PlanItem;
  diagnostics: Diagnostic[];
  term_summaries: TermSummary[];
}

export interface CourseStatePatch {
  status?: CourseStatus;
  offer?: Offer | null;
  difficulty?: number;
  note?: string | null;
  completed_term?: string | null; // v2 (F1)
}

export interface PlanPatch {
  name?: string;
  grade_version_id?: string;
  current_term?: string;
  target_term?: string | null;
  start_term?: string | null; // v2 (F1)
  max_credits_per_term?: number;
  max_difficulty_per_term?: number | null;
}

export interface PlanCreate {
  name: string;
  grade_version_id?: string;
  current_term: string;
  target_term?: string;
  start_term?: string; // v2 (F1)
  max_credits_per_term?: number;
  max_difficulty_per_term?: number | null;
}

export interface RequirementEntry {
  id: number;
  description: string;
  hours: number;
  entry_date: string | null;
}

export interface RequirementProgress extends RequirementCategoryBase {
  logged_hours: number;
  course_hours: number;
  total_hours: number;
  remaining_hours: number;
  satisfied: boolean;
  entries?: RequirementEntry[];
}

export interface RequirementsResponse {
  categories: RequirementProgress[];
}

// ===== v2 (F3): sections / schedule =====

export interface Timeslot {
  weekday: number; // 0=Mon..6=Sun
  start: string; // "HH:MM"
  end: string;
}

export interface Section {
  id: string;
  course_code: string;
  course_name: string | null;
  term: string;
  label: string | null;
  professor: string | null;
  capacity: number | null;
  enrolled: number | null;
  source: string;
  fetched_at: string | null;
  note: string | null;
  /** v4 (A3, ADR-020): target programs when offered to another course; null = own/unknown (old data). */
  offered_to: string[] | null;
  /** v4 (A3): server-derived (= offered_to != null). */
  foreign_offer: boolean;
  timeslots: Timeslot[];
}

export interface SectionsResponse {
  term: string;
  has_data: boolean;
  sections: Section[];
}

export interface ScheduleCheckResult {
  terms_checked: string[];
  terms_without_data: string[];
  items_without_section: string[];
  diagnostics: Diagnostic[];
}

export interface RecommendRequest {
  term: string;
  max_credits?: number | null;
  max_difficulty?: number | null;
  locked_choices?: string[];
  top_n?: number;
  /** v5 (F2a, ADR-029): course_codes to pin — the engine picks the section. Max 6. */
  pinned_courses?: string[];
}

export interface Recommendation {
  rank: number;
  score: number;
  score_breakdown: Record<string, number>;
  choices: { course_code: string; section_id: string }[];
  left_out: { course_code: string; reason: string }[];
  diagnostics: Diagnostic[];
}

/** v5 (F2a, ADR-029/030): per-pinned-course outcome vocabulary (server-decided). */
export type PinnedStatus =
  | "ok"
  | "sem_oferta"
  | "bloqueada"
  | "turma_ja_fixada"
  | "conflito"
  | "estoura_limite";

export interface PinnedInfo {
  course_code: string;
  status: PinnedStatus;
  /** Section chosen by the engine in the top recommendation; null when none fits. */
  section_id: string | null;
  /** Other sections of the same course in the term. */
  alternatives: string[];
  /** Human-readable explanation when the status is problematic. */
  reason: string | null;
}

export interface RecommendResult {
  term: string;
  recommendations: Recommendation[];
  eligible_courses: string[];
  diagnostics: Diagnostic[];
  truncated: boolean;
  /** v5 (F2a): present when pinned_courses was sent — one entry per pinned course. */
  pinned?: PinnedInfo[];
  /** v5 (F2a, D5): true = no viable combination with the pins; recommendations comes []. */
  pinned_infeasible?: boolean;
}

// ===== v2 (F2): PDF history import =====

export interface ImportMatch {
  code: string;
  name_in_pdf: string;
  term: string | null;
  status_inferred: "aprovada" | "cursando";
  grade_in_pdf: string | null;
  current_status: string;
  current_completed_term: string | null;
  conflict: boolean;
  apply: boolean;
}

export interface ImportProposal {
  source: { filename: string | null; parser: string; parser_version: string; parsed_at: string };
  inferred: { start_term: string | null; student_name: string | null; student_id: string | null };
  matches: ImportMatch[];
  unmatched: { code_in_pdf: string; name_in_pdf: string; term: string | null; status_inferred: string; hint: string }[];
  unparsed_lines: string[];
  warnings: string[];
}

export interface ImportApplyResult {
  applied: number;
  skipped: number;
  diagnostics_hint: string;
}

// ===== v4 (A2, ADR-021): per-(plan, term) eligibility =====

export interface MissingPrereq {
  code: string;
  name: string;
  status: string; // effective status of the missing prereq (e.g. "falta")
}

export interface CourseEligibility {
  code: string;
  eligible: boolean;
  missing_prereqs: MissingPrereq[];
}

/** Response of GET /plans/{planId}/terms/{term}/eligibility (ARCHITECTURE-v4 §6.2). */
export interface EligibilityResponse {
  term: string;
  courses: CourseEligibility[];
}

// ===== v4 (C1, ADR-017/018): enrollment campaigns =====

export type CampaignStatus = "aberta" | "encerrada";
export type EnrollmentRequestStatus = "desejada" | "pedida" | "aceita" | "negada";
export type EnrollmentRequestKind = "add" | "drop";

/** Round rules are DATA from the grade seed (ADR-018) — keys/labels never hard-coded in UI. */
export interface CampaignRound {
  key: string;
  label: string;
  order: number;
  scope: "proprio_curso" | "qualquer_curso" | (string & {});
  max_adds: number | null;
  allows_drops: boolean;
  drops_before_adds?: boolean;
  note?: string;
}

export interface EnrollmentRequest {
  id: number;
  round: string;
  kind: EnrollmentRequestKind;
  course_code: string;
  course_name: string | null;
  section_id: string | null;
  section_label: string | null;
  status: EnrollmentRequestStatus;
  note: string | null;
  /** Server-derived (ADR-017): the section still exists in the offer. null = no section chosen. */
  section_alive: boolean | null;
  /** Server-derived: the plan_item currently points at this exact section. */
  in_plan: boolean;
  created_at: string;
  updated_at: string;
}

/** Full campaign shape (spec 6.3) — returned by POST/GET/PATCH of a single campaign. */
export interface Campaign {
  id: string;
  plan_id: string;
  term: string;
  status: CampaignStatus;
  note: string | null;
  rounds: CampaignRound[];
  requests: EnrollmentRequest[];
  /** Convenience for locked_choices in recommend-schedule (spec 5.3). */
  accepted_section_ids: string[];
  created_at: string;
  updated_at: string;
}

/** List item of GET /plans/{id}/campaigns — the persisted per-term history. */
export interface CampaignSummary {
  id: string;
  term: string;
  status: CampaignStatus;
  note: string | null;
  requests_total: number;
  /** round -> status -> count */
  counts: Record<string, Record<string, number>>;
  created_at: string;
  updated_at: string;
}

export interface CampaignsResponse {
  campaigns: CampaignSummary[];
}

export interface CampaignPatchBody {
  status?: CampaignStatus;
  note?: string | null;
}

export interface EnrollmentRequestCreateBody {
  round: string;
  kind?: EnrollmentRequestKind;
  course_code: string;
  section_id?: string | null;
}

export interface EnrollmentRequestCreateResponse {
  request: EnrollmentRequest;
  /** e.g. scope mismatch — informational, never blocking (spec 5.1). */
  warnings: string[];
}

export interface EnrollmentRequestPatchBody {
  status?: EnrollmentRequestStatus;
  section_id?: string | null;
  note?: string | null;
}

export interface EnrollmentRequestPatchResponse {
  request: EnrollmentRequest;
  /** Accept applied its effect on plan_item (ADR-017). */
  plan_synced: boolean;
  /** Fully revalidated plan — present only when plan_synced. */
  plan?: PlanDetail;
}

// spec 6.5: criticality queue (server-ranked; the front only renders)
export interface CampaignQueueEntry {
  rank: number;
  course_code: string;
  name: string;
  critical: boolean;
  height: number;
  unlocks: number;
  credits: number;
  difficulty: number;
  blocked: boolean;
  missing_prereqs: MissingPrereq[];
  /** Section ids offered in the campaign term. */
  sections: string[];
  already_accepted: boolean;
  requested_in_round: boolean;
}

export interface CampaignQueueResponse {
  term: string;
  round: string;
  queue: CampaignQueueEntry[];
}

// ===== v4 (C2, ADR-019/024): correction assistant — swap suggestions + what-if (spec 6.6) =====

/** One leg of a swap: a course + the concrete section involved. */
export interface SwapChoice {
  course_code: string;
  section_id: string;
}

/** Course-criticality gains/losses of a swap (spec 7.4) — auditable in the UI. */
export interface SwapScoreBreakdown {
  gained_critical: number;
  gained_unlocks: number;
  gained_credits: number;
  lost_critical: number;
  lost_unlocks: number;
  lost_credits: number;
}

export interface SwapSuggestion {
  rank: number;
  /** First wave — removals are processed BEFORE additions (two-waves semantics). */
  drop: SwapChoice[];
  /** Second wave — additions; their schedule was checked with the drops' slots free. */
  add: SwapChoice[];
  /** valor(add) − valor(drop); the server only suggests delta > 0. */
  score_delta: number;
  score_breakdown: SwapScoreBreakdown;
  /** Timeslots freed by the drops — transparency of "why the adds fit". */
  freed_slots: Timeslot[];
  /** Contract guarantee (spec 6.6): always empty in every suggestion. */
  conflicts_after: unknown[];
}

export interface SwapSuggestionsResponse {
  term: string;
  /** Base: accepted sections of the campaign + plan items of the term with a section. */
  held_sections: string[];
  suggestions: SwapSuggestion[];
  truncated: boolean;
}

export type WhatIfOutcome = "aceita" | "negada";

export interface WhatIfAssumption {
  request_id: number;
  outcome: WhatIfOutcome;
}

/** Both baseline AND projection come complete from the backend: full
 * term_summaries + diagnostics on each side, for column-by-column comparison.
 * Note: earliest_completion_term is the SAME on both sides (the engine does
 * not move it) — the difference lives in term_summaries/diagnostics. */
export interface WhatIfSide {
  earliest_completion_term: string | null;
  critical_path: CriticalPath;
  term_summaries: TermSummary[];
  diagnostics: Diagnostic[];
}

export interface WhatIfResponse {
  /** ADR-024: always false — the simulator never writes anything. */
  persisted: boolean;
  baseline: WhatIfSide;
  projection: WhatIfSide;
}

// ===== v4 (C3, spec 6.7): special-enrollment advisor — server-ranked top-5 =====

/** Weighted score parts (spec 7.3) — fixed keys, auditable in the UI. */
export interface SpecialScoreBreakdown {
  critical: number;
  unlocks: number;
  credits: number;
  fit: number;
  vacancy: number;
  full: number;
  blocked: number;
  foreign: number;
}

export interface SpecialCandidate {
  rank: number;
  course_code: string;
  name: string;
  /** Best section of the course (server-picked: max fit+vacancy, tie by id). */
  section_id: string;
  section_label: string | null;
  score: number;
  score_breakdown: SpecialScoreBreakdown;
  /** D6: requestable anyway (authorized break) — never rank 1 with an alternative. */
  blocked: boolean;
  missing_prereqs: MissingPrereq[];
  capacity: number | null;
  enrolled: number | null;
  /** D5: enrolled >= capacity — penalized and flagged, never excluded. */
  full: boolean;
  foreign_offer: boolean;
  offered_to: string[] | null;
  /** Accepted section ids of the campaign clashing with the best section. */
  conflicts_with: string[];
  /** Other sections of the same course in the term. */
  alternatives: string[];
}

export interface SpecialCandidatesResponse {
  term: string;
  /** From the grade's round rules (ADR-018) — null = no 'especial' round / no limit. */
  max_choices: number | null;
  candidates: SpecialCandidate[];
  /** Freshness nudge derived from the oldest fetched_at; null = no offer data. */
  rescrape_hint: string | null;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

// ===== v3 (F4): current_term validation (ADR-010) =====

/** Item allocated before the new current_term — sent in 400 details.offending_items. */
export interface OffendingItem {
  course_code: string;
  term: string;
}

// ===== v3 (F5): scrape by plan (ADR-011) =====

export type ScrapeSource = "scraper:ufpel-curso" | "scraper:ufpel-professores" | (string & {});

export interface ScrapeRejectedItem {
  course_code: string;
  reason: string;
}

export interface ScrapeMissingCourse {
  code: string;
  name: string;
  reason: string;
}

/** Elective offered on the portal but outside the plan's grade (ADR-016, read-only). */
export interface DiscoveredOptativa {
  code: string;
  name: string;
  sections: number;
}

/** Response of POST /admin/plans/{planId}/sections/scrape (ARCHITECTURE-v3 §4.6). */
export interface ScrapeResult {
  term: string;
  source: ScrapeSource;
  level_used: 1 | 2;
  upserted: number;
  removed: number;
  orphaned_choices: number;
  rejected: ScrapeRejectedItem[];
  missing_without_offer: ScrapeMissingCourse[];
  discovered_optativas: DiscoveredOptativa[];
  scanned_codes: string[];
  pages_fetched: number;
  pages_failed: number;
}


// ===== v5 F1b/F1c (ADR-028): elective catalog + semester commit =====
export interface Elective {
  code: string;
  name: string;
  credits: number;
  hours: number;
  prereqs: string[];
  kind: string;
  adopted: boolean;
  in_seed: boolean;
  status: string;
  sections: Section[];
  foreign_offer: boolean;
}

export interface ElectivesResponse {
  grade_version_id: string;
  term: string;
  fetched_at: string | null;
  electives: Elective[];
}

export interface TermChoice {
  course_code: string;
  section_id: string | null;
}

/** item 5: rematrícula pre-fill done by the commit (server-decided). */
export interface TermCommitCampaign {
  campaign_id: string | null;
  campaign_created: boolean;
  round: string | null;
  created: string[];
  updated: string[];
  skipped: string[];
  /** Foreign-course turmas: never auto-requested — they need matrícula especial. */
  special_needed: string[];
}

export interface TermCommitResult {
  term: string;
  committed: { adopted: string[]; allocated: string[]; unchanged: string[] };
  campaign: TermCommitCampaign;
  plan: PlanDetail;
}
