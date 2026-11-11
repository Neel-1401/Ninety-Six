// Shared simulated clock for the UI (lead-owned). Always display times relative to the
// SERVER's simulated now — never mix in the browser clock for scheduling semantics.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { qk } from "./queryKeys";
import type { ClockOut } from "./types";

export function useSimClock(enabled = true) {
  return useQuery({
    queryKey: qk.clock(),
    queryFn: () => api.get<ClockOut>("/api/clock"),
    enabled,
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

/** Hours between two ISO instants (b - a). */
export function hoursBetween(a: string, b: string): number {
  return (new Date(b).getTime() - new Date(a).getTime()) / 3_600_000;
}
