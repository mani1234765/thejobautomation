// Mirrors the FastAPI response models (backend/app/api/*.py).

export interface Me {
  id: string;
  email: string;
  full_name: string | null;
  onboarding_completed: boolean;
  created_at: string;
}

export type WorkMode = "remote" | "hybrid" | "onsite";
export type ExperienceLevel = "fresher" | "internship" | "0-1" | "1-2" | "2-3" | "3-5" | "5+";
export type EmploymentType = "full_time" | "part_time" | "contract" | "internship";

export interface Profile {
  full_name: string | null;
  phone: string | null;
  location: string | null;
  headline: string | null;
  summary: string | null;
  work_authorization: string | null;
  career_objectives: string | null;
  linkedin_url: string | null;
  portfolio_url: string | null;
  target_titles: string[];
  preferred_locations: string[];
  work_modes: WorkMode[];
  experience_levels: ExperienceLevel[];
  employment_types: EmploymentType[];
  industries: string[];
  years_experience: number | null;
  salary_expectation_lpa: number | null;
  notice_period: string | null;
  verified_at: string | null;
}

/** Provenance and review state of an entry extracted from a resume. */
export interface ReviewFields {
  source_ids?: string[];
  confidence?: number | null;
  review_reasons?: string[];
  needs_review?: boolean;
  user_edited?: boolean;
}

export interface Experience extends ReviewFields {
  id: string;
  resume_id: string | null;
  category: "professional" | "internship" | "freelance";
  employer: string;
  title: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  bullets: string[];
  verified: boolean;
}

export interface Project extends ReviewFields {
  id: string;
  resume_id: string | null;
  category: "academic" | "personal" | "professional";
  name: string;
  description: string | null;
  technologies: string[];
  url: string | null;
  verified: boolean;
}

export interface Education extends ReviewFields {
  id: string;
  resume_id: string | null;
  institution: string;
  degree: string | null;
  field: string | null;
  start_date: string | null;
  end_date: string | null;
  grade: string | null;
  details?: string[];
  verified: boolean;
}

export interface Certification extends ReviewFields {
  id: string;
  resume_id: string | null;
  name: string;
  issuer: string | null;
  issued_on: string | null;
  details?: string[];
  verified: boolean;
}

export interface Skill {
  id: string;
  name: string;
  category: string | null;
  source: string;
  verified: boolean;
  evidence: string[];
}

export interface FullProfile {
  profile: Profile;
  experiences: Experience[];
  projects: Project[];
  education: Education[];
  certifications: Certification[];
  skills: Skill[];
  completeness: { percent: number; checks: Record<string, boolean> };
}

export interface Settings {
  min_ctc_lpa: number;
  allow_undisclosed_salary: boolean;
  min_stipend_monthly_inr: number | null;
  allow_undisclosed_stipend: boolean;
  daily_application_limit: number;
  require_approval: boolean;
  min_match_score: number;
  ai_model_override: string | null;
  notify_in_app: boolean;
  notify_email: boolean;
  notify_whatsapp: boolean;
  whatsapp_number: string | null;
  daily_summary: boolean;
  /** Stored values that were out of range and are shown/applied as the nearest valid value. */
  normalized?: { field: string; stored: unknown; effective: unknown; reason: string }[];
}

export interface MatchFactor {
  key: string;
  weight: number;
  score: number | null;
  summary: string;
}

export interface MatchBreakdown {
  score: number;
  factors: MatchFactor[];
  matched_skills: string[];
  related_skills: { required: string; have: string }[];
  missing_required: string[];
  missing_preferred: string[];
  unknown_factors: string[];
  strengths: string[];
  concerns: string[];
  scoring_version: string;
}

export interface EligibilityCheck {
  rule: "SALARY" | "STIPEND" | "SKILLS" | "EXPERIENCE" | "SENIORITY" | "LOCATION" | "EMPLOYMENT";
  outcome: "pass" | "reject" | "review" | "unknown";
  code: string;
  explanation: string;
  evidence: string[];
  missing: string[];
}

export interface EligibilityPolicy {
  min_ctc_lpa: number;
  allow_undisclosed_salary: boolean;
  min_stipend_monthly_inr: number | null;
  allow_undisclosed_stipend: boolean;
  engine: string;
  fx: { source: string; as_of: string } | null;
}

export interface Eligibility {
  status: "eligible" | "review" | "ineligible";
  salary: { decision: string; reason: string };
  blocks: string[];
  confidence: number;
  // Explainable verdicts (engine 2026.11.3+). Older decisions kept for history may lack these.
  verdict?: "ELIGIBLE" | "REJECTED" | "MANUAL_REVIEW";
  summary?: string;
  checks?: EligibilityCheck[];
  reason_codes?: string[];
  decided_by?: string[];
  missing_information?: string[];
  rules?: Record<string, string>;
  policy?: EligibilityPolicy;
  evaluated_at?: string;
}

export interface JobCard {
  id: string;
  title: string;
  company: string;
  location: string | null;
  work_mode: WorkMode | null;
  employment_type: string | null;
  is_internship: boolean;
  seniority: string | null;
  experience_min: number | null;
  experience_max: number | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_period: string | null;
  salary_disclosure: "undisclosed" | "advertised" | "verified";
  department: string | null;
  provider: string;
  url: string;
  apply_url: string | null;
  posted_at: string | null;
  first_seen_at: string;
  closed: boolean;
  required_skills: string[];
  preferred_skills: string[];
  match_score: number | null;
  eligibility: Eligibility["status"] | null;
  matched_skills: string[];
  missing_skills: string[];
  saved_state: "saved" | "dismissed" | null;
  application_id: string | null;
  application_status: string | null;
}

export interface JobDetail extends JobCard {
  description_html: string | null;
  description_text: string;
  match: MatchBreakdown | null;
  eligibility_detail: Eligibility | null;
  eligibility_outdated: boolean;
  eligibility_outdated_reasons?: ("policy" | "engine" | "profile" | "listing" | "untracked")[];
  duplicate_of_id: string | null;
}

export interface JobPage {
  items: JobCard[];
  total: number;
  page: number;
  page_size: number;
  sources: number;
}

export interface JobSource {
  id: string;
  provider: "greenhouse" | "lever" | "ashby";
  identifier: string;
  display_name: string;
  enabled: boolean;
  last_synced_at: string | null;
  /** ok · partial (some listings skipped) · warning (response looked incomplete; nothing closed) · error */
  last_status: "ok" | "partial" | "warning" | "error" | null;
  last_error: string | null;
  last_job_count: number | null;
}

export interface ResumeSummary {
  id: string;
  name: string;
  original_filename: string;
  file_format: "docx" | "pdf";
  size_bytes: number;
  is_primary: boolean;
  parse_status: "pending" | "parsed" | "failed";
  parse_error: string | null;
  parsed_at: string | null;
  created_at: string;
  quality_score: number | null;
  versions: number;
}

export interface QualityIssue {
  severity: "high" | "medium" | "low";
  code: string;
  message: string;
  paragraph_id: string | null;
}

export interface ResumeDetail extends ResumeSummary {
  sections: { canonical_type: string; raw_heading: string | null; confidence: number; text: string; paragraph_ids: string[] }[];
  extracted: {
    contact?: Record<string, string | null>;
    summary?: string | null;
    experiences?: { category: string; employer: string; title: string | null; start_date: string | null; end_date: string | null; is_current: boolean; bullets: string[] }[];
    projects?: { category: string; name: string; technologies: string[] }[];
    education?: { institution: string; degree: string | null; grade: string | null }[];
    skills?: Record<string, string[]>;
    unrecognised_skills?: string[];
    years_experience?: number;
    internship_months?: number;
    seniority?: string;
    missing?: string[];
  };
  quality: { score?: number; issues?: QualityIssue[]; metrics?: Record<string, number | string[]> };
  formatting_preserved: boolean;
}

export type ReviewKind = "experience" | "projects" | "education" | "certifications";
export type ReviewTarget = ReviewKind | "summary" | "skills";
type Reviewed<T> = T & { label: string; issues: string[] };

export interface ResumeReview {
  resume_id: string;
  imported: boolean;
  reviewed_at: string | null;
  last_reextracted_at: string | null;
  review_threshold: number;
  summary: { text: string | null; confidence: number | null; review_reasons: string[]; needs_review: boolean };
  entries: {
    experience: Reviewed<Experience>[];
    projects: Reviewed<Project>[];
    education: Reviewed<Education>[];
    certifications: Reviewed<Certification>[];
  };
  skills: Skill[];
  unclassified: { heading: string | null; text: string; source_ids: string[]; review_reasons: string[] }[];
  needs_review: number;
}
export type ReviewEntry = ResumeReview["entries"][ReviewKind][number];

export interface ReextractPreview {
  add: { kind: ReviewKind; label: string; confidence: number; review_reasons: string[] }[];
  update: { kind: ReviewKind; label: string; current_kind: ReviewKind; current_label: string; entry_id: string }[];
  unchanged: number;
  kept: { kind: ReviewKind; label: string; current_kind: ReviewKind; current_label: string; entry_id: string; differs: boolean }[];
  dismissed: { kind: ReviewKind; label: string }[];
  stale: { kind: ReviewKind; label: string; entry_id: string }[];
  skills: string[];
  summary: { action: "fill" | "differs"; extracted: string; current?: string } | null;
}

export interface ResumeChange {
  id: string;
  paragraph_id: string;
  section: string;
  original: string;
  proposed: string;
  rationale: string;
  kind: "reorder" | "rewrite" | "user_edit";
  flags: string[];
  status: "pending" | "accepted" | "rejected";
  acknowledged?: boolean;
}

export interface ResumeVersion {
  id: string;
  resume_id: string;
  job_id: string | null;
  label: string;
  status: "draft" | "approved";
  changes: ResumeChange[];
  analysis: {
    matched_required?: string[];
    missing_required?: string[];
    matched_preferred?: string[];
    missing_preferred?: string[];
    note?: string;
    ai_note?: string | null;
    summary_of_fit?: string | null;
  };
  generated_by: string;
  created_at: string;
  approved_at: string | null;
  downloadable: boolean;
}

export type AppStatus =
  | "discovered" | "shortlisted" | "preparing" | "awaiting_approval" | "approved" | "submitted"
  | "assessment" | "interview" | "offer" | "rejected" | "withdrawn" | "failed";

export interface Answer {
  id: string;
  question: string;
  answer: string | null;
  confidence: "verified" | "inferred" | "unknown";
  needs_review: boolean;
  source: string | null;
  edited_by_user: boolean;
}

export interface AppEvent {
  id: string;
  kind: string;
  from_status: string | null;
  to_status: string | null;
  actor: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface Approval {
  id: string;
  application_id: string;
  status: "pending" | "approved" | "rejected" | "expired";
  requested_by: string;
  created_at: string;
  decided_at: string | null;
  decided_via: string | null;
  decision_note: string | null;
  scheduled_for: string | null;
  job_title: string | null;
  company: string | null;
}

export interface InterviewItem {
  id: string;
  stage: string;
  scheduled_at: string | null;
  mode: string | null;
  location_or_link: string | null;
  notes: string | null;
  outcome: string | null;
}

export interface Application {
  id: string;
  status: AppStatus;
  status_changed_at: string;
  submission_state: "not_submitted" | "attempted" | "acknowledged" | "failed" | "unknown";
  submission_method: string | null;
  submission_reference: string | null;
  submitted_at: string | null;
  acknowledged_at: string | null;
  follow_up_at: string | null;
  resume_id: string | null;
  resume_version_id: string | null;
  created_at: string;
  created_by: string;
  job: JobCard;
  allowed_next: AppStatus[];
  pending_review: number;
}

export interface ApplicationDetail extends Application {
  cover_letter: string | null;
  cover_letter_generated_by: string | null;
  answers: Answer[];
  events: AppEvent[];
  approvals: Approval[];
  interviews: InterviewItem[];
  readiness: string[];
}

export interface Dashboard {
  metrics: {
    jobs_discovered: number;
    matching_jobs: number;
    match_threshold: number;
    applications_submitted: number;
    awaiting_approval: number;
    interviews_scheduled: number;
    recruiter_responses: number;
    rejections: number;
    offers: number;
    pending_actions: number;
    sources: number;
  };
  pipeline: { status: AppStatus; count: number }[];
  activity: { date: string; discovered: number; applications: number; submitted: number }[];
  recommended: {
    job_id: string; title: string; company: string; location: string | null; work_mode: string | null;
    score: number; eligibility: string; matched_skills: string[]; applied: boolean;
  }[];
  insights: {
    skill_demand: { jobs_analysed: number; top_skills: { skill: string; jobs: number; share: number; have: boolean }[]; gaps: string[] };
    companies_hiring: { company: string; matching_jobs: number }[];
    resume: { id: string; name: string; score: number | null; top_issues: QualityIssue[] } | null;
    next_actions: { title: string; detail: string; href: string }[];
  };
  timeline: {
    id: string; kind: string; to_status: string | null; from_status: string | null; actor: string;
    detail: Record<string, unknown>; at: string; application_id: string; job_title: string; company: string;
  }[];
  upcoming: {
    interviews: { id: string; stage: string; scheduled_at: string; application_id: string; company: string | null }[];
    follow_ups: { application_id: string; company: string; title: string; due: string }[];
    approvals: { id: string; application_id: string; title: string | null; company: string | null; created_at: string }[];
  };
}

export interface Analytics {
  range: { start: string; end: string; bucket: "day" | "week" };
  series: { period: string; discovered: number; created: number; submitted: number }[];
  totals: { jobs_discovered: number; applications: number; submitted: number; interviews: number; offers: number };
  conversion: { application_to_interview: number | null; interview_to_offer: number | null; response_rate: number | null };
  definitions: Record<string, string>;
  status_breakdown: Record<string, number>;
  top_companies: { company: string; applications: number }[];
  top_skills: { skill: string; jobs: number }[];
  resume_progress: { name: string; date: string; score: number | null }[];
  stage_durations_days: Record<string, number>;
}

export interface Notification {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  read_at: string | null;
  created_at: string;
}

export interface AutomationConfig {
  id: string;
  name: string;
  kind: "discovery" | "preparation";
  interval_minutes: number;
  filters: Record<string, unknown>;
  state: "active" | "paused" | "stopped";
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
}

export interface AutomationExecution {
  id: string;
  config_id: string | null;
  kind: string;
  trigger: string;
  status: "running" | "succeeded" | "failed" | "partial";
  started_at: string;
  finished_at: string | null;
  stats: Record<string, number>;
  log: { ts: string; level: string; event: string; [k: string]: unknown }[];
  error: string | null;
}

export interface Communication {
  id: string;
  application_id: string | null;
  channel: string;
  direction: "inbound" | "outbound" | "draft";
  sender: string | null;
  recipient: string | null;
  subject: string | null;
  snippet: string | null;
  body: string | null;
  category: string;
  classified_by: string;
  occurred_at: string;
  company: string | null;
  job_title: string | null;
}

export interface IntegrationInfo {
  provider: "gmail" | "whatsapp" | "ai";
  name: string;
  available: boolean;
  setup_hint: string | null;
  status: string;
  account: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  scopes: string[];
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools_used: string[];
  created_at: string;
}

export interface InterviewQuestion {
  id: string;
  category: string;
  question: string;
  rationale: string;
  provenance: "job_description" | "skill_template" | "standard";
  answer: string | null;
  feedback: {
    method: string;
    scores?: Record<string, number> | null;
    strengths?: string[];
    improvements?: string[];
    star?: Record<string, boolean>;
    improved_answer?: string | null;
    note?: string;
  } | null;
}

export interface InterviewSession {
  id: string;
  title: string;
  job_id: string | null;
  application_id: string | null;
  generated_by: string;
  status: string;
  created_at: string;
  progress: { answered: number; total: number };
  focus_areas: string[];
  questions: InterviewQuestion[];
}

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  token?: string | null;
}

export interface AiStatus {
  configured: boolean;
  provider: string | null;
  model: string | null;
}
