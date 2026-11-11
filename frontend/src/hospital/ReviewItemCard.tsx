// Review item (Agent C): urgent concerns, failed/unknown actions, missed check-ins, closure reviews.
// Tracks acknowledgement, ownership and waiting time. Doctor-only resolution is explained, not hidden.
import { useQueryClient } from "@tanstack/react-query";
import { Clock, UserRound } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { errorMessage } from "../lib/api";
import { useMe } from "../lib/auth";
import { fmtDateTime, fmtWaiting } from "../lib/format";
import type { ReviewItemOut } from "../lib/types";
import { Banner, Button, cn, Dialog, Field, PriorityBadge, Select, StatusBadge, TextArea, useToast } from "../ui";
import { invalidateScopes, reviewApi, useStaff } from "./hooks";
import { REVIEW_KIND_LABEL } from "./util";

export function ReviewItemCard({ item, showPatient = true }: { item: ReviewItemOut; showPatient?: boolean }) {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState<null | "ack" | "assign" | "resolve">(null);
  const [error, setError] = useState<string | null>(null);
  const [resolveOpen, setResolveOpen] = useState(false);
  const [note, setNote] = useState("");
  const [noteErr, setNoteErr] = useState<string | null>(null);
  const [assignTo, setAssignTo] = useState("");
  const { data: staff } = useStaff();

  const urgent = item.priority === "urgent";
  const canResolve = item.requires_role !== "doctor" || me?.user.role === "doctor";
  const done = item.status === "resolved";

  async function run(kind: "ack" | "assign" | "resolve", fn: () => Promise<unknown>, ok: string) {
    setBusy(kind);
    setError(null);
    try {
      await fn();
      toast({ tone: "success", text: ok });
      await invalidateScopes(qc, ["reviews", "overview", "episode", "episodes", "plan", "activity", "actions", "approvals", "checkins"], item.episode_id);
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  return (
    <article
      className={cn(
        "rounded-lg border bg-surface p-4",
        urgent && !done ? "border-urgent/50 border-l-4 border-l-urgent" : "border-line",
      )}
      aria-label={`${REVIEW_KIND_LABEL[item.kind] ?? item.kind}: ${item.title}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <PriorityBadge priority={item.priority} />
        <StatusBadge domain="review" value={item.status} />
        <span className="text-sm font-bold text-muted">{REVIEW_KIND_LABEL[item.kind] ?? item.kind}</span>
        {item.requires_role === "doctor" && <span className="text-sm text-muted">· Doctor review required</span>}
      </div>
      <h3 className="mt-2 font-bold text-ink">{item.title}</h3>
      {item.summary && <p className="mt-1 text-[15px] text-muted">{item.summary}</p>}
      <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted">
        {showPatient && (
          <div>
            <dt className="sr-only">Patient</dt>
            <dd>
              <Link to={`/hospital/patients/${item.episode_id}`} className="font-bold text-primary-ink underline">
                {item.patient.full_name}
              </Link>
            </dd>
          </div>
        )}
        <div className="flex items-center gap-1">
          <Clock aria-hidden className="size-3.5" />
          <dt className="sr-only">Waiting</dt>
          <dd>{done ? `Resolved ${fmtDateTime(item.resolved_at)}` : `Waiting ${fmtWaiting(item.waiting_minutes)}`}</dd>
        </div>
        <div className="flex items-center gap-1">
          <UserRound aria-hidden className="size-3.5" />
          <dt className="sr-only">Owner</dt>
          <dd>{item.owner ? `Owner: ${item.owner.display_name}` : "No owner yet"}</dd>
        </div>
        {item.acknowledged_by && (
          <div>
            <dt className="sr-only">Acknowledged</dt>
            <dd>
              Acknowledged by {item.acknowledged_by} · {fmtDateTime(item.acknowledged_at)}
            </dd>
          </div>
        )}
      </dl>
      {done && item.resolution_note && (
        <p className="mt-2 rounded-md bg-sunken px-3 py-2 text-sm">
          <span className="font-bold">Resolution ({item.resolved_by}):</span> {item.resolution_note}
        </p>
      )}
      {error && (
        <Banner tone="warning" className="mt-3" live>
          {error}
        </Banner>
      )}
      {!done && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {item.status === "open" && (
            <Button
              size="sm"
              variant={urgent ? "primary" : "secondary"}
              loading={busy === "ack"}
              onClick={() => run("ack", () => reviewApi.acknowledge(item.id), "Acknowledged — you are now the owner.")}
            >
              Acknowledge
            </Button>
          )}
          <div className="flex items-center gap-1.5">
            <label htmlFor={`assign-${item.id}`} className="sr-only">
              Route to staff member
            </label>
            <Select id={`assign-${item.id}`} value={assignTo} onChange={(e) => setAssignTo(e.target.value)} className="min-h-8 w-auto py-1 text-sm">
              <option value="">Route to…</option>
              {(staff ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name} ({u.role})
                </option>
              ))}
            </Select>
            <Button
              size="sm"
              variant="secondary"
              disabled={!assignTo}
              loading={busy === "assign"}
              onClick={() =>
                run("assign", () => reviewApi.assign(item.id, Number(assignTo)), "Routed.").then((ok) => ok && setAssignTo(""))
              }
            >
              Route
            </Button>
          </div>
          {canResolve ? (
            <Button size="sm" variant="secondary" onClick={() => setResolveOpen(true)}>
              Resolve…
            </Button>
          ) : (
            <span className="text-sm text-muted">Only a doctor can resolve this item.</span>
          )}
        </div>
      )}
      <Dialog
        open={resolveOpen}
        onClose={() => setResolveOpen(false)}
        title="Resolve review item"
        description="Record what was done. The note is visible to staff only."
        footer={
          <>
            <Button variant="secondary" onClick={() => setResolveOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={busy === "resolve"}
              onClick={async () => {
                if (!note.trim()) {
                  setNoteErr("Add a short resolution note.");
                  return;
                }
                const ok = await run("resolve", () => reviewApi.resolve(item.id, note.trim()), "Review item resolved.");
                if (ok) {
                  setResolveOpen(false);
                  setNote("");
                }
              }}
            >
              Resolve
            </Button>
          </>
        }
      >
        <Field label="Resolution note" required error={noteErr}>
          {({ id, describedBy, invalid }) => (
            <TextArea
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              rows={4}
              value={note}
              onChange={(e) => {
                setNote(e.target.value);
                setNoteErr(null);
              }}
            />
          )}
        </Field>
      </Dialog>
    </article>
  );
}
