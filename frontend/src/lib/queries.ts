"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { toast } from "@/lib/toast";
import { api, ApiError } from "./api";
import type {
  AiStatus, Analytics, ApiToken, Application, ApplicationDetail, Approval, AutomationConfig, AutomationExecution,
  ChatMessage, Communication, Conversation, Dashboard, FullProfile, IntegrationInfo, InterviewSession, JobDetail,
  JobPage, JobSource, Me, Notification, ResumeDetail, ResumeSummary, ResumeVersion, Settings,
} from "./types";

export const keys = {
  me: ["me"] as const,
  profile: ["profile"] as const,
  settings: ["settings"] as const,
  dashboard: ["dashboard"] as const,
  jobs: (q: object) => ["jobs", q] as const,
  job: (id: string) => ["job", id] as const,
  sources: ["job-sources"] as const,
  resumes: ["resumes"] as const,
  resume: (id: string) => ["resume", id] as const,
  versions: (jobId?: string) => ["resume-versions", jobId ?? "all"] as const,
  version: (id: string) => ["resume-version", id] as const,
  applications: ["applications"] as const,
  application: (id: string) => ["application", id] as const,
  approvals: ["approvals"] as const,
  notifications: ["notifications"] as const,
  analytics: (range: object) => ["analytics", range] as const,
  automations: ["automations"] as const,
  executions: ["executions"] as const,
  comms: (f: object) => ["communications", f] as const,
  integrations: ["integrations"] as const,
  conversations: ["conversations"] as const,
  messages: (id: string) => ["messages", id] as const,
  interviews: ["interview-sessions"] as const,
  interview: (id: string) => ["interview-session", id] as const,
  tokens: ["tokens"] as const,
  aiStatus: ["ai-status"] as const,
  upcoming: ["upcoming-interviews"] as const,
};

export const useMe = () =>
  useQuery({ queryKey: keys.me, queryFn: () => api.get<Me>("/auth/me"), retry: false, staleTime: 60_000 });
export const useProfile = () => useQuery({ queryKey: keys.profile, queryFn: () => api.get<FullProfile>("/profile") });
export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: () => api.get<Settings>("/settings") });
export const useDashboard = () =>
  useQuery({ queryKey: keys.dashboard, queryFn: () => api.get<Dashboard>("/dashboard"), refetchInterval: 60_000 });
export const useAiStatus = () =>
  useQuery({ queryKey: keys.aiStatus, queryFn: () => api.get<AiStatus>("/ai/status"), staleTime: 300_000 });

export type JobFilters = {
  q?: string; location?: string; work_modes?: string[]; employment_types?: string[]; experience_levels?: string[];
  min_salary_lpa?: number; skills?: string[]; posted_within_days?: number; min_score?: number; eligibility?: string;
  state?: string; sort?: string; page?: number; page_size?: number;
};
export const useJobs = (filters: JobFilters) =>
  useQuery({
    queryKey: keys.jobs(filters),
    queryFn: () => api.get<JobPage>("/jobs", filters),
    placeholderData: keepPreviousData,
  });
export const useJob = (id: string | null) =>
  useQuery({ queryKey: keys.job(id ?? ""), queryFn: () => api.get<JobDetail>(`/jobs/${id}`), enabled: !!id });
export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: () => api.get<JobSource[]>("/job-sources") });

export const useResumes = () => useQuery({ queryKey: keys.resumes, queryFn: () => api.get<ResumeSummary[]>("/resumes") });
export const useResume = (id: string | null) =>
  useQuery({ queryKey: keys.resume(id ?? ""), queryFn: () => api.get<ResumeDetail>(`/resumes/${id}`), enabled: !!id });
export const useVersions = (jobId?: string) =>
  useQuery({ queryKey: keys.versions(jobId), queryFn: () => api.get<ResumeVersion[]>("/resume-versions", { job_id: jobId }) });
export const useVersion = (id: string | null) =>
  useQuery({ queryKey: keys.version(id ?? ""), queryFn: () => api.get<ResumeVersion>(`/resume-versions/${id}`), enabled: !!id });

export const useApplications = () =>
  useQuery({ queryKey: keys.applications, queryFn: () => api.get<Application[]>("/applications") });
export const useApplication = (id: string | null) =>
  useQuery({ queryKey: keys.application(id ?? ""), queryFn: () => api.get<ApplicationDetail>(`/applications/${id}`), enabled: !!id });
export const useApprovals = () =>
  useQuery({ queryKey: keys.approvals, queryFn: () => api.get<Approval[]>("/approvals", { status: "pending" }) });

export const useNotifications = () =>
  useQuery({
    queryKey: keys.notifications,
    queryFn: () => api.get<{ unread: number; items: Notification[] }>("/notifications"),
    refetchInterval: 45_000,
  });
export const useAnalytics = (range: { start: string; end: string }) =>
  useQuery({ queryKey: keys.analytics(range), queryFn: () => api.get<Analytics>("/analytics", range), placeholderData: keepPreviousData });

export const useAutomations = () =>
  useQuery({ queryKey: keys.automations, queryFn: () => api.get<AutomationConfig[]>("/automations") });
export const useExecutions = () =>
  useQuery({
    queryKey: keys.executions,
    queryFn: () => api.get<AutomationExecution[]>("/automations/executions/recent"),
    refetchInterval: 15_000,
  });
export const useCommunications = (f: { category?: string; application_id?: string }) =>
  useQuery({ queryKey: keys.comms(f), queryFn: () => api.get<Communication[]>("/communications", f) });
export const useIntegrations = () =>
  useQuery({ queryKey: keys.integrations, queryFn: () => api.get<IntegrationInfo[]>("/integrations") });
export const useConversations = () =>
  useQuery({ queryKey: keys.conversations, queryFn: () => api.get<Conversation[]>("/assistant/conversations") });
export const useMessages = (id: string | null) =>
  useQuery({
    queryKey: keys.messages(id ?? ""),
    queryFn: () => api.get<ChatMessage[]>(`/assistant/conversations/${id}/messages`),
    enabled: !!id,
  });
export const useInterviewSessions = () =>
  useQuery({ queryKey: keys.interviews, queryFn: () => api.get<InterviewSession[]>("/interview/sessions") });
export const useInterviewSession = (id: string | null) =>
  useQuery({ queryKey: keys.interview(id ?? ""), queryFn: () => api.get<InterviewSession>(`/interview/sessions/${id}`), enabled: !!id });
export const useTokens = () => useQuery({ queryKey: keys.tokens, queryFn: () => api.get<ApiToken[]>("/auth/tokens") });

/** Mutation helper: shows API errors as toasts and invalidates related queries on success. */
export function useAction<TVars, TResult = unknown>(
  fn: (vars: TVars) => Promise<TResult>,
  opts: { invalidate?: QueryKey[]; success?: string | ((r: TResult) => string | null); onSuccess?: (r: TResult, v: TVars) => void; silentError?: boolean } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (result, vars) => {
      await Promise.all((opts.invalidate ?? []).map((k) => qc.invalidateQueries({ queryKey: k })));
      const msg = typeof opts.success === "function" ? opts.success(result) : opts.success;
      if (msg) toast.success(msg);
      opts.onSuccess?.(result, vars);
    },
    onError: (err) => {
      if (!opts.silentError) toast.error(err instanceof ApiError ? err.message : "Something went wrong.");
    },
  });
}
