// Active / ended episode workspace (Agent C).
import { useQueryClient } from "@tanstack/react-query";
import { ShieldOff } from "lucide-react";
import { useState } from "react";
import { errorMessage } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { useSimClock } from "../../lib/clock";
import { fmtDateTime, fmtTPlus } from "../../lib/format";
import type { EpisodeDetail, PlanStepOut, RoutineOut } from "../../lib/types";
import { Banner, Button, Card, CardHeader, Dialog, EmptyState, ErrorState, Field, Skeleton, StatusBadge, TabPanel, Tabs, TextArea, useToast } from "../../ui";
import { ActionCard } from "../approvals/ActionCard";
import { invalidateScopes, routineApi, useCurrentPlan, useEpisodeActions, useEpisodeReviews, usePlans, useSummary } from "../hooks";
import { ReviewItemCard } from "../ReviewItemCard";
import { actionLabel, PAST_STEP, TEMPLATE_LABEL, TRIGGER_LABEL } from "../util";
import { PlanDiffView } from "./PlanPanel";
import { ActivityTab, CheckinsMessagesTab, ExecutionsTab, FollowupsTab, MedicinesTab, MemoryTab, NotesTab, PlanHistoryTab } from "./Tabs";
import { Timeline } from "./Timeline";

const TABS = [
  { id: "checkins", label: "Check-ins & messages" },
  { id: "medicines", label: "Medicines & instructions" },
  { id: "followups", label: "Follow-ups" },
  { id: "plans", label: "Plan history" },
  { id: "executions", label: "Execution history" },
  { id: "memory", label: "Memory" },
  { id: "activity", label: "Activity" },
  { id: "notes", label: "Notes" },
];

export function MonitorView({ ep }: { ep: EpisodeDetail }) {
  const { data: clock } = useSimClock();
  const plan = useCurrentPlan(ep.id);
  const plans = usePlans(ep.id);
  const actions = useEpisodeActions(ep.id);
  const reviews = useEpisodeReviews(ep.id);
  const [tab, setTab] = useState("checkins");
  const [showPast, setShowPast] = useState(false);

  const pendingActions = (actions.data ?? []).filter((a) => a.approval_state === "pending");
  const troubled = (actions.data ?? []).filter((a) => a.approval_state !== "pending" && (a.execution_state === "failed" || a.execution_state === "unknown"));
  const activeReviews = (reviews.data ?? []).filter((r) => r.status !== "resolved");
  const urgentReviews = activeReviews.filter((r) => r.priority === "urgent");
  const otherReviews = activeReviews.filter((r) => r.priority !== "urgent");

  const steps = plan.data?.steps ?? [];
  const upcoming = steps.filter((s) => !PAST_STEP.has(s.status));
  const past = steps.filter((s) => PAST_STEP.has(s.status));

  function selectStep(id: number) {
    const s = steps.find((x) => x.id === id);
    if (s && PAST_STEP.has(s.status)) setShowPast(true);
    window.setTimeout(() => {
      const el = document.getElementById(`mstep-${id}`);
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
      el?.focus();
    }, 50);
  }

  return (
    <div className="space-y-5">
      {(ep.status === "awaiting_closure_review" || ep.status === "closed") && <SummaryCard ep={ep} />}

      {urgentReviews.length > 0 && (
        <section aria-labelledby="urgent-h" className="space-y-3">
          <h2 id="urgent-h" className="text-lg font-bold text-urgent-ink">
            Urgent review
          </h2>
          {urgentReviews.map((r) => (
            <ReviewItemCard key={r.id} item={r} showPatient={false} />
          ))}
        </section>
      )}

      <Card>
        <CardHeader
          title="96-hour timeline"
          description={`Window ${fmtDateTime(ep.discharge_at)} → ${fmtDateTime(ep.window_ends_at)} (IST). Select a marker to jump to the step.`}
          actions={plan.data && <span className="text-sm text-muted">Showing plan v{plan.data.version}</span>}
        />
        <div className="px-4 py-4">
          {plan.isLoading && <Skeleton className="h-40" />}
          {plan.isError && <ErrorState error={plan.error} onRetry={() => plan.refetch()} />}
          {plan.data && <Timeline steps={steps} dischargeAt={ep.discharge_at} nowIso={clock?.now} versions={plans.data ?? []} onSelect={selectStep} />}
        </div>
      </Card>

      {pendingActions.length > 0 && (
        <section aria-labelledby="pending-h" className="space-y-3">
          <h2 id="pending-h" className="text-lg font-bold">
            Waiting for approval ({pendingActions.length})
          </h2>
          {pendingActions.map((a) => (
            <ActionCard key={a.id} action={a} showPatient={false} />
          ))}
        </section>
      )}
      {troubled.length > 0 && (
        <section aria-labelledby="troubled-h" className="space-y-3">
          <h2 id="troubled-h" className="text-lg font-bold">
            Failed or uncertain actions
          </h2>
          {troubled.map((a) => (
            <ActionCard key={a.id} action={a} showPatient={false} />
          ))}
        </section>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader title="Current and upcoming steps" description="Times are IST; T+ is hours after the actual discharge time." />
          <div className="px-4 py-3">
            {plan.data && upcoming.length === 0 && <p className="text-muted">No upcoming steps.</p>}
            <StepList steps={upcoming} />
            {past.length > 0 && (
              <div className="mt-3 border-t border-line pt-3">
                <button type="button" aria-expanded={showPast} onClick={() => setShowPast((v) => !v)} className="text-sm font-bold text-primary-ink underline cursor-pointer">
                  {showPast ? "Hide" : "Show"} completed, skipped and superseded steps ({past.length})
                </button>
                {showPast && <StepList steps={past} />}
              </div>
            )}
          </div>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Proposed changes" description="What the agent changed in the latest plan version, and why." />
            <div className="px-4 py-3">
              {plan.data && plans.data ? (
                plan.data.version > 1 || plan.data.change_summary.length ? (
                  <>
                    <p className="mb-2 text-sm text-muted">
                      v{plan.data.version} · {TRIGGER_LABEL[plan.data.trigger] ?? plan.data.trigger} · {fmtDateTime(plan.data.created_at)}
                    </p>
                    <PlanDiffView versions={plans.data} current={plan.data} />
                  </>
                ) : (
                  <p className="text-sm text-muted">No changes since the plan was activated.</p>
                )
              ) : (
                <Skeleton className="h-20" />
              )}
            </div>
          </Card>

          <RoutineCard ep={ep} />

          <Card>
            <CardHeader title="Other review items" />
            <div className="space-y-3 px-4 py-3">
              {otherReviews.length === 0 ? <p className="text-sm text-muted">No open review items.</p> : otherReviews.map((r) => <ReviewItemCard key={r.id} item={r} showPatient={false} />)}
            </div>
          </Card>
        </div>
      </div>

      <section aria-label="Episode details">
        <Tabs tabs={TABS} value={tab} onChange={setTab} label="Episode details" />
        <TabPanel id="checkins" active={tab === "checkins"}>
          <CheckinsMessagesTab ep={ep} />
        </TabPanel>
        <TabPanel id="medicines" active={tab === "medicines"}>
          <MedicinesTab ep={ep} />
        </TabPanel>
        <TabPanel id="followups" active={tab === "followups"}>
          <FollowupsTab ep={ep} />
        </TabPanel>
        <TabPanel id="plans" active={tab === "plans"}>
          <PlanHistoryTab ep={ep} />
        </TabPanel>
        <TabPanel id="executions" active={tab === "executions"}>
          <ExecutionsTab ep={ep} />
        </TabPanel>
        <TabPanel id="memory" active={tab === "memory"}>
          <MemoryTab ep={ep} />
        </TabPanel>
        <TabPanel id="activity" active={tab === "activity"}>
          <ActivityTab ep={ep} />
        </TabPanel>
        <TabPanel id="notes" active={tab === "notes"}>
          <NotesTab ep={ep} />
        </TabPanel>
      </section>
    </div>
  );
}

function StepList({ steps }: { steps: PlanStepOut[] }) {
  return (
    <ol className="divide-y divide-line">
      {steps.map((s) => (
        <li key={s.id} id={`mstep-${s.id}`} tabIndex={-1} className="py-2.5 focus-visible:outline-offset-0">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-bold">
                <span className="mr-2 font-mono text-sm text-muted">{fmtTPlus(s.offset_hours)}</span>
                {s.title}
              </p>
              <p className="text-sm text-muted">
                {actionLabel(s.action_type)} · due {fmtDateTime(s.due_at)}
                {s.depends_on.length ? ` · after ${s.depends_on.join(", ")}` : ""}
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <StatusBadge domain="step" value={s.status} />
              {s.action && s.action.approval_state !== "not_required" && <StatusBadge domain="approval" value={s.action.approval_state} />}
            </div>
          </div>
          {(s.status_reason || s.result_summary || s.executed_at) && (
            <p className="mt-1 text-sm">
              {s.executed_at && <span className="text-muted">Ran {fmtDateTime(s.executed_at)}. </span>}
              {s.result_summary}
              {s.status_reason && <span className="text-muted"> {s.status_reason}</span>}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}

function RoutineCard({ ep }: { ep: EpisodeDetail }) {
  const { data: me } = useMe();
  const isDoctor = me?.user.role === "doctor";
  const [revoking, setRevoking] = useState<RoutineOut | null>(null);
  const ras = ep.routine_authorisations;
  return (
    <Card>
      <CardHeader title="Routine authorisation" description="Bounded permission for templated reminders. Separate from approving or rejecting single actions." />
      <div className="space-y-3 px-4 py-3">
        {ras.length === 0 && <p className="text-sm text-muted">None granted — every external action needs an individual approval.</p>}
        {ras.map((r) => (
          <div key={r.id} className="rounded-md border border-line px-3 py-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge domain="routine" value={r.status} />
              <span className="text-muted">
                Granted by {r.granted_by} · {fmtDateTime(r.granted_at)} · expires {fmtDateTime(r.expires_at)}
              </span>
            </div>
            <p className="mt-1.5">{r.scope_summary}</p>
            <p className="mt-1 text-muted">
              Templates: {r.template_ids.map((t) => TEMPLATE_LABEL[t] ?? t).join(", ")} · recipients: {r.recipient_roles.join(", ")} · at{" "}
              {r.offsets_hours.map((h) => fmtTPlus(h)).join(", ")} · max {r.max_per_day}/day
            </p>
            {r.status === "revoked" && (
              <p className="mt-1">
                Revoked by {r.revoked_by} · {fmtDateTime(r.revoked_at)} — {r.revoke_reason}
              </p>
            )}
            {r.status === "active" && isDoctor && ep.permissions.can_revoke_routine && (
              <Button className="mt-2" size="sm" variant="secondary" icon={<ShieldOff aria-hidden className="size-4" />} onClick={() => setRevoking(r)}>
                Revoke routine permission…
              </Button>
            )}
          </div>
        ))}
      </div>
      {revoking && <RevokeDialog ep={ep} ra={revoking} onClose={() => setRevoking(null)} />}
    </Card>
  );
}

function RevokeDialog({ ep, ra, onClose }: { ep: EpisodeDetail; ra: RoutineOut; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onClose={onClose}
      tone="danger"
      title="Revoke routine authorisation"
      description="All future reminders it covered will stop and wait for your individual approval. Already-sent messages are unaffected. This is different from rejecting a single action."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Keep it
          </Button>
          <Button
            variant="danger"
            loading={busy}
            onClick={async () => {
              if (!reason.trim()) {
                setErr("A reason is required.");
                return;
              }
              setBusy(true);
              try {
                await routineApi.revoke(ra.id, reason.trim());
                toast({ tone: "success", text: "Routine authorisation revoked." });
                await invalidateScopes(qc, ["episode", "plan", "actions", "approvals", "overview", "activity", "routine"], ep.id);
                onClose();
              } catch (e) {
                setErr(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Revoke
          </Button>
        </>
      }
    >
      <Field label="Reason" required error={err}>
        {({ id, describedBy, invalid }) => (
          <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={3} value={reason} onChange={(e) => { setReason(e.target.value); setErr(null); }} />
        )}
      </Field>
    </Dialog>
  );
}

function SummaryCard({ ep }: { ep: EpisodeDetail }) {
  const q = useSummary(ep.id, true);
  return (
    <Card className="border-attention-line">
      <CardHeader
        title={ep.status === "closed" ? "Episode closed after review" : "96-hour window ended — awaiting closure review"}
        description="The monitoring window ending is not a medical clearance. Unresolved items stay visible below."
      />
      <div className="px-4 py-3">
        {ep.closure_note && (
          <Banner tone="info" className="mb-3" title="Closure note">
            {ep.closure_note}
          </Banner>
        )}
        {q.isLoading && <Skeleton className="h-24" />}
        {q.isError && <EmptyState title="Summary not available yet">The worker writes it when the window ends.</EmptyState>}
        {q.data && <SummaryBody s={q.data} />}
      </div>
    </Card>
  );
}

function SummaryBody({ s }: { s: Record<string, unknown> }) {
  const entries = Object.entries(s).filter(([k]) => k !== "statement");
  return (
    <div className="space-y-3 text-sm">
      {typeof s.statement === "string" && <p className="font-bold">{s.statement}</p>}
      <dl className="grid gap-3 md:grid-cols-2">
        {entries.map(([k, v]) => (
          <div key={k} className="rounded-md border border-line px-3 py-2">
            <dt className="font-bold capitalize">{k.replace(/_/g, " ")}</dt>
            <dd className="mt-1">{renderValue(v)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function renderValue(v: unknown): React.ReactNode {
  if (v == null) return <span className="text-muted">—</span>;
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="text-muted">None</span>;
    return (
      <ul className="list-disc space-y-0.5 pl-4">
        {v.slice(0, 30).map((x, i) => (
          <li key={i}>{typeof x === "object" && x ? summarise(x as Record<string, unknown>) : String(x)}</li>
        ))}
      </ul>
    );
  }
  if (typeof v === "object") return summarise(v as Record<string, unknown>);
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return fmtDateTime(v);
  return String(v);
}

function summarise(o: Record<string, unknown>): string {
  const title = (o.title ?? o.label ?? o.kind ?? o.name) as string | undefined;
  const rest = Object.entries(o)
    .filter(([k, x]) => !["title", "label", "id", "name"].includes(k) && x != null && x !== "" && typeof x !== "object")
    .map(([k, x]) => `${k.replace(/_/g, " ")}: ${typeof x === "string" && /^\d{4}-\d{2}-\d{2}T/.test(x) ? fmtDateTime(x) : String(x)}`);
  return [title, rest.join(" · ")].filter(Boolean).join(" — ");
}
