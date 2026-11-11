// Proposed external action card (Agent C). Shows the EXACT payload, intended vs effective
// recipient, why it was proposed, supporting facts, plan version and live/simulated mode.
// Approval binds to the payload hash the doctor saw; editing creates a new payload version.
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, ChevronUp, Info, Mail, MessageSquareText, ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { errorMessage, isApiError } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { fmtDateTime, fromLocalInputValue, maskPhone, toLocalInputValue } from "../../lib/format";
import type { ActionOut } from "../../lib/types";
import {
  Banner,
  Button,
  cn,
  Dialog,
  Field,
  ModeBadge,
  PriorityBadge,
  Select,
  StatusBadge,
  TextArea,
  TextInput,
  useToast,
} from "../../ui";
import { actionsApi, invalidateScopes } from "../hooks";
import { actionLabel, RECIPIENT_LABEL, TEMPLATE_LABEL } from "../util";

const SCOPES = ["approvals", "actions", "plan", "episode", "episodes", "overview", "activity", "messages", "followups", "executions", "reviews"];

function recipientText(a: ActionOut, which: "intended" | "effective"): string {
  const v = which === "intended" ? a.intended_recipient : a.effective_recipient;
  if (!v) return "—";
  if (a.channel === "email" || a.channel === "calendar" || v.includes("@")) return v;
  return /^\+?\d[\d\s-]{5,}$/.test(v) ? maskPhone(v) : v;
}

export function ActionCard({ action: a, showPatient = true }: { action: ActionOut; showPatient?: boolean }) {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const isDoctor = me?.user.role === "doctor";
  const pending = a.approval_state === "pending";
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  // Edit draft is LOCAL state: live refetches never overwrite what the doctor is typing.
  const [draft, setDraft] = useState<Record<string, string>>({});
  const baseHash = useRef<string | null>(null);
  const [reason, setReason] = useState("");
  const [reasonErr, setReasonErr] = useState<string | null>(null);
  const [reconcileOpen, setReconcileOpen] = useState(false);

  const changedUnderneath = mode === "edit" && baseHash.current !== null && baseHash.current !== a.payload_hash;

  useEffect(() => {
    if (!pending && mode !== "view") setMode("view");
  }, [pending, mode]);

  function startEdit() {
    const d: Record<string, string> = {};
    for (const f of a.editable_fields) {
      const v = a.payload[f];
      d[f] = f === "start_at" ? toLocalInputValue(typeof v === "string" ? v : null) : v == null ? "" : String(v);
    }
    setDraft(d);
    baseHash.current = a.payload_hash;
    setError(null);
    setMode("edit");
  }

  async function run(kind: string, fn: () => Promise<unknown>, ok: string) {
    setBusy(kind);
    setError(null);
    try {
      await fn();
      toast({ tone: "success", text: ok });
      setMode("view");
      await invalidateScopes(qc, SCOPES, a.episode_id);
    } catch (e) {
      if (isApiError(e) && (e.code === "stale_version" || e.status === 409)) {
        setError(`${e.message} The latest version has been loaded — please review it before deciding.`);
        await invalidateScopes(qc, SCOPES, a.episode_id);
      } else setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const TypeIcon = a.action_type === "create_calendar_event" ? CalendarClock : a.action_type === "care_team_alert" ? Mail : MessageSquareText;
  const urgent = a.priority === "urgent";
  const validDecisions = a.decisions.filter((d) => d.valid);

  return (
    <article
      aria-label={`${actionLabel(a.action_type)} for ${a.patient.full_name}: ${a.step_title}`}
      className={cn(
        "rounded-lg border bg-surface",
        urgent && pending ? "border-urgent/50 border-l-4 border-l-urgent" : "border-line",
      )}
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <PriorityBadge priority={a.priority} />
        <StatusBadge domain="approval" value={a.approval_state} />
        {a.execution_state !== "not_started" && <StatusBadge domain="execution" value={a.execution_state} />}
        <ModeBadge mode={a.expected_mode} />
        <span className="ml-auto text-sm text-muted">
          Plan v{a.plan_version} · due {fmtDateTime(a.due_at)}
        </span>
      </header>

      <div className="grid gap-4 px-4 py-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        {/* Left: what exactly will happen */}
        <div className="min-w-0">
          <div className="flex items-start gap-2">
            <TypeIcon aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />
            <div className="min-w-0">
              <h3 className="font-bold text-ink">
                {actionLabel(a.action_type)}
                {a.channel && a.channel !== "calendar" ? ` · ${a.channel.toUpperCase()}` : ""}
                {" — "}
                {a.step_title}
              </h3>
              {showPatient && (
                <p className="text-sm">
                  Patient:{" "}
                  <Link to={`/hospital/patients/${a.episode_id}`} className="font-bold text-primary-ink underline">
                    {a.patient.full_name}
                  </Link>
                </p>
              )}
            </div>
          </div>

          <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted">Recipient</dt>
            <dd className="font-bold">
              {RECIPIENT_LABEL[a.recipient_role] ?? a.recipient_role} · {recipientText(a, "intended")}
            </dd>
            <dt className="text-muted">Will actually go to</dt>
            <dd className={cn("font-bold", a.redirected && "text-attention-ink")}>
              {recipientText(a, "effective")}
              {a.redirected && <span className="block font-normal">Redirected to the presenter's DEMO_PHONE — the patient's number is not contacted.</span>}
            </dd>
            {a.template_id && (
              <>
                <dt className="text-muted">Template</dt>
                <dd>{TEMPLATE_LABEL[a.template_id] ?? a.template_id}</dd>
              </>
            )}
            <dt className="text-muted">Payload version</dt>
            <dd>
              v{a.payload_version} <span className="font-mono text-xs text-faint">#{a.payload_hash.slice(0, 10)}</span>
            </dd>
          </dl>

          {mode === "edit" ? (
            <EditForm a={a} draft={draft} setDraft={setDraft} />
          ) : (
            <PayloadView a={a} />
          )}
        </div>

        {/* Right: why */}
        <div className="min-w-0 space-y-3 text-sm">
          <div>
            <h4 className="font-bold text-ink">Why the agent proposed this</h4>
            <p className="mt-0.5 text-muted">{a.reason || "No reason recorded."}</p>
          </div>
          <div>
            <h4 className="font-bold text-ink">Supporting facts</h4>
            {a.supporting_facts.length === 0 ? (
              <p className="text-muted">None linked.</p>
            ) : (
              <ul className="mt-1 space-y-1">
                {a.supporting_facts.map((f) => (
                  <li key={f.id} className="rounded-md bg-sunken px-2 py-1">
                    <span className="font-bold">{f.label}:</span> {f.text}
                    {f.source_ref && <span className="block text-xs text-faint">Source: {f.source_ref}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {a.approval_requirement === "explicit" && (
            <p className="flex items-start gap-1.5 text-muted">
              <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              Needs an individual doctor approval (not coverable by routine authorisation).
            </p>
          )}
          {a.invalidated_reason && (
            <Banner tone="warning" title="Earlier approval invalidated">
              {a.invalidated_reason}
            </Banner>
          )}
        </div>
      </div>

      {/* Decision history + execution */}
      <div className="border-t border-line px-4 py-2">
        <button
          type="button"
          onClick={() => setShowDetail((v) => !v)}
          aria-expanded={showDetail}
          className="inline-flex min-h-8 items-center gap-1 text-sm font-bold text-primary-ink cursor-pointer"
        >
          {showDetail ? <ChevronUp aria-hidden className="size-4" /> : <ChevronDown aria-hidden className="size-4" />}
          Decisions ({a.decisions.length}) and execution
        </button>
        {showDetail && (
          <div className="mt-2 grid gap-3 pb-2 text-sm md:grid-cols-2">
            <div>
              <h4 className="font-bold">Decisions</h4>
              {a.decisions.length === 0 ? (
                <p className="text-muted">No decisions yet.</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {a.decisions.map((d) => (
                    <li key={d.id} className={cn(!d.valid && "text-faint line-through decoration-1")}>
                      {d.decision === "edited_approved" ? "Edited & approved" : d.decision === "approved" ? "Approved" : "Rejected"} by {d.decided_by} ·{" "}
                      {fmtDateTime(d.decided_at)} · #{d.payload_hash.slice(0, 8)}
                      {d.reason && <span className="block no-underline">Reason: {d.reason}</span>}
                      {!d.valid && <span className="block no-underline">Invalid: {d.invalidated_reason ?? "payload changed"}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h4 className="font-bold">Last execution</h4>
              {a.last_execution ? (
                <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5">
                  <dt className="text-muted">Outcome</dt>
                  <dd className="font-bold">{a.last_execution.outcome}</dd>
                  <dt className="text-muted">Mode</dt>
                  <dd>
                    <ModeBadge mode={a.last_execution.mode} />
                  </dd>
                  <dt className="text-muted">Provider</dt>
                  <dd>
                    {a.last_execution.provider}
                    {a.last_execution.provider_ref ? ` · ref ${a.last_execution.provider_ref}` : ""}
                  </dd>
                  {a.last_execution.delivery_status && (
                    <>
                      <dt className="text-muted">Delivery</dt>
                      <dd>
                        <StatusBadge domain="message" value={a.last_execution.delivery_status} />
                      </dd>
                    </>
                  )}
                  {a.last_execution.safe_error && (
                    <>
                      <dt className="text-muted">Error</dt>
                      <dd>{a.last_execution.safe_error}</dd>
                    </>
                  )}
                  <dt className="text-muted">Attempt</dt>
                  <dd>
                    {a.last_execution.attempt} · {fmtDateTime(a.last_execution.started_at)}
                  </dd>
                </dl>
              ) : (
                <p className="text-muted">Not executed.</p>
              )}
            </div>
          </div>
        )}
      </div>

      {error && (
        <div className="px-4 pb-3">
          <Banner tone="warning" live>
            {error}
          </Banner>
        </div>
      )}

      {/* Actions */}
      <footer className="flex flex-wrap items-center gap-2 border-t border-line bg-sunken/60 px-4 py-3">
        {pending && isDoctor && mode === "view" && (
          <>
            <Button loading={busy === "approve"} disabled={!!busy} onClick={() => run("approve", () => actionsApi.approve(a), "Approved. It will run when due.")}>
              Approve
            </Button>
            {a.editable_fields.length > 0 && (
              <Button variant="secondary" disabled={!!busy} onClick={startEdit}>
                Edit & approve…
              </Button>
            )}
            <Button
              variant="secondary"
              disabled={!!busy}
              onClick={() => {
                setReason("");
                setReasonErr(null);
                setMode("reject");
              }}
            >
              Reject…
            </Button>
          </>
        )}
        {pending && isDoctor && mode === "edit" && (
          <div className="w-full space-y-2">
            {changedUnderneath ? (
              <Banner
                tone="warning"
                title="This action changed since you started editing"
                actions={
                  <Button size="sm" variant="secondary" onClick={startEdit}>
                    Load latest and restart edit
                  </Button>
                }
              >
                Your edits are kept here but cannot be submitted against the old version.
              </Banner>
            ) : (
              <p className="flex items-start gap-1.5 text-sm text-muted">
                <Info aria-hidden className="mt-0.5 size-4 shrink-0" />
                Saving creates payload version v{a.payload_version + 1}. Your approval binds to the edited text; any earlier approval of
                v{a.payload_version} is invalidated.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                loading={busy === "edit"}
                disabled={!!busy || changedUnderneath}
                onClick={() => {
                  const payload: Record<string, unknown> = {};
                  for (const f of a.editable_fields) {
                    payload[f] = f === "start_at" ? fromLocalInputValue(draft[f] ?? "") : draft[f];
                  }
                  if (a.editable_fields.includes("body") && !String(payload.body ?? "").trim()) {
                    setError("The message body can't be empty.");
                    return;
                  }
                  void run("edit", () => actionsApi.editApprove(a, baseHash.current ?? a.payload_hash, payload), "Edited version approved.");
                }}
              >
                Save edit & approve
              </Button>
              <Button variant="secondary" disabled={!!busy} onClick={() => setMode("view")}>
                Cancel edit
              </Button>
            </div>
          </div>
        )}
        {pending && isDoctor && mode === "reject" && (
          <div className="w-full space-y-2">
            <Field label="Reason for rejecting this action" required error={reasonErr} hint="Rejecting stops only this action. It does not revoke routine authorisation.">
              {({ id, describedBy, invalid }) => (
                <TextArea
                  id={id}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  rows={2}
                  value={reason}
                  onChange={(e) => {
                    setReason(e.target.value);
                    setReasonErr(null);
                  }}
                />
              )}
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="danger"
                loading={busy === "reject"}
                disabled={!!busy}
                onClick={() => {
                  if (!reason.trim()) {
                    setReasonErr("A reason is required.");
                    return;
                  }
                  void run("reject", () => actionsApi.reject(a, reason.trim()), "Action rejected. It will not run.");
                }}
              >
                Reject action
              </Button>
              <Button variant="secondary" disabled={!!busy} onClick={() => setMode("view")}>
                Cancel
              </Button>
            </div>
          </div>
        )}
        {pending && !isDoctor && (
          <p className="text-sm text-muted">Only the assigned doctor can approve, edit or reject this action.</p>
        )}
        {!pending && a.execution_state === "failed" && isDoctor && (
          <Button variant="secondary" loading={busy === "retry"} onClick={() => run("retry", () => actionsApi.retry(a), "Retry queued.")}>
            Retry failed action
          </Button>
        )}
        {a.execution_state === "unknown" && a.last_execution && (
          <Button variant="secondary" onClick={() => setReconcileOpen(true)}>
            Reconcile unknown outcome…
          </Button>
        )}
        {!pending && a.execution_state !== "failed" && a.execution_state !== "unknown" && (
          <p className="text-sm text-muted">
            {a.approval_state === "rejected"
              ? "Rejected — this action will not run."
              : a.approval_state === "invalidated"
                ? "This version is no longer valid and will not run."
                : a.execution_state === "succeeded"
                  ? "Executed."
                  : a.approval_state === "routine"
                    ? "Covered by routine authorisation; re-checked just before it runs."
                    : "Approved; re-checked just before it runs."}
          </p>
        )}
        {validDecisions.length > 0 && (
          <span className="ml-auto text-xs text-muted">
            Last decision: {validDecisions[validDecisions.length - 1].decided_by}, {fmtDateTime(validDecisions[validDecisions.length - 1].decided_at)}
          </span>
        )}
      </footer>
      {a.last_execution && (
        <ReconcileDialog open={reconcileOpen} onClose={() => setReconcileOpen(false)} executionId={a.last_execution.id} episodeId={a.episode_id} />
      )}
    </article>
  );
}

function PayloadView({ a }: { a: ActionOut }) {
  const p = a.payload;
  if (a.action_type === "create_calendar_event") {
    return (
      <div className="mt-3 rounded-md border border-line bg-sunken/60 px-3 py-2 text-sm">
        <p className="font-bold">{String(p.title ?? "Follow-up")}</p>
        <p>
          {fmtDateTime(typeof p.start_at === "string" ? p.start_at : null)}
          {typeof p.end_at === "string" ? ` – ${fmtDateTime(p.end_at)}` : ""} IST
        </p>
        {p.location != null && <p>Location: {String(p.location)}</p>}
        {p.notes != null && String(p.notes) && <p className="mt-1 whitespace-pre-wrap">{String(p.notes)}</p>}
        <p className="mt-1 text-attention-ink">Availability has not been checked. This creates a calendar event, not a confirmed hospital appointment.</p>
      </div>
    );
  }
  const body = typeof p.body === "string" ? p.body : null;
  const rest = Object.entries(p).filter(([k]) => k !== "body" && k !== "subject");
  return (
    <div className="mt-3">
      {typeof p.subject === "string" && (
        <p className="text-sm">
          <span className="text-muted">Subject: </span>
          <span className="font-bold">{p.subject}</span>
        </p>
      )}
      {body != null && (
        <blockquote className="mt-1 whitespace-pre-wrap rounded-md border border-line bg-sunken/60 px-3 py-2 text-[15px] leading-relaxed text-ink">
          {body}
        </blockquote>
      )}
      {rest.length > 0 && (
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 text-xs text-muted">
          {rest.map(([k, v]) => (
            <div key={k} className="contents">
              <dt>{k}</dt>
              <dd className="break-words">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function EditForm({ a, draft, setDraft }: { a: ActionOut; draft: Record<string, string>; setDraft: (d: Record<string, string>) => void }) {
  const set = (k: string, v: string) => setDraft({ ...draft, [k]: v });
  return (
    <div className="mt-3 space-y-3">
      {a.editable_fields.map((f) => (
        <Field key={f} label={f === "start_at" ? "Start (IST)" : f[0].toUpperCase() + f.slice(1).replace(/_/g, " ")}>
          {({ id, describedBy }) =>
            f === "body" || f === "notes" ? (
              <TextArea id={id} aria-describedby={describedBy} rows={f === "body" ? 6 : 3} value={draft[f] ?? ""} onChange={(e) => set(f, e.target.value)} />
            ) : f === "start_at" ? (
              <TextInput id={id} type="datetime-local" aria-describedby={describedBy} value={draft[f] ?? ""} onChange={(e) => set(f, e.target.value)} />
            ) : (
              <TextInput id={id} aria-describedby={describedBy} value={draft[f] ?? ""} onChange={(e) => set(f, e.target.value)} />
            )
          }
        </Field>
      ))}
    </div>
  );
}

export function ReconcileDialog({
  open,
  onClose,
  executionId,
  episodeId,
}: {
  open: boolean;
  onClose: () => void;
  executionId: number;
  episodeId: number;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [outcome, setOutcome] = useState<"" | "success" | "failure">("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Reconcile an unknown outcome"
      description="The provider may or may not have acted (for example a timeout). Check the provider console or ask the recipient, then record what actually happened. Nothing is retried automatically."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={busy}
            onClick={async () => {
              if (!outcome || !note.trim()) {
                setErr("Choose what happened and add a note explaining how you checked.");
                return;
              }
              setBusy(true);
              setErr(null);
              try {
                await actionsApi.reconcile(executionId, outcome, note.trim());
                toast({ tone: "success", text: "Outcome recorded." });
                await invalidateScopes(qc, SCOPES, episodeId);
                onClose();
              } catch (e) {
                setErr(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Record outcome
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="What actually happened?" required>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={outcome} onChange={(e) => setOutcome(e.target.value as typeof outcome)}>
              <option value="">Choose…</option>
              <option value="success">It was carried out (e.g. message received)</option>
              <option value="failure">It did not happen</option>
            </Select>
          )}
        </Field>
        <Field label="How you checked" required error={err}>
          {({ id, describedBy, invalid }) => (
            <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          )}
        </Field>
      </div>
    </Dialog>
  );
}
