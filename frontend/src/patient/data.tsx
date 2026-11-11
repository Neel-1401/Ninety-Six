// Patient data layer (Agent D). All reads go through the shared api client and the shared
// query keys so SSE notices (scopes today/checkins/messages/followups/instructions) refetch them.
import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, isApiError } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type {
  InstructionsOut,
  PatientCheckInOut,
  PatientEpisodeOut,
  PatientFollowUpOut,
  PatientMessageOut,
  TodayOut,
} from "../lib/types";

const retry = (count: number, e: unknown) => !(isApiError(e) && e.status >= 400 && e.status < 500) && count < 2;

export function useMyEpisodes() {
  return useQuery({
    queryKey: qk.myEpisodes(),
    queryFn: () => api.get<PatientEpisodeOut[]>("/api/me/episodes"),
    retry,
  });
}

export function useToday(episodeId: number | null) {
  return useQuery({
    queryKey: qk.today(episodeId ?? -1),
    queryFn: () => api.get<TodayOut>(`/api/me/episodes/${episodeId}/today`),
    enabled: episodeId != null,
    retry,
    refetchInterval: 60_000,
  });
}

export function useInstructions(episodeId: number | null) {
  return useQuery({
    queryKey: qk.instructions(episodeId ?? -1),
    queryFn: () => api.get<InstructionsOut>(`/api/me/episodes/${episodeId}/instructions`),
    enabled: episodeId != null,
    retry,
  });
}

export function useMyCheckins(episodeId: number | null) {
  return useQuery({
    queryKey: qk.myCheckins(episodeId ?? -1),
    queryFn: () => api.get<PatientCheckInOut[]>(`/api/me/episodes/${episodeId}/checkins`),
    enabled: episodeId != null,
    retry,
  });
}

/** One check-in. While processing, poll as a fallback in case the live stream is down. */
export function useMyCheckin(checkinId: number | null) {
  return useQuery({
    queryKey: qk.myCheckin(checkinId ?? -1),
    queryFn: () => api.get<PatientCheckInOut>(`/api/me/checkins/${checkinId}`),
    enabled: checkinId != null,
    retry,
    refetchInterval: (q) => {
      const r = q.state.data?.receipt;
      if (!r || !r.received) return false;
      return r.agent_processing === "processing" || r.agent_processing === "not_started" ? 4_000 : false;
    },
  });
}

export function useMyMessages(episodeId: number | null) {
  return useQuery({
    queryKey: qk.myMessages(episodeId ?? -1),
    queryFn: () => api.get<PatientMessageOut[]>(`/api/me/episodes/${episodeId}/messages`),
    enabled: episodeId != null,
    retry,
  });
}

export function useMyFollowups(episodeId: number | null) {
  return useQuery({
    queryKey: qk.myFollowups(episodeId ?? -1),
    queryFn: () => api.get<PatientFollowUpOut[]>(`/api/me/episodes/${episodeId}/followups`),
    enabled: episodeId != null,
    retry,
  });
}

/** Demo mode is detected only by the backend answering /api/demo/state (404 otherwise). */
export function useDemoMode() {
  return useQuery({
    queryKey: ["clock", null, "demo-probe"],
    queryFn: async () => {
      try {
        await api.get<unknown>("/api/demo/state");
        return true;
      } catch {
        return false;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
}

// ------------------------------------------------------------------ selected episode
const STORAGE_KEY = "ns.patient.episode";

interface EpisodeCtx {
  episodes: PatientEpisodeOut[];
  episode: PatientEpisodeOut | null;
  episodeId: number | null;
  select: (id: number) => void;
}

const Ctx = createContext<EpisodeCtx>({ episodes: [], episode: null, episodeId: null, select: () => {} });

function readStored(): number | null {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

/** Picks the active episode by default (newest active, else newest), remembers a manual choice. */
export function EpisodeProvider({ episodes, children }: { episodes: PatientEpisodeOut[]; children: ReactNode }) {
  const [chosen, setChosen] = useState<number | null>(() => readStored());
  const fallback = useMemo(() => {
    const sorted = [...episodes].sort((a, b) => b.discharge_at.localeCompare(a.discharge_at));
    return sorted.find((e) => e.monitoring_active) ?? sorted[0] ?? null;
  }, [episodes]);
  const episode = episodes.find((e) => e.id === chosen) ?? fallback;

  useEffect(() => {
    if (chosen != null && !episodes.some((e) => e.id === chosen)) setChosen(null);
  }, [chosen, episodes]);

  const value: EpisodeCtx = {
    episodes,
    episode,
    episodeId: episode?.id ?? null,
    select: (id) => {
      setChosen(id);
      try {
        window.localStorage.setItem(STORAGE_KEY, String(id));
      } catch {
        /* storage unavailable: choice lasts for this session only */
      }
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useEpisode(): EpisodeCtx {
  return useContext(Ctx);
}

// ------------------------------------------------------------------ safe localStorage
export const store = {
  get<T>(key: string): T | null {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : null;
    } catch {
      return null;
    }
  },
  set(key: string, value: unknown) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode / quota: server draft still saves */
    }
  },
  remove(key: string) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  },
};

// ------------------------------------------------------------------ monitoring state
/** not_started: plan not yet activated by the doctor · active · ended: 96 h window over · cancelled */
export type MonitoringState = "not_started" | "active" | "ended" | "cancelled";

export function monitoringState(ep: Pick<PatientEpisodeOut, "status" | "monitoring_active">, windowEnded = false): MonitoringState {
  if (ep.status === "cancelled") return "cancelled";
  if (windowEnded || ep.status === "awaiting_closure_review" || ep.status === "closed") return "ended";
  if (ep.status === "draft" || ep.status === "awaiting_review") return "not_started";
  return ep.monitoring_active || ep.status === "active" ? "active" : "not_started";
}
