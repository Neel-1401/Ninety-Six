// TanStack Query keys (lead-owned). The FIRST element is always an SSE scope name
// (CONTRACTS §7) so live notices can invalidate by scope; the SECOND, when present, is the
// episode id. Keep to this shape so <LiveProvider> invalidation works.

export const qk = {
  me: () => ["me"] as const,
  clock: () => ["clock"] as const,
  settings: () => ["settings"] as const,
  overview: () => ["overview"] as const,
  episodes: (filter?: Record<string, string>) => ["episodes", null, filter ?? {}] as const,
  episode: (id: number) => ["episode", id] as const,
  plan: (id: number) => ["plan", id, "current"] as const,
  plans: (id: number) => ["plan", id, "all"] as const,
  actions: (id: number) => ["actions", id] as const,
  approvals: (state = "pending") => ["approvals", null, state] as const,
  action: (actionId: number) => ["actions", null, "one", actionId] as const,
  checkins: (id: number) => ["checkins", id] as const,
  messages: (id: number) => ["messages", id] as const,
  followups: (id: number) => ["followups", id] as const,
  memory: (id: number) => ["memory", id] as const,
  activity: (id: number) => ["activity", id] as const,
  reviews: (id: number | null, status = "active") => ["reviews", id, status] as const,
  executions: (id: number) => ["executions", id] as const,
  notes: (id: number) => ["notes", id] as const,
  summary: (id: number) => ["summary", id] as const,
  samples: () => ["settings", null, "samples"] as const,
  staff: (role?: string) => ["settings", null, "staff", role ?? "all"] as const,
  demoState: () => ["clock", null, "demo-state"] as const,
  demoOutbox: () => ["messages", null, "demo-outbox"] as const,
  // patient
  myEpisodes: () => ["today", null, "my-episodes"] as const,
  today: (id: number) => ["today", id] as const,
  instructions: (id: number) => ["instructions", id] as const,
  myCheckins: (id: number) => ["checkins", id, "mine"] as const,
  myCheckin: (checkinId: number) => ["checkins", null, "mine-one", checkinId] as const,
  myMessages: (id: number) => ["messages", id, "mine"] as const,
  myFollowups: (id: number) => ["followups", id, "mine"] as const,
};
