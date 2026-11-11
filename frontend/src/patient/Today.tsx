// Today (Agent D): next useful action first, then progress, check-ins, reminders, contacts.
// Never shows risk scores, triage levels or agent reasoning.
import { motion, useReducedMotion } from "framer-motion";
import { BellRing, CalendarClock, ChevronRight, ClipboardCheck, FileText, MessageCirclePlus } from "lucide-react";
import { Link } from "react-router";
import { useSimClock } from "../lib/clock";
import { fmtDateTime, fmtDuration, fmtRelative } from "../lib/format";
import type { PatientCheckInOut, TodayOut } from "../lib/types";
import { Banner, Button, EmptyState, StatusBadge } from "../ui";
import { monitoringState, useEpisode, useMyCheckins, useToday } from "./data";
import { ContactBlock, LoadError, LoadingBlock, PageTitle, Panel, ReceiptStages, Section } from "./parts";

function ProgressPanel({ t }: { t: TodayOut }) {
  const { hours_elapsed, window_hours, ended } = t.progress;
  const clamped = Math.min(Math.max(hours_elapsed, 0), window_hours);
  const totalDays = Math.round(window_hours / 24);
  const day = Math.min(totalDays, Math.floor(clamped / 24) + 1);
  const pct = Math.round((clamped / window_hours) * 100);
  return (
    <Panel>
      {ended ? (
        <>
          <p className="font-bold">Monitoring period ended</p>
          <p className="mt-1 text-[15px] text-muted">
            This is not a medical all-clear. Keep your follow-up appointment and keep following your instructions.
          </p>
        </>
      ) : (
        <>
          <p className="font-bold">
            Day {day} of the {totalDays}-day monitoring period
          </p>
          <p className="mt-0.5 text-[15px] text-muted">{fmtDuration(Math.max(hours_elapsed, 0))} since you went home</p>
        </>
      )}
      <div
        role="progressbar"
        aria-label="Monitoring period"
        aria-valuemin={0}
        aria-valuemax={window_hours}
        aria-valuenow={Math.round(clamped)}
        aria-valuetext={ended ? "Monitoring period ended" : `${Math.round(clamped)} of ${window_hours} hours`}
        className="mt-3 h-2.5 w-full overflow-hidden rounded-full bg-sunken"
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1.5 flex justify-between text-xs text-muted">
        <span>Went home</span>
        <span>{totalDays} days</span>
      </div>
    </Panel>
  );
}

function NextAction({ t }: { t: TodayOut }) {
  const reduce = useReducedMotion();
  const na = t.next_action;
  const due = t.due_checkin;
  const checkinId = na.checkin_id ?? (due && due.status === "due" && !due.receipt.received ? due.id : null);

  let cta: React.ReactNode = null;
  if (na.kind === "checkin" && checkinId) {
    cta = (
      <Link
        to={`/p/checkins/${checkinId}`}
        className="mt-4 flex min-h-12 items-center justify-center gap-2 rounded-md bg-primary px-5 font-bold text-white hover:bg-primary-hover"
      >
        <ClipboardCheck aria-hidden className="size-5" />
        Start check-in
      </Link>
    );
  } else if (na.kind === "instructions") {
    cta = (
      <Link
        to="/p/instructions"
        className="mt-4 flex min-h-12 items-center justify-center gap-2 rounded-md bg-primary px-5 font-bold text-white hover:bg-primary-hover"
      >
        <FileText aria-hidden className="size-5" />
        Read your instructions
      </Link>
    );
  } else if (na.kind === "followup") {
    cta = (
      <Link
        to="/p/follow-up"
        className="mt-4 flex min-h-12 items-center justify-center gap-2 rounded-md bg-primary px-5 font-bold text-white hover:bg-primary-hover"
      >
        <CalendarClock aria-hidden className="size-5" />
        See follow-up details
      </Link>
    );
  }

  return (
    <motion.section
      aria-labelledby="next-action-title"
      initial={{ opacity: 0, y: reduce ? 0 : 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
      className={
        na.kind === "checkin"
          ? "rounded-lg border-2 border-primary bg-primary-soft p-5"
          : "rounded-lg border border-line bg-surface p-5"
      }
    >
      <p className="text-sm font-bold uppercase tracking-wide text-primary-ink">Next step</p>
      <h2 id="next-action-title" className="mt-1 text-xl font-bold text-ink">
        {na.title}
      </h2>
      {na.description && <p className="mt-1 text-[15px] text-ink">{na.description}</p>}
      {na.kind === "checkin" && due?.available_until && (
        <p className="mt-2 text-sm text-muted">Open until {fmtDateTime(due.available_until)}</p>
      )}
      {cta}
    </motion.section>
  );
}

function CheckinRow({ c, now }: { c: PatientCheckInOut; now: string | undefined }) {
  const submitted = c.receipt.received;
  return (
    <li>
      <Link
        to={`/p/checkins/${c.id}`}
        className="flex min-h-16 items-center justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-3 hover:border-primary"
      >
        <span className="min-w-0">
          <span className="block font-bold text-ink">{c.title}</span>
          <span className="block text-sm text-muted">
            {submitted
              ? `Sent ${fmtDateTime(c.submitted_at ?? c.receipt.received_at)}`
              : c.status === "upcoming"
                ? `Opens ${now ? fmtRelative(c.due_at, now) : fmtDateTime(c.due_at)}`
                : `Due ${fmtDateTime(c.due_at)}`}
          </span>
          <span className="mt-1.5 flex flex-wrap gap-1.5">
            <StatusBadge domain="checkin" value={c.status} />
            {submitted && c.receipt.staff_review === "reviewed" && <StatusBadge domain="staffReview" value="reviewed" />}
            {submitted && c.receipt.staff_review === "awaiting" && <StatusBadge domain="staffReview" value="awaiting" />}
          </span>
        </span>
        <ChevronRight aria-hidden className="size-5 shrink-0 text-muted" />
      </Link>
    </li>
  );
}

export function Today() {
  const { episode, episodeId } = useEpisode();
  const q = useToday(episodeId);
  const checkins = useMyCheckins(episodeId);
  const simNow = useSimClock().data?.now;

  if (q.isLoading) return <LoadingBlock />;
  if (q.isError || !q.data) return <LoadError error={q.error} onRetry={() => void q.refetch()} what="today's plan" />;
  const t = q.data;
  const mstate = monitoringState(t.episode, t.progress.ended);
  const ended = mstate === "ended";
  const list = (checkins.data ?? []).slice().sort((a, b) => b.due_at.localeCompare(a.due_at));
  const lastSubmitted = list.find((c) => c.receipt.received);

  return (
    <div>
      <PageTitle sub={episode ? `Discharged ${fmtDateTime(t.episode.discharge_at)}` : undefined}>Today</PageTitle>

      {ended && (
        <Banner tone="info" className="mb-5" title="Monitoring period ended">
          This is not a medical all-clear. Keep your follow-up appointment and contact your hospital if you're worried.
        </Banner>
      )}

      <NextAction t={t} />

      <div className="mt-5">
        <ProgressPanel t={t} />
      </div>

      {t.upcoming_checkin && !(t.next_action.kind === "checkin") && (
        <p className="mt-4 flex items-center gap-2 text-[15px] text-ink">
          <ClipboardCheck aria-hidden className="size-5 text-primary" />
          Next check-in: <span className="font-bold">{t.upcoming_checkin.title}</span>,{" "}
          {fmtDateTime(t.upcoming_checkin.due_at)}
        </p>
      )}

      {lastSubmitted && (
        <Section
          title="Your latest check-in"
          action={
            <Link to={`/p/checkins/${lastSubmitted.id}`} className="text-sm font-bold text-primary-ink underline underline-offset-2">
              Details
            </Link>
          }
        >
          <Panel>
            <p className="mb-3 text-sm text-muted">
              {lastSubmitted.title} · sent {fmtDateTime(lastSubmitted.submitted_at ?? lastSubmitted.receipt.received_at)}
            </p>
            <ReceiptStages receipt={lastSubmitted.receipt} />
          </Panel>
        </Section>
      )}

      {mstate === "not_started" && (
        <Banner tone="info" className="mt-5" title="Your follow-up is being set up">
          Your care team is still reviewing your plan. Until then, follow the written instructions you were given at the
          hospital. If you're worried, call the hospital.
        </Banner>
      )}

      {mstate === "active" && (
        <Section title="Something worrying you?">
          <Panel className="flex flex-col gap-3">
            <p className="text-[15px]">
              You don't have to wait for a check-in. Tell your care team in your own words.
            </p>
            <Link
              to="/p/concern"
              className="flex min-h-12 items-center justify-center gap-2 rounded-md border border-line-strong bg-surface px-5 font-bold text-ink hover:bg-sunken"
            >
              <MessageCirclePlus aria-hidden className="size-5" />
              Report a concern
            </Link>
          </Panel>
        </Section>
      )}

      {t.reminders.length > 0 && (
        <Section title="Reminders from your hospital">
          <ul className="flex flex-col gap-2.5">
            {t.reminders.map((r) => (
              <li key={r.id} className="rounded-lg border border-line bg-surface p-4">
                <p className="flex items-start gap-2 text-[15px]">
                  <BellRing aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
                  <span>{r.body}</span>
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
                  <StatusBadge domain="message" value={r.status} />
                  {r.sent_at && <span>{fmtDateTime(r.sent_at)}</span>}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Your check-ins">
        {checkins.isLoading ? (
          <LoadingBlock lines={1} />
        ) : checkins.isError ? (
          <LoadError error={checkins.error} onRetry={() => void checkins.refetch()} what="your check-ins" />
        ) : list.length === 0 ? (
          <EmptyState title="No check-ins yet">
            Your care team schedules short check-ins after you go home. They'll appear here when they open.
          </EmptyState>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {list.map((c) => (
              <CheckinRow key={c.id} c={c} now={simNow} />
            ))}
          </ul>
        )}
      </Section>

      <Section title="What to expect">
        <Panel>
          <p className="whitespace-pre-line text-[15px]">{t.monitoring_expectations}</p>
        </Panel>
      </Section>

      <Section title="Contacts">
        <ContactBlock hospitalPhone={t.contact.hospital_phone} emergency={t.contact.emergency_number} />
      </Section>

      <div className="mt-6">
        <Button variant="quiet" size="sm" onClick={() => void q.refetch()}>
          Refresh
        </Button>
      </div>
    </div>
  );
}
