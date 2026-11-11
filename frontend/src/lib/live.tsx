// Live updates over SSE (lead-owned). SSE is a hint to refetch; the API is the source of truth.
// - One EventSource per signed-in tab.
// - Each notice invalidates queries whose key[0] is one of notice.scopes and whose key[1] is
//   null or the notice's episode id.
// - After any reconnect we refetch everything (missed events are also replayed by the server
//   via Last-Event-ID, which the browser sends automatically).
// - Queries never overwrite unsaved form state: forms must keep local draft state (see ui docs).
import { useQueryClient, type Query } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { LiveNotice } from "./types";

export type LiveStatus = "off" | "connecting" | "open" | "reconnecting";

interface LiveCtx {
  status: LiveStatus;
  lastEventAt: number | null;
  lastNotice: LiveNotice | null;
}

const Ctx = createContext<LiveCtx>({ status: "off", lastEventAt: null, lastNotice: null });

export function LiveProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("off");
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [lastNotice, setLastNotice] = useState<LiveNotice | null>(null);
  const hadError = useRef(false);

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") {
      setStatus("off");
      return;
    }
    setStatus("connecting");
    const es = new EventSource("/api/stream", { withCredentials: true });

    es.onopen = () => {
      setStatus("open");
      if (hadError.current) {
        // Recovered after a drop: refetch current state everywhere.
        hadError.current = false;
        void qc.invalidateQueries();
      }
    };
    es.onerror = () => {
      hadError.current = true;
      setStatus("reconnecting"); // EventSource retries automatically
    };
    es.addEventListener("update", (ev) => {
      let n: LiveNotice;
      try {
        n = JSON.parse((ev as MessageEvent).data) as LiveNotice;
      } catch {
        return;
      }
      setLastEventAt(Date.now());
      setLastNotice(n);
      const scopes = new Set(n.scopes ?? []);
      void qc.invalidateQueries({
        predicate: (q: Query) => {
          const [scope, episodeId] = q.queryKey as [string, number | null | undefined];
          if (!scopes.has(scope)) return false;
          return episodeId == null || n.episode_id == null || episodeId === n.episode_id;
        },
      });
    });
    return () => {
      es.close();
      setStatus("off");
    };
  }, [enabled, qc]);

  return <Ctx.Provider value={{ status, lastEventAt, lastNotice }}>{children}</Ctx.Provider>;
}

export function useLive(): LiveCtx {
  return useContext(Ctx);
}
