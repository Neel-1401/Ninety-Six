// Hospital shell (Agent C): left nav on wide screens, disclosure menu under 1024px,
// top bar with simulated time, signed-in user, live-update status and sign out.
import { ClipboardCheck, LayoutDashboard, LogOut, Menu, Settings, Users, X } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { useLogout, useMe } from "../lib/auth";
import { useSimClock } from "../lib/clock";
import { fmtDateTime } from "../lib/format";
import { useLive } from "../lib/live";
import { cn } from "../ui";
import { useApprovals } from "./hooks";

const NAV = [
  { to: "/hospital", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/hospital/patients", label: "Patients", icon: Users, end: false },
  { to: "/hospital/approvals", label: "Approvals", icon: ClipboardCheck, end: false },
  { to: "/hospital/settings", label: "Settings", icon: Settings, end: false },
];

export function HospitalLayout() {
  const { data: me } = useMe();
  const loc = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => setMenuOpen(false), [loc.pathname]);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[232px_1fr]">
      {/* Sidebar (desktop) */}
      <aside className="hidden border-r border-line bg-surface lg:flex lg:flex-col">
        <Brand />
        <nav aria-label="Hospital" className="flex flex-col gap-1 px-3 py-2">
          <NavItems />
        </nav>
        <div className="mt-auto border-t border-line px-4 py-3 text-xs text-muted">
          Prototype · fictional patients only. Not for clinical use.
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <TopBar onMenu={() => setMenuOpen((v) => !v)} menuOpen={menuOpen} role={me?.user.role} />
        {menuOpen && (
          <nav id="hospital-mobile-nav" aria-label="Hospital" className="flex flex-col gap-1 border-b border-line bg-surface px-3 py-2 lg:hidden">
            <NavItems />
          </nav>
        )}
        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-5 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-4 py-4">
      <span aria-hidden className="inline-flex size-8 items-center justify-center rounded-md bg-primary text-sm font-bold text-white">
        96
      </span>
      <span className="leading-tight">
        <span className="block font-bold">Ninety-Six</span>
        <span className="block text-xs text-muted">Discharge coordination</span>
      </span>
    </div>
  );
}

function NavItems() {
  const { data: pending } = useApprovals("pending");
  const n = pending?.length ?? 0;
  return (
    <>
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cn(
              "flex min-h-10 items-center gap-2.5 rounded-md px-3 text-[15px] font-bold",
              isActive ? "bg-primary-soft text-primary-ink" : "text-muted hover:bg-sunken hover:text-ink",
            )
          }
        >
          <Icon aria-hidden className="size-4.5 shrink-0" />
          <span className="flex-1">{label}</span>
          {label === "Approvals" && n > 0 && (
            <span className="rounded-full bg-attention px-2 text-xs font-bold text-attention-ink" aria-hidden>
              {n}
            </span>
          )}
        </NavLink>
      ))}
      <span role="status" className="sr-only">
        {n === 0 ? "No pending approvals" : `${n} pending approval${n === 1 ? "" : "s"}`}
      </span>
    </>
  );
}

function TopBar({ onMenu, menuOpen, role }: { onMenu: () => void; menuOpen: boolean; role?: string }) {
  const { data: me } = useMe();
  const { data: clock } = useSimClock();
  const live = useLive();
  const logout = useLogout();
  const navigate = useNavigate();
  const liveText =
    live.status === "open" ? "Live updates on" : live.status === "reconnecting" ? "Reconnecting…" : live.status === "connecting" ? "Connecting…" : "Live updates off";
  return (
    <header className="sticky top-0 z-30 flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-surface/95 px-4 py-2 backdrop-blur sm:px-6">
      <button
        type="button"
        onClick={onMenu}
        aria-expanded={menuOpen}
        aria-controls="hospital-mobile-nav"
        className="inline-flex min-h-10 items-center gap-2 rounded-md px-2 font-bold text-ink hover:bg-sunken lg:hidden cursor-pointer"
      >
        {menuOpen ? <X aria-hidden className="size-5" /> : <Menu aria-hidden className="size-5" />}
        Menu
      </button>
      <p className="text-sm">
        <span className="text-muted">{clock && clock.sim_offset_hours > 0 ? "Simulated time" : "Time"} · </span>
        <span className="font-bold">{clock ? fmtDateTime(clock.now) : "—"}</span>
        <span className="text-muted"> IST</span>
        {clock && clock.sim_offset_hours > 0 && (
          <span className="ml-1 text-muted">(+{Math.round(clock.sim_offset_hours * 10) / 10} h)</span>
        )}
      </p>
      <p className="flex items-center gap-1.5 text-sm text-muted" aria-live="polite">
        <span
          aria-hidden
          className={cn(
            "inline-block size-2 rounded-full",
            live.status === "open" ? "bg-primary" : live.status === "reconnecting" ? "bg-attention-strong" : "bg-line-strong",
          )}
        />
        {liveText}
      </p>
      <div className="ml-auto flex items-center gap-3">
        {me && (
          <p className="text-right text-sm leading-tight">
            <span className="block font-bold">{me.user.display_name}</span>
            <span className="block text-muted capitalize">
              {role}
              {me.user.specialty ? ` · ${me.user.specialty}` : ""}
              {me.session_via === "demo" ? " · demo session" : ""}
            </span>
          </p>
        )}
        <button
          type="button"
          onClick={() => logout.mutate(undefined, { onSettled: () => navigate("/hospital/login") })}
          className="inline-flex min-h-10 items-center gap-2 rounded-md border border-line-strong px-3 text-sm font-bold hover:bg-sunken cursor-pointer"
        >
          <LogOut aria-hidden className="size-4" />
          Sign out
        </button>
      </div>
    </header>
  );
}
