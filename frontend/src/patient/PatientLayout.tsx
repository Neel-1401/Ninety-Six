// Signed-in patient shell (Agent D): header with sign-out, episode switcher, bottom navigation.
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, FileText, House, LogOut, MessageSquare } from "lucide-react";
import { useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { isApiError } from "../lib/api";
import { useLogout } from "../lib/auth";
import { useLive } from "../lib/live";
import { fmtDate } from "../lib/format";
import { Button, cn, EmptyState, Select } from "../ui";
import { EpisodeProvider, useEpisode, useMyEpisodes } from "./data";
import { LoadError, LoadingBlock } from "./parts";

const NAV = [
  { to: "/p", label: "Today", icon: House, end: true },
  { to: "/p/instructions", label: "My Instructions", icon: FileText, end: false },
  { to: "/p/messages", label: "Messages", icon: MessageSquare, end: false },
  { to: "/p/follow-up", label: "Follow-up", icon: CalendarClock, end: false },
];

/** If any patient query fails with 401 (session expired / signed out elsewhere), go to sign-in. */
function useSessionExpiryRedirect() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  useEffect(() => {
    return qc.getQueryCache().subscribe((ev) => {
      const err = ev.query.state.error;
      if (ev.type === "updated" && isApiError(err) && err.status === 401) {
        qc.clear();
        navigate("/p/sign-in", { replace: true, state: { expired: true } });
      }
    });
  }, [qc, navigate]);
}

function SignOutButton() {
  const logout = useLogout();
  const navigate = useNavigate();
  return (
    <Button
      variant="secondary"
      size="md"
      className="min-h-11 shrink-0 whitespace-nowrap"
      loading={logout.isPending}
      icon={<LogOut aria-hidden className="size-4" />}
      onClick={() =>
        logout.mutate(undefined, {
          onSettled: () => {
            try {
              window.localStorage.removeItem("ns.patient.episode");
            } catch {
              /* ignore */
            }
            navigate("/p/sign-in", { replace: true });
          },
        })
      }
    >
      Sign out
    </Button>
  );
}

function Shell() {
  const { episodes, episode, select } = useEpisode();
  const live = useLive();
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur supports-[backdrop-filter]:bg-surface/85">
        <div className="mx-auto flex w-full max-w-xl items-center justify-between gap-3 px-4 py-2.5">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-bold text-primary-ink">
              <span aria-hidden className="inline-flex size-7 items-center justify-center rounded-md bg-primary text-xs text-white">
                96
              </span>
              Ninety-Six
            </p>
            <p className="truncate text-sm text-muted">{episode?.hospital_name ?? "Your hospital"}</p>
          </div>
          <SignOutButton />
        </div>
        <p className="mx-auto w-full max-w-xl px-4 pb-2 text-xs text-muted">
          Using a shared phone? Sign out when you're done.
        </p>
      </header>

      <main id="main" className="mx-auto w-full max-w-xl flex-1 px-4 pb-28 pt-5">
        {live.status === "reconnecting" && (
          <p role="status" className="mb-4 text-sm text-muted">
            Reconnecting for live updates… what you see may be a few moments old.
          </p>
        )}
        {episodes.length > 1 && (
          <div className="mb-5">
            <label htmlFor="episode-switch" className="text-sm font-bold">
              Hospital stay
            </label>
            <Select
              id="episode-switch"
              className="mt-1 min-h-12"
              value={episode?.id ?? ""}
              onChange={(e) => select(Number(e.target.value))}
            >
              {episodes.map((e) => (
                <option key={e.id} value={e.id}>
                  Discharged {fmtDate(e.discharge_at)} {e.monitoring_active ? "(monitoring now)" : ""}
                </option>
              ))}
            </Select>
          </div>
        )}
        <Outlet />
      </main>

      <nav
        aria-label="Patient sections"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-surface pb-[env(safe-area-inset-bottom)]"
      >
        <ul className="mx-auto grid w-full max-w-xl grid-cols-4">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "flex min-h-16 flex-col items-center justify-center gap-1 px-1 text-center text-[13px] font-bold leading-tight",
                    isActive ? "text-primary-ink" : "text-muted hover:text-ink",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <span
                      className={cn(
                        "inline-flex h-7 w-12 items-center justify-center rounded-full transition-colors duration-150",
                        isActive && "bg-primary-soft",
                      )}
                    >
                      <Icon aria-hidden className="size-5" />
                    </span>
                    {label}
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

export function PatientLayout() {
  useSessionExpiryRedirect();
  const q = useMyEpisodes();
  if (q.isLoading) {
    return (
      <main id="main" className="mx-auto w-full max-w-xl px-4 pt-8">
        <LoadingBlock />
      </main>
    );
  }
  if (q.isError && !(isApiError(q.error) && q.error.status === 401)) {
    return (
      <main id="main" className="mx-auto w-full max-w-xl px-4 pt-8">
        <LoadError error={q.error} onRetry={() => void q.refetch()} what="your care plan" />
      </main>
    );
  }
  const episodes = q.data ?? [];
  if (episodes.length === 0) {
    return (
      <main id="main" className="mx-auto w-full max-w-xl px-4 pt-8">
        <EmptyState title="No follow-up plan yet">
          Your hospital hasn't shared a recovery plan with this account yet. If you were recently discharged, please
          contact the hospital.
        </EmptyState>
        <div className="mt-6">
          <SignOutButton />
        </div>
      </main>
    );
  }
  return (
    <EpisodeProvider episodes={episodes}>
      <Shell />
    </EpisodeProvider>
  );
}
