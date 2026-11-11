// Session helpers (lead-owned). The server decides who you are; these only read /api/auth/me.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { api, isApiError } from "./api";
import { qk } from "./queryKeys";
import type { MeOut, Role } from "./types";

export function useMe() {
  return useQuery({
    queryKey: qk.me(),
    queryFn: async () => {
      try {
        return await api.get<MeOut>("/api/auth/me");
      } catch (e) {
        if (isApiError(e) && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: (count, e) => !(isApiError(e) && e.status < 500) && count < 2,
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/api/auth/logout"),
    onSettled: () => {
      qc.clear(); // drop every cached record on sign-out (shared devices)
      qc.setQueryData(qk.me(), null);
    },
  });
}

/** Call after any sign-in (staff login, OTP verify, demo login-as). */
export function useAfterSignIn() {
  const qc = useQueryClient();
  return (me: MeOut) => {
    qc.clear();
    qc.setQueryData(qk.me(), me);
  };
}

export function homePathFor(role: Role | undefined | null): string {
  if (role === "patient") return "/p";
  if (role === "doctor" || role === "nurse") return "/hospital";
  return "/";
}

/** Route guard. UI convenience only — the backend enforces every permission. */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { data: me, isLoading } = useMe();
  const loc = useLocation();
  if (isLoading) {
    return (
      <div role="status" className="p-8 text-muted">
        Checking your session…
      </div>
    );
  }
  if (!me) {
    const to = roles.includes("patient") ? "/p/sign-in" : "/hospital/login";
    return <Navigate to={to} replace state={{ from: loc.pathname }} />;
  }
  if (!roles.includes(me.user.role)) return <Navigate to={homePathFor(me.user.role)} replace />;
  return <>{children}</>;
}
