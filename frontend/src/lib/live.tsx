// Live updates over SSE (lead-owned). SSE is a hint to refetch; the API is the source of truth.
// - One EventSource per signed-in tab.
// - Each notice invalidates queries whose key[0] is one of notice.scopes and whose key[1] is
//   null or the notice's episode id.
// - Browsers give up for good when a reconnect gets an HTTP error (e.g. proxy 500 while the
//   backend restarts, or 401). We therefore recreate the EventSource ourselves with backoff,
//   passing ?last_event_id so the server replays missed notices, and refetch everything after
//   any recovery. On 401-style failure we recheck the session.
// - Queries never overwrite unsaved form state: forms must keep local draft state.
import { useQueryClient, type Query } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { qk } from "./queryKeys";
import type { LiveNotice } from "./types";

export type LiveStatus = "off" | "connecting" | "open" | "reconnecting";

interface LiveCtx {
  status: LiveStatus;
  lastEventAt: number | null;
  lastNotice: LiveNotice | null;
  /** Force a fresh connection (e.g. after a demo reset or sign-in change). */
  reconnect: () => void;
}

const Ctx = createContext<LiveCtx>({ status: "off", lastEventAt: null, lastNotice: null, reconnect: () => {} });

export function LiveProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("off");
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [lastNotice, setLastNotice] = useState<LiveNotice | null>(null);
  const [generation, setGeneration] = useState(0);
  const lastId = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") {
      setStatus("off");
      return;
    }
    let es: EventSource | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let disposed = false;
    let recovering = false;

    const connect = () => {
      if (disposed) return;
      setStatus(attempt === 0 ? "connecting" : "reconnecting");
      const url = lastId.current != null ? `/api/stream?last_event_id=${lastId.current}` : "/api/stream";
      es = new EventSource(url, { withCredentials: true });

      es.onopen = () => {
        setStatus("open");
        if (recovering || attempt > 0) void qc.invalidateQueries(); // refetch current state
        recovering = false;
        attempt = 0;
      };
      es.onerror = () => {
        recovering = true;
        if (!es || es.readyState !== EventSource.CLOSED) {
          setStatus("reconnecting"); // browser is retrying by itself
          return;
        }
        // Browser gave up (HTTP error). Recheck the session, then retry with backoff.
        es.close();
        setStatus("reconnecting");
        void qc.invalidateQueries({ queryKey: qk.me() });
        attempt += 1;
        const delay = Math.min(15_000, 1000 * 2 ** Math.min(attempt, 4));
        timer = window.setTimeout(connect, delay);
      };
      es.addEventListener("update", (ev) => {
        let n: LiveNotice;
        try {
          n = JSON.parse((ev as MessageEvent).data) as LiveNotice;
        } catch {
          return;
        }
        if (typeof n.id === "number") lastId.current = n.id;
        setLastEventAt(Date.now());
        setLastNotice(n);
        if (n.kind === "demo.reset") {
          lastId.current = null;
          void qc.invalidateQueries();
          return;
        }
        const scopes = new Set(n.scopes ?? []);
        void qc.invalidateQueries({
          predicate: (q: Query) => {
            const [scope, episodeId] = q.queryKey as [string, number | null | undefined];
            if (!scopes.has(scope)) return false;
            return episodeId == null || n.episode_id == null || episodeId === n.episode_id;
          },
        });
      });
    };

    connect();
    return () => {
      disposed = true;
      if (timer) window.clearTimeout(timer);
      es?.close();
      setStatus("off");
    };
  }, [enabled, qc, generation]);

  const reconnect = () => {
    lastId.current = null;
    setGeneration((g) => g + 1);
  };

  return <Ctx.Provider value={{ status, lastEventAt, lastNotice, reconnect }}>{children}</Ctx.Provider>;
}

export function useLive(): LiveCtx {
  return useContext(Ctx);
}
