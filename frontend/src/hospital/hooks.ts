// Hospital data hooks (Agent C). All data comes from the backend via lib/api; SSE notices
// (lib/live) invalidate these by scope. Mutations also invalidate immediately on success.
import { useMutation, useQuery, useQueryClient, type Query, type QueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type {
  ActionOut,
  AuditEventOut,
  CheckInOut,
  EpisodeDetail,
  EpisodeRow,
  ExecutionOut,
  FactOut,
  FollowUpOut,
  MemoryOut,
  MessageOut,
  NoteOut,
  OverviewOut,
  PlanVersionOut,
  ReviewItemOut,
  RoutineOut,
  SampleOut,
  SettingsOut,
  UserOut,
} from "../lib/types";

/** Invalidate every cached query whose key[0] is one of `scopes` (and matches the episode). */
export function invalidateScopes(qc: QueryClient, scopes: string[], episodeId?: number | null) {
  const set = new Set(scopes);
  return qc.invalidateQueries({
    predicate: (q: Query) => {
      const [scope, ep] = q.queryKey as [string, number | null | undefined];
      if (!set.has(scope)) return false;
      return ep == null || episodeId == null || ep === episodeId;
    },
  });
}

const EPISODE_SCOPES = [
  "overview",
  "episodes",
  "episode",
  "plan",
  "actions",
  "approvals",
  "routine",
  "checkins",
  "messages",
  "followups",
  "memory",
  "activity",
  "reviews",
  "executions",
  "notes",
  "summary",
];

/** Generic mutation that refreshes everything about an episode afterwards. */
export function useEpisodeMutation<TVars, TOut>(
  episodeId: number | null | undefined,
  fn: (vars: TVars) => Promise<TOut>,
  scopes: string[] = EPISODE_SCOPES,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => invalidateScopes(qc, scopes, episodeId ?? null),
  });
}

// ------------------------------------------------------------------ queries
export const useOverview = () => useQuery({ queryKey: qk.overview(), queryFn: () => api.get<OverviewOut>("/api/overview") });

export const useEpisodes = (status?: string) =>
  useQuery({
    queryKey: qk.episodes(status ? { status } : undefined),
    queryFn: () => api.get<EpisodeRow[]>(`/api/episodes${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  });

export const useEpisode = (id: number) =>
  useQuery({ queryKey: qk.episode(id), queryFn: () => api.get<EpisodeDetail>(`/api/episodes/${id}`), enabled: id > 0 });

export const useCurrentPlan = (id: number, enabled = true) =>
  useQuery({
    queryKey: qk.plan(id),
    queryFn: () => api.get<PlanVersionOut>(`/api/episodes/${id}/plans/current`),
    enabled: enabled && id > 0,
    retry: false,
  });

export const usePlans = (id: number, enabled = true) =>
  useQuery({ queryKey: qk.plans(id), queryFn: () => api.get<PlanVersionOut[]>(`/api/episodes/${id}/plans`), enabled: enabled && id > 0 });

export const useEpisodeActions = (id: number) =>
  useQuery({ queryKey: qk.actions(id), queryFn: () => api.get<ActionOut[]>(`/api/episodes/${id}/actions`) });

export const useApprovals = (state: "pending" | "all" = "pending") =>
  useQuery({ queryKey: qk.approvals(state), queryFn: () => api.get<ActionOut[]>(`/api/approvals?state=${state}`) });

export const useCheckins = (id: number) =>
  useQuery({ queryKey: qk.checkins(id), queryFn: () => api.get<CheckInOut[]>(`/api/episodes/${id}/checkins`) });

export const useMessages = (id: number) =>
  useQuery({ queryKey: qk.messages(id), queryFn: () => api.get<MessageOut[]>(`/api/episodes/${id}/messages`) });

export const useFollowups = (id: number) =>
  useQuery({ queryKey: qk.followups(id), queryFn: () => api.get<FollowUpOut[]>(`/api/episodes/${id}/followups`) });

export const useMemory = (id: number) =>
  useQuery({ queryKey: qk.memory(id), queryFn: () => api.get<MemoryOut>(`/api/episodes/${id}/memory`) });

export const useActivity = (id: number) =>
  useQuery({ queryKey: qk.activity(id), queryFn: () => api.get<AuditEventOut[]>(`/api/episodes/${id}/activity`) });

export const useExecutions = (id: number) =>
  useQuery({ queryKey: qk.executions(id), queryFn: () => api.get<ExecutionOut[]>(`/api/episodes/${id}/executions`) });

export const useEpisodeReviews = (id: number) =>
  useQuery({ queryKey: qk.reviews(id, "all"), queryFn: () => api.get<ReviewItemOut[]>(`/api/episodes/${id}/review-items`) });

export const useNotes = (id: number) =>
  useQuery({ queryKey: qk.notes(id), queryFn: () => api.get<NoteOut[]>(`/api/episodes/${id}/notes`) });

export const useSummary = (id: number, enabled: boolean) =>
  useQuery({
    queryKey: qk.summary(id),
    queryFn: () => api.get<Record<string, unknown>>(`/api/episodes/${id}/summary`),
    enabled,
    retry: false,
  });

export const useSamples = () => useQuery({ queryKey: qk.samples(), queryFn: () => api.get<SampleOut[]>("/api/samples"), staleTime: 5 * 60_000 });

export const useStaff = (role?: "doctor" | "nurse") =>
  useQuery({
    queryKey: qk.staff(role),
    queryFn: () => api.get<UserOut[]>(`/api/staff${role ? `?role=${role}` : ""}`),
    staleTime: 5 * 60_000,
  });

export const useSettings = () => useQuery({ queryKey: qk.settings(), queryFn: () => api.get<SettingsOut>("/api/settings") });

// ------------------------------------------------------------------ mutation helpers
export const actionsApi = {
  approve: (a: ActionOut) => api.post<ActionOut>(`/api/actions/${a.id}/approve`, { payload_hash: a.payload_hash }),
  editApprove: (a: ActionOut, base: string, payload: Record<string, unknown>) =>
    api.post<ActionOut>(`/api/actions/${a.id}/edit-approve`, { base_payload_hash: base, payload }),
  reject: (a: ActionOut, reason: string) =>
    api.post<ActionOut>(`/api/actions/${a.id}/reject`, { base_payload_hash: a.payload_hash, reason }),
  retry: (a: ActionOut) => api.post<ActionOut>(`/api/actions/${a.id}/retry`),
  reconcile: (executionId: number, outcome: "success" | "failure", note: string) =>
    api.post<ExecutionOut>(`/api/executions/${executionId}/reconcile`, { outcome, note }),
};

export const reviewApi = {
  acknowledge: (id: number) => api.post<ReviewItemOut>(`/api/review-items/${id}/acknowledge`),
  assign: (id: number, userId: number) => api.post<ReviewItemOut>(`/api/review-items/${id}/assign`, { user_id: userId }),
  resolve: (id: number, note: string) => api.post<ReviewItemOut>(`/api/review-items/${id}/resolve`, { note }),
};

export const factsApi = {
  patch: (episodeId: number, factId: number, body: { value: Record<string, unknown>; label?: string; reason?: string }) =>
    api.patch<FactOut>(`/api/episodes/${episodeId}/facts/${factId}`, body),
  add: (episodeId: number, body: { category: string; label: string; value: Record<string, unknown> }) =>
    api.post<FactOut>(`/api/episodes/${episodeId}/facts`, body),
  reject: (episodeId: number, factId: number, reason: string) =>
    api.post<FactOut>(`/api/episodes/${episodeId}/facts/${factId}/reject`, { reason }),
};

export const routineApi = {
  revoke: (id: number, reason: string) => api.post<RoutineOut>(`/api/routine-authorisations/${id}/revoke`, { reason }),
};
