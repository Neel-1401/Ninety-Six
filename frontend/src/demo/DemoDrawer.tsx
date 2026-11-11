// Demo controls (Agent C). Renders NOTHING unless the backend answers GET /api/demo/state
// (demo mode only — production returns 404). Everything goes through real backend endpoints.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { FastForward, FlaskConical, RotateCcw, Send, UserRoundCog, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { api, errorMessage, isApiError } from "../lib/api";
import { homePathFor, useAfterSignIn, useMe } from "../lib/auth";
import { fmtDateTime } from "../lib/format";
import { qk } from "../lib/queryKeys";
import type { DemoAdvanceOut, DemoOutboxItem, DemoState, EpisodeRow, MeOut, PatientCheckInOut, SampleOut } from "../lib/types";
import { Banner, Button, ModeBadge, Select, StatusBadge, TextInput, cn, useToast } from "../ui";

export default function DemoDrawer() {
  const state = useQuery({
    queryKey: qk.demoState(),
    queryFn: () => api.get<DemoState>("/api/demo/state"),
    retry: false,
    staleTime: 10_000,
  });
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (open) headingRef.current?.focus();
  }, [open]);

  if (state.isError && isApiError(state.error) && state.error.status === 404) return null;
  if (!state.data) return null;

  const onPatient = loc.pathname.startsWith("/p");
  const close = () => {
    setOpen(false);
    toggleRef.current?.focus();
  };

  return (
    <>
      <button
        ref={toggleRef}
        type="button"
        aria-expanded={open}
        aria-controls="demo-drawer"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "fixed left-3 z-40 inline-flex min-h-10 items-center gap-2 rounded-full border border-attention-strong bg-attention px-3 text-sm font-bold text-attention-ink shadow-[var(--shadow-pop)] cursor-pointer",
          onPatient ? "bottom-20 sm:bottom-3" : "bottom-3",
        )}
      >
        <FlaskConical aria-hidden className="size-4" />
        Demo controls
      </button>
      <AnimatePresence>
        {open && (
          <motion.aside
            id="demo-drawer"
            role="region"
            aria-labelledby="demo-drawer-title"
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 16, transition: { duration: 0.15, ease: "easeIn" } }}
            transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
            onKeyDown={(e) => e.key === "Escape" && close()}
            className="fixed inset-x-2 bottom-2 z-50 flex max-h-[85dvh] flex-col rounded-lg border border-attention-line bg-surface shadow-[var(--shadow-pop)] sm:inset-x-auto sm:top-2 sm:right-2 sm:bottom-2 sm:max-h-none sm:w-[400px]"
          >
            <header className="flex items-start justify-between gap-2 border-b border-attention-line bg-attention px-4 py-3">
              <div>
                <h2 ref={headingRef} tabIndex={-1} id="demo-drawer-title" className="font-bold text-attention-ink">
                  Demo controls
                </h2>
                <p className="text-sm text-attention-ink">Not available in production · fictional data only</p>
              </div>
              <button type="button" onClick={close} aria-label="Close demo controls" className="inline-flex size-9 items-center justify-center rounded-md text-attention-ink hover:bg-attention-line/40 cursor-pointer">
                <X aria-hidden className="size-5" />
              </button>
            </header>
            <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
              <DrawerBody state={state.data} onNavigate={() => setOpen(false)} />
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <h3 className="mb-2 text-sm font-bold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </section>
  );
}

function DrawerBody({ state, onNavigate }: { state: DemoState; onNavigate: () => void }) {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const afterSignIn = useAfterSignIn();
  const toast = useToast();
  const signedIn = !!me;
  const isStaff = me?.user.role === "doctor" || me?.user.role === "nurse";

  // ---- clock
  const [advanceResult, setAdvanceResult] = useState<DemoAdvanceOut | null>(null);
  const advance = useMutation({
    mutationFn: (hours: 6 | 24) => api.post<DemoAdvanceOut>("/api/demo/advance", { hours }),
    onSuccess: (r) => {
      setAdvanceResult(r);
      void qc.invalidateQueries();
    },
  });

  // ---- role switch
  const loginAs = useMutation({
    mutationFn: (userId: number) => api.post<MeOut>("/api/demo/login-as", { user_id: userId }),
    onSuccess: (m) => {
      afterSignIn(m);
      toast({ tone: "info", text: `Now signed in as ${m.user.display_name} (${m.user.role}).` });
      navigate(homePathFor(m.user.role));
      onNavigate();
    },
  });

  // ---- samples (staff-only endpoint)
  const samples = useQuery({ queryKey: qk.samples(), queryFn: () => api.get<SampleOut[]>("/api/samples"), enabled: isStaff, staleTime: 5 * 60_000 });

  // ---- patient test responses
  const episodes = useQuery({ queryKey: qk.episodes(), queryFn: () => api.get<EpisodeRow[]>("/api/episodes"), enabled: isStaff });
  const patientEpisodes = state.accounts.filter((a) => a.role === "patient" && a.episode_id != null);
  const episodeOptions: { id: number; label: string }[] = [
    ...(episodes.data ?? []).filter((e) => e.status === "active").map((e) => ({ id: e.id, label: `${e.patient.full_name} (episode ${e.id})` })),
    ...patientEpisodes
      .filter((a) => !(episodes.data ?? []).some((e) => e.id === a.episode_id))
      .map((a) => ({ id: a.episode_id as number, label: `${a.display_name} (episode ${a.episode_id})` })),
  ];
  const [epId, setEpId] = useState<string>("");
  useEffect(() => {
    if (!epId && episodeOptions.length) setEpId(String(episodeOptions[0].id));
  }, [epId, episodeOptions]);
  const [respResult, setRespResult] = useState<PatientCheckInOut | null>(null);
  const respond = useMutation({
    mutationFn: (preset: "all_good" | "concern" | "red_flag") => api.post<PatientCheckInOut>(`/api/demo/episodes/${epId}/patient-response`, { preset }),
    onSuccess: (r) => {
      setRespResult(r);
      void qc.invalidateQueries();
    },
  });

  // ---- outbox
  const outbox = useQuery({ queryKey: qk.demoOutbox(), queryFn: () => api.get<DemoOutboxItem[]>("/api/demo/outbox") });

  // ---- reset
  const [confirmText, setConfirmText] = useState("");
  const [resetNotice, setResetNotice] = useState<string | null>(null);
  const reset = useMutation({
    mutationFn: () => api.post<{ ok: boolean; notice: string; me: MeOut | null }>("/api/demo/reset", { confirm: confirmText }),
    onSuccess: (r) => {
      setResetNotice(r.notice);
      setConfirmText("");
      qc.clear();
      qc.setQueryData(qk.me(), r.me ?? null);
      navigate(r.me ? homePathFor(r.me.user.role) : "/");
    },
  });

  const groups: { role: string; label: string }[] = [
    { role: "doctor", label: "Doctors" },
    { role: "nurse", label: "Nurses" },
    { role: "patient", label: "Patients (after invitation and code sign-in)" },
  ];

  return (
    <>
      <Section title="Simulated time">
        <p className="text-sm">
          <span className="font-bold">{fmtDateTime(state.sim_now)} IST</span>
          <span className="text-muted"> · offset +{Math.round(state.sim_offset_hours * 10) / 10} h</span>
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {([6, 24] as const).map((h) => (
            <Button key={h} size="sm" variant="secondary" icon={<FastForward aria-hidden className="size-4" />} disabled={!signedIn || advance.isPending} loading={advance.isPending && advance.variables === h} onClick={() => advance.mutate(h)}>
              Advance {h} h
            </Button>
          ))}
        </div>
        {!signedIn && <p className="mt-1 text-xs text-muted">Sign in or switch to a role first.</p>}
        <p className="mt-1 text-xs text-muted">Approval gates still apply. Stale reminders are skipped, not sent in a burst.</p>
        {advance.isError && <Banner tone="warning" className="mt-2">{errorMessage(advance.error)}</Banner>}
        {advanceResult && (
          <div role="status" className="mt-2 rounded-md bg-sunken px-3 py-2 text-sm">
            <p>
              Now {fmtDateTime(advanceResult.sim_now)} · ran <strong>{advanceResult.ran}</strong> · skipped as stale <strong>{advanceResult.skipped_stale}</strong> · waiting for
              approval <strong>{advanceResult.awaiting_approval}</strong>
            </p>
            {advanceResult.notes.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-xs text-muted">
                {advanceResult.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Section>

      <Section title="Switch role">
        {groups.map((g) => {
          const list = state.accounts.filter((a) => a.role === g.role);
          if (!list.length) return null;
          return (
            <div key={g.role} className="mb-2">
              <p className="text-xs font-bold text-muted">{g.label}</p>
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {list.map((a) => {
                  const current = me?.user.id === a.user_id;
                  return (
                    <li key={a.user_id}>
                      <Button
                        size="sm"
                        variant={current ? "primary" : "secondary"}
                        icon={<UserRoundCog aria-hidden className="size-3.5" />}
                        aria-pressed={current}
                        disabled={loginAs.isPending}
                        onClick={() => loginAs.mutate(a.user_id)}
                      >
                        {a.display_name}
                        {a.episode_id != null ? ` · ep ${a.episode_id}` : ""}
                      </Button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
        {loginAs.isError && <Banner tone="warning">{errorMessage(loginAs.error)}</Banner>}
      </Section>

      <Section title="Load a fictional sample">
        {me?.user.role === "nurse" ? (
          <ul className="flex flex-wrap gap-1.5">
            {[...(samples.data ?? [])]
              .sort((a, b) => Number(b.primary) - Number(a.primary))
              .map((s) => (
                <li key={s.key}>
                  <Button
                    size="sm"
                    variant={s.primary ? "primary" : "secondary"}
                    onClick={() => {
                      navigate(`/hospital/intake?sample=${encodeURIComponent(s.key)}`);
                      onNavigate();
                    }}
                  >
                    {s.label}
                  </Button>
                </li>
              ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">Intake is a nurse task — switch to a nurse account to load a sample into the intake form.</p>
        )}
      </Section>

      <Section title="Submit a patient test response">
        {!signedIn ? (
          <p className="text-sm text-muted">Sign in as the assigned staff or the patient first.</p>
        ) : episodeOptions.length === 0 ? (
          <p className="text-sm text-muted">No active episodes you can access.</p>
        ) : (
          <>
            <label htmlFor="demo-ep" className="text-sm font-bold">
              Episode
            </label>
            <Select id="demo-ep" value={epId} onChange={(e) => setEpId(e.target.value)} className="mt-1">
              {episodeOptions.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(
                [
                  ["all_good", "All good"],
                  ["concern", "Concern"],
                  ["red_flag", "Red flag"],
                ] as const
              ).map(([k, label]) => (
                <Button
                  key={k}
                  size="sm"
                  variant={k === "red_flag" ? "danger" : "secondary"}
                  icon={<Send aria-hidden className="size-3.5" />}
                  disabled={!epId || respond.isPending}
                  loading={respond.isPending && respond.variables === k}
                  onClick={() => respond.mutate(k)}
                >
                  {label}
                </Button>
              ))}
            </div>
            <p className="mt-1 text-xs text-muted">Submits the due check-in (or a concern if none is due) through the normal patient path.</p>
            {respond.isError && <Banner tone="warning" className="mt-2">{errorMessage(respond.error)}</Banner>}
            {respResult && (
              <p role="status" className="mt-2 rounded-md bg-sunken px-3 py-2 text-sm">
                {respResult.title}: received {respResult.receipt.received ? "yes" : "no"} · agent processing {respResult.receipt.agent_processing} · staff review{" "}
                {respResult.receipt.staff_review}
              </p>
            )}
          </>
        )}
      </Section>

      <Section title="Tool modes">
        <ul className="space-y-1.5">
          {state.tools.map((t) => (
            <li key={t.name} className="flex items-start justify-between gap-2 text-sm">
              <span>
                <span className="font-bold">{t.label}</span>
                <span className="block text-xs text-muted">{t.detail}</span>
              </span>
              <ModeBadge mode={t.mode} />
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Simulated outbox (incl. sign-in codes)">
        {outbox.isError && <p className="text-sm text-muted">{errorMessage(outbox.error)}</p>}
        {outbox.data?.length === 0 && <p className="text-sm text-muted">Nothing simulated yet.</p>}
        <ul className="max-h-64 space-y-1.5 overflow-y-auto">
          {(outbox.data ?? []).slice(0, 15).map((m) => (
            <li key={m.id} className="rounded-md border border-line px-2.5 py-1.5 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-1">
                <span className="font-bold">
                  {m.kind} → {m.to_masked}
                </span>
                <StatusBadge domain="message" value={m.status} />
              </div>
              <p className="mt-0.5 break-words">{m.body}</p>
              <p className="text-xs text-muted">{fmtDateTime(m.created_at)}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Reset demo data">
        <p className="text-sm text-muted">
          Wipes local data, re-seeds staff and sample episodes and resets the simulated clock. Messages already sent and calendar events already created by live
          integrations are <strong>not</strong> undone.
        </p>
        {resetNotice && (
          <Banner tone="info" className="mt-2" live>
            {resetNotice}
          </Banner>
        )}
        <label htmlFor="demo-reset" className="mt-2 block text-sm font-bold">
          Type RESET to confirm
        </label>
        <div className="mt-1 flex gap-2">
          <TextInput id="demo-reset" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} autoComplete="off" className="min-h-9" />
          <Button variant="danger" size="sm" icon={<RotateCcw aria-hidden className="size-4" />} disabled={confirmText !== "RESET" || !signedIn} loading={reset.isPending} onClick={() => reset.mutate()}>
            Reset
          </Button>
        </div>
        {!signedIn && <p className="mt-1 text-xs text-muted">Sign in first.</p>}
        {reset.isError && <Banner tone="warning" className="mt-2">{errorMessage(reset.error)}</Banner>}
      </Section>
    </>
  );
}
