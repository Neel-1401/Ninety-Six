// Supporting tabs of the patient workspace (Agent C).
import { useQueryClient } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowUpRight, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { fmtDateTime, maskPhone } from "../../lib/format";
import type { CheckInOut, EpisodeDetail, ExecutionOut, FactOut, FollowUpOut } from "../../lib/types";
import {
  Badge,
  Banner,
  Button,
  Card,
  CardHeader,
  Checkbox,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  ModeBadge,
  Select,
  Skeleton,
  StatusBadge,
  TextArea,
  cn,
  useToast,
} from "../../ui";
import { ReconcileDialog } from "../approvals/ActionCard";
import {
  invalidateScopes,
  useActivity,
  useCheckins,
  useCurrentPlan,
  useExecutions,
  useFollowups,
  useMemory,
  useMessages,
  useNotes,
  usePlans,
} from "../hooks";
import { CATEGORY_LABEL, CATEGORY_ORDER, factText, SOURCE_KIND_LABEL, TRIGGER_LABEL } from "../util";
import { PlanDiffView, PlanMeta, PlanStepsTable } from "./PlanPanel";

// ------------------------------------------------------------------ Check-ins & messages
export function CheckinsMessagesTab({ ep }: { ep: EpisodeDetail }) {
  const checkins = useCheckins(ep.id);
  const messages = useMessages(ep.id);
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader title="Check-ins" description="Triage level is staff-only. Agent processing is not a staff review — mark reviewed when a person has looked." />
        <div className="space-y-3 px-4 py-3">
          {checkins.isLoading && <Skeleton className="h-24" />}
          {checkins.isError && <ErrorState error={checkins.error} onRetry={() => checkins.refetch()} />}
          {checkins.data?.length === 0 && <p className="text-sm text-muted">No check-ins yet. They open at the planned times.</p>}
          {checkins.data?.map((c) => <CheckinCard key={c.id} c={c} episodeId={ep.id} />)}
        </div>
      </Card>
      <Card>
        <CardHeader title="Messages" description="All audiences. Status is what the provider reported — accepted is not delivered; simulated was never sent." />
        <ul className="divide-y divide-line">
          {messages.isLoading && <li className="px-4 py-3"><Skeleton className="h-16" /></li>}
          {messages.isError && <li className="px-4 py-3"><ErrorState error={messages.error} onRetry={() => messages.refetch()} /></li>}
          {messages.data?.length === 0 && <li className="px-4 py-3 text-sm text-muted">No messages yet.</li>}
          {messages.data?.map((m) => (
            <li key={m.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {m.direction === "inbound" ? <ArrowDownLeft aria-hidden className="size-4 text-primary" /> : <ArrowUpRight aria-hidden className="size-4 text-muted" />}
                <span className="font-bold">
                  {m.direction === "inbound" ? "From" : "To"} {m.audience} · {m.channel.toUpperCase()} · {m.kind}
                </span>
                <StatusBadge domain="message" value={m.status} />
                <ModeBadge mode={m.mode} />
                {m.patient_visible ? <Badge tone="neutral">Visible to patient</Badge> : <Badge tone="neutral">Staff only</Badge>}
              </div>
              {m.subject && <p className="mt-1 text-sm font-bold">{m.subject}</p>}
              <p className="mt-1 whitespace-pre-wrap text-[15px]">{m.body}</p>
              <p className="mt-1 text-xs text-muted">
                {fmtDateTime(m.sent_at ?? m.created_at)}
                {m.effective_recipient && ` · to ${m.effective_recipient.includes("@") ? m.effective_recipient : maskPhone(m.effective_recipient)}`}
                {m.intended_recipient && m.effective_recipient && m.intended_recipient !== m.effective_recipient && " (redirected via DEMO_PHONE)"}
                {m.provider_ref && ` · ref ${m.provider_ref}`}
                {m.error && <span className="block text-urgent-ink">Error: {m.error}</span>}
              </p>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function CheckinCard({ c, episodeId }: { c: CheckInOut; episodeId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const triageTone = c.triage_level === "urgent" ? "urgent" : c.triage_level === "concern" ? "attention" : "neutral";
  return (
    <article className={cn("rounded-md border px-3 py-2.5", c.triage_level === "urgent" ? "border-urgent/50" : "border-line")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-bold">{c.title}</p>
          <p className="text-sm text-muted">
            {c.kind === "concern" ? "Patient-raised concern" : `Due ${fmtDateTime(c.due_at)}`}
            {c.submitted_at && ` · submitted ${fmtDateTime(c.submitted_at)}`}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <StatusBadge domain="checkin" value={c.status} />
          {c.status === "submitted" && <StatusBadge domain="processing" value={c.processing_state} />}
          {c.triage_level && (
            <Badge tone={triageTone} title={`Source: ${c.triage_source ?? "unknown"}`}>
              Triage: {c.triage_level}
            </Badge>
          )}
          {c.status === "submitted" && <StatusBadge domain="staffReview" value={c.staff_review_state} />}
        </div>
      </div>
      {c.triage_reasons.length > 0 && (
        <ul className="mt-1.5 list-disc pl-5 text-sm">
          {c.triage_reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
          {c.triage_source && <li className="list-none text-xs text-muted">Triage source: {c.triage_source}</li>}
        </ul>
      )}
      {c.answers.length > 0 && (
        <dl className="mt-2 grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          {c.answers.map((a) => (
            <div key={a.question_id} className="contents">
              <dt className="text-muted">{a.question_text}</dt>
              <dd className="font-bold">{a.label || a.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {c.free_text && <p className="mt-2 rounded bg-sunken px-2 py-1 text-sm">“{c.free_text}”</p>}
      {c.reviewed_by && (
        <p className="mt-1.5 text-sm text-muted">
          Reviewed by {c.reviewed_by} · {fmtDateTime(c.reviewed_at)}
          {c.review_note ? ` — ${c.review_note}` : ""}
        </p>
      )}
      {c.status === "submitted" && c.staff_review_state !== "reviewed" && (
        <Button className="mt-2" size="sm" variant="secondary" onClick={() => setOpen(true)}>
          Mark reviewed by me…
        </Button>
      )}
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Mark check-in reviewed"
        description="The patient will see “Reviewed by staff”. Only do this once a person has actually read the answers."
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={busy}
              onClick={async () => {
                if (!note.trim()) {
                  setErr("Add a short note.");
                  return;
                }
                setBusy(true);
                try {
                  await api.post(`/api/checkins/${c.id}/mark-reviewed`, { note: note.trim() });
                  toast({ tone: "success", text: "Check-in marked reviewed." });
                  await invalidateScopes(qc, ["checkins", "reviews", "overview", "activity", "episode"], episodeId);
                  setOpen(false);
                } catch (e) {
                  setErr(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              Mark reviewed
            </Button>
          </>
        }
      >
        <Field label="Review note (staff only)" required error={err}>
          {({ id, describedBy, invalid }) => <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={3} value={note} onChange={(e) => { setNote(e.target.value); setErr(null); }} />}
        </Field>
      </Dialog>
    </article>
  );
}

// ------------------------------------------------------------------ Medicines & instructions
const PATIENT_CATS = ["diagnosis", "medication", "allergy", "follow_up", "red_flag", "instruction"];

export function MedicinesTab({ ep }: { ep: EpisodeDetail }) {
  const reviewed = ep.facts.filter((f) => f.active && PATIENT_CATS.includes(f.category));
  const medInfo = ep.facts.filter((f) => f.active && f.category === "medication_info");
  const byCat = (c: string) => reviewed.filter((f) => f.category === c);
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader title="Reviewed instructions" description="What the patient sees: doctor-confirmed information from the discharge summary, with its source line." />
        <div className="space-y-4 px-4 py-3">
          {PATIENT_CATS.map((c) => {
            const list = byCat(c);
            if (!list.length) return null;
            return (
              <section key={c} aria-labelledby={`rv-${c}`}>
                <h3 id={`rv-${c}`} className="text-sm font-bold uppercase tracking-wide text-muted">
                  {CATEGORY_LABEL[c]}
                </h3>
                <ul className="mt-1 space-y-1">
                  {list.map((f) => (
                    <li key={f.id} className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-line px-3 py-2 text-sm">
                      <span className={cn("min-w-0", f.is_missing ? "text-attention-ink font-bold" : "")}>{factText(f)}</span>
                      <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                        <StatusBadge domain="fact" value={f.review_state} />
                        {f.source_ref}
                        {f.reviewed_by ? ` · ${f.reviewed_by}, ${fmtDateTime(f.reviewed_at)}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </Card>
      <Card>
        <CardHeader title="Medication information (tool lookups)" description="Label excerpts and local interaction rules. Staff only." />
        <div className="space-y-3 px-4 py-3">
          <Banner tone="info">
            Interaction checks are informational. They cannot prove a combination is safe, and “no information found” is not a safety clearance. Ninety-Six
            never suggests dose changes or new medicines.
          </Banner>
          {medInfo.length === 0 && <p className="text-sm text-muted">No medication information recorded.</p>}
          {medInfo.map((f) => (
            <MedInfo key={f.id} f={f} />
          ))}
        </div>
      </Card>
    </div>
  );
}

function MedInfo({ f }: { f: FactOut }) {
  const v = f.value as Record<string, unknown>;
  const limitations = v.limitations as string | string[] | undefined;
  return (
    <article className="rounded-md border border-line px-3 py-2.5 text-sm">
      <p className="font-bold">{f.label}</p>
      <p className="mt-1 whitespace-pre-wrap">{typeof v.text === "string" ? v.text : factText(f)}</p>
      <p className="mt-1 text-xs text-muted">
        Source: {(v.source as string) ?? f.source_ref ?? "unknown"} · {SOURCE_KIND_LABEL[f.source_kind] ?? f.source_kind} · {fmtDateTime(f.created_at)}
        {typeof v.mode === "string" && ` · ${v.mode}`}
      </p>
      {limitations && (
        <p className="mt-1 text-xs text-attention-ink">Limitations: {Array.isArray(limitations) ? limitations.join(" ") : limitations}</p>
      )}
    </article>
  );
}

// ------------------------------------------------------------------ Follow-ups
export function FollowupsTab({ ep }: { ep: EpisodeDetail }) {
  const q = useFollowups(ep.id);
  return (
    <div className="space-y-3">
      <Banner tone="info">
        A calendar event is not a confirmed appointment. Mark “confirmed by the hospital” only after the clinic has actually confirmed the slot.
      </Banner>
      {q.isLoading && <Skeleton className="h-20" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data?.length === 0 && <EmptyState title="No follow-ups yet">Follow-up bookings appear here once proposed.</EmptyState>}
      {q.data?.map((f) => <FollowupCard key={f.id} f={f} episodeId={ep.id} />)}
    </div>
  );
}

function FollowupCard({ f, episodeId }: { f: FollowUpOut; episodeId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <article className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-bold">{f.title}</p>
          <p className="text-sm text-muted">
            {[f.provider_name, f.location].filter(Boolean).join(" · ") || "Provider and location not set"}
            {f.start_at && ` · ${fmtDateTime(f.start_at)} IST`}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <StatusBadge domain="followup" value={f.status} />
          {f.calendar_mode && <ModeBadge mode={f.calendar_mode} />}
        </div>
      </div>
      <p className="mt-1 text-sm">{f.availability_checked ? "Availability was checked." : "Availability has not been checked."}</p>
      {f.external_link && (
        <a href={f.external_link} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-sm font-bold text-primary-ink underline">
          Open calendar event <ExternalLink aria-hidden className="size-3.5" />
        </a>
      )}
      {f.confirmed_by && (
        <p className="mt-1 text-sm text-muted">
          Confirmed by {f.confirmed_by} · {fmtDateTime(f.confirmed_at)}
          {f.notes ? ` — ${f.notes}` : ""}
        </p>
      )}
      {(f.status === "calendar_event_created" || f.status === "proposed") && (
        <Button className="mt-2" size="sm" variant="secondary" onClick={() => setOpen(true)}>
          Record hospital confirmation…
        </Button>
      )}
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Record hospital confirmation"
        description="Use only when the clinic has confirmed this appointment. The patient will then see “Confirmed by the hospital”."
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={busy}
              onClick={async () => {
                if (!note.trim()) {
                  setErr("Note how it was confirmed (who, when).");
                  return;
                }
                setBusy(true);
                try {
                  await api.post(`/api/followups/${f.id}/confirm`, { note: note.trim() });
                  toast({ tone: "success", text: "Recorded as confirmed by the hospital." });
                  await invalidateScopes(qc, ["followups", "activity", "episode"], episodeId);
                  setOpen(false);
                } catch (e) {
                  setErr(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              Confirm appointment
            </Button>
          </>
        }
      >
        <Field label="How it was confirmed" required error={err}>
          {({ id, describedBy, invalid }) => <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={3} value={note} onChange={(e) => { setNote(e.target.value); setErr(null); }} />}
        </Field>
      </Dialog>
    </article>
  );
}

// ------------------------------------------------------------------ Plan history
export function PlanHistoryTab({ ep }: { ep: EpisodeDetail }) {
  const plans = usePlans(ep.id);
  const current = useCurrentPlan(ep.id);
  if (plans.isLoading) return <Skeleton className="h-32" />;
  if (plans.isError) return <ErrorState error={plans.error} onRetry={() => plans.refetch()} />;
  const list = [...(plans.data ?? [])].sort((a, b) => b.version - a.version);
  return (
    <div className="space-y-3">
      {list.map((v) => (
        <details key={v.id} open={v.id === current.data?.id} className="rounded-lg border border-line bg-surface">
          <summary className="cursor-pointer px-4 py-3 font-bold">
            Plan v{v.version} · {v.status} · {TRIGGER_LABEL[v.trigger] ?? v.trigger} · {fmtDateTime(v.created_at)}
          </summary>
          <div className="space-y-4 border-t border-line px-4 py-3">
            <PlanMeta plan={v} />
            <PlanDiffView versions={list} current={v} />
            <PlanStepsTable steps={v.steps} />
          </div>
        </details>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Executions
export function ExecutionsTab({ ep }: { ep: EpisodeDetail }) {
  const q = useExecutions(ep.id);
  const [reconciling, setReconciling] = useState<ExecutionOut | null>(null);
  if (q.isLoading) return <Skeleton className="h-32" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data?.length) return <EmptyState title="No tool executions yet" />;
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full min-w-[900px] text-left text-sm">
        <caption className="sr-only">Tool execution history</caption>
        <thead className="bg-sunken text-xs uppercase tracking-wide text-muted">
          <tr>
            <th scope="col" className="px-3 py-2">When</th>
            <th scope="col" className="px-3 py-2">Tool</th>
            <th scope="col" className="px-3 py-2">Mode</th>
            <th scope="col" className="px-3 py-2">Outcome</th>
            <th scope="col" className="px-3 py-2">Provider result</th>
            <th scope="col" className="px-3 py-2"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {q.data.map((x) => (
            <tr key={x.id} className="align-top">
              <td className="px-3 py-2 whitespace-nowrap">
                {fmtDateTime(x.started_at)}
                <span className="block text-xs text-muted">attempt {x.attempt}</span>
              </td>
              <td className="px-3 py-2">
                {x.tool}
                <span className="block text-xs text-muted">{x.provider}</span>
              </td>
              <td className="px-3 py-2">
                <ModeBadge mode={x.mode} />
              </td>
              <td className="px-3 py-2">
                <Badge tone={x.outcome === "success" ? "done" : x.outcome === "failure" ? "urgent" : "attention"}>{x.outcome}</Badge>
                {x.reconciled_by && <span className="mt-1 block text-xs text-muted">Reconciled by {x.reconciled_by}: {x.reconcile_note}</span>}
              </td>
              <td className="px-3 py-2">
                {x.provider_ref && <span className="block font-mono text-xs">{x.provider_ref}</span>}
                {x.provider_status && <span className="block">status: {x.provider_status}</span>}
                {x.delivery_status && (
                  <span className="mt-1 block">
                    <StatusBadge domain="message" value={x.delivery_status} />
                  </span>
                )}
                {x.safe_error && <span className="block text-urgent-ink">{x.safe_error}</span>}
              </td>
              <td className="px-3 py-2">
                {x.outcome === "unknown" && !x.reconciled_at && (
                  <Button size="sm" variant="secondary" onClick={() => setReconciling(x)}>
                    Reconcile…
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {reconciling && <ReconcileDialog open onClose={() => setReconciling(null)} executionId={reconciling.id} episodeId={ep.id} />}
    </div>
  );
}

// ------------------------------------------------------------------ Memory
export function MemoryTab({ ep }: { ep: EpisodeDetail }) {
  const q = useMemory(ep.id);
  const [history, setHistory] = useState(false);
  const facts = useMemo(() => (q.data?.facts ?? []).filter((f) => history || f.active), [q.data, history]);
  const factById = useMemo(() => new Map((q.data?.facts ?? []).map((f) => [f.id, f])), [q.data]);
  const sorted = useMemo(
    () => [...facts].sort((a, b) => (CATEGORY_ORDER.indexOf(a.category) + 1 || 99) - (CATEGORY_ORDER.indexOf(b.category) + 1 || 99) || a.id - b.id),
    [facts],
  );
  if (q.isLoading) return <Skeleton className="h-32" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const usage = [...(q.data?.usage ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader
          title="Stored facts"
          description="Everything the agent knows about this episode, with source, time, review state and whether it was extracted, patient-reported or inferred."
          actions={<Checkbox label="Include superseded" checked={history} onChange={(e) => setHistory(e.target.checked)} />}
        />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <caption className="sr-only">Memory facts</caption>
            <thead className="bg-sunken text-xs uppercase tracking-wide text-muted">
              <tr>
                <th scope="col" className="px-3 py-2">Fact</th>
                <th scope="col" className="px-3 py-2">Kind & source</th>
                <th scope="col" className="px-3 py-2">Review</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {sorted.map((f) => (
                <tr key={f.id} className={cn("align-top", !f.active && "opacity-60")}>
                  <td className="px-3 py-2">
                    <span className="text-xs text-muted">{CATEGORY_LABEL[f.category] ?? f.category} · #{f.id}</span>
                    <span className="block font-bold">{factText(f)}</span>
                  </td>
                  <td className="px-3 py-2">
                    {SOURCE_KIND_LABEL[f.source_kind] ?? f.source_kind}
                    <span className="block text-xs text-muted">
                      {f.source_ref ?? "—"} · {fmtDateTime(f.created_at)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge domain="fact" value={f.review_state} />
                    {!f.active && <span className="mt-1 block text-xs text-muted">Superseded</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card>
        <CardHeader title="How memory was used" description="Which facts informed planning, triage, messages and the summary." />
        {usage.length === 0 ? (
          <p className="px-4 py-3 text-sm text-muted">No usage recorded yet.</p>
        ) : (
          <ol className="max-h-[60vh] divide-y divide-line overflow-auto">
            {usage.map((u, i) => {
              const f = factById.get(u.fact_id);
              return (
                <li key={i} className="px-4 py-2 text-sm">
                  <span className="font-bold capitalize">{u.used_for}</span>{" "}
                  <span className="text-muted">
                    ({u.ref_type}
                    {u.ref_id != null ? ` #${u.ref_id}` : ""}) · {fmtDateTime(u.created_at)}
                  </span>
                  <span className="block">{f ? `${f.label}: ${factText(f)}` : `Fact #${u.fact_id}`}</span>
                  {u.note && <span className="block text-xs text-muted">{u.note}</span>}
                </li>
              );
            })}
          </ol>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ Activity
export function ActivityTab({ ep }: { ep: EpisodeDetail }) {
  const q = useActivity(ep.id);
  const [kind, setKind] = useState("");
  const kinds = useMemo(() => [...new Set((q.data ?? []).map((e) => e.kind.split(".")[0]))].sort(), [q.data]);
  if (q.isLoading) return <Skeleton className="h-32" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const list = [...(q.data ?? [])].filter((e) => !kind || e.kind.startsWith(kind + ".") || e.kind === kind).sort((a, b) => b.id - a.id);
  return (
    <Card>
      <CardHeader
        title="Activity log"
        description="Audit trail of agent, worker, staff and patient events. Decision summaries only — no hidden reasoning."
        actions={
          <div className="flex items-center gap-2">
            <label htmlFor="act-kind" className="text-sm font-bold">
              Filter
            </label>
            <Select id="act-kind" value={kind} onChange={(e) => setKind(e.target.value)} className="min-h-8 w-auto py-1 text-sm">
              <option value="">All events</option>
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </Select>
          </div>
        }
      />
      {list.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted">No events.</p>
      ) : (
        <ol className="max-h-[70vh] divide-y divide-line overflow-auto">
          {list.map((e) => (
            <li key={e.id} className="px-4 py-2 text-sm">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-xs whitespace-nowrap text-muted">{fmtDateTime(e.created_at)}</span>
                <span className="font-mono text-xs text-primary-ink">{e.kind}</span>
                <span className="text-xs text-muted">{e.actor_label || e.actor_type}</span>
              </div>
              <p className={cn(e.kind.includes("fallback") || e.kind.includes("failed") || e.kind.includes("unknown") ? "font-bold text-attention-ink" : "")}>{e.title}</p>
              {Object.keys(e.detail ?? {}).length > 0 && (
                <details className="mt-0.5">
                  <summary className="cursor-pointer text-xs font-bold text-primary-ink">Details</summary>
                  <pre className="mt-1 max-h-60 overflow-auto rounded bg-sunken p-2 text-xs whitespace-pre-wrap">{JSON.stringify(e.detail, null, 2)}</pre>
                </details>
              )}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ Notes
export function NotesTab({ ep }: { ep: EpisodeDetail }) {
  const q = useNotes(ep.id);
  const qc = useQueryClient();
  const toast = useToast();
  const [body, setBody] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader title="Coordination notes" description="Staff only. Never shown to the patient." />
        {q.isLoading && <Skeleton className="m-4 h-16" />}
        {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
        {q.data?.length === 0 && <p className="px-4 py-3 text-sm text-muted">No notes yet.</p>}
        <ol className="divide-y divide-line">
          {[...(q.data ?? [])].reverse().map((n) => (
            <li key={n.id} className="px-4 py-2.5 text-sm">
              <p className="whitespace-pre-wrap">{n.body}</p>
              <p className="mt-0.5 text-xs text-muted">
                {n.author} · {fmtDateTime(n.created_at)}
              </p>
            </li>
          ))}
        </ol>
      </Card>
      {ep.permissions.can_add_note && (
        <Card>
          <CardHeader title="Add a note" />
          <div className="space-y-3 px-4 py-3">
            <Field label="Note" error={err}>
              {({ id, describedBy, invalid }) => (
                <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={4} value={body} onChange={(e) => { setBody(e.target.value); setErr(null); }} />
              )}
            </Field>
            <Button
              loading={busy}
              onClick={async () => {
                if (!body.trim()) {
                  setErr("Write something first.");
                  return;
                }
                setBusy(true);
                try {
                  await api.post(`/api/episodes/${ep.id}/notes`, { body: body.trim() });
                  setBody("");
                  toast({ tone: "success", text: "Note added." });
                  await invalidateScopes(qc, ["notes", "activity"], ep.id);
                } catch (e) {
                  setErr(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              Add note
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
