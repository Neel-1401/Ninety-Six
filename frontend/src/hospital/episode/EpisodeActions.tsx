// Episode-level actions (Agent C): invite patient, assign team, request replan, close episode.
import { MailPlus, RefreshCw, Users } from "lucide-react";
import { useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { fmtDateTime } from "../../lib/format";
import type { EpisodeDetail, MessageOut, PlanVersionOut } from "../../lib/types";
import { Banner, Button, Dialog, Field, Select, StatusBadge, TextArea, useToast } from "../../ui";
import { useEpisodeMutation, useStaff } from "../hooks";

export function EpisodeActions({ ep }: { ep: EpisodeDetail }) {
  const { data: me } = useMe();
  const isDoctor = me?.user.role === "doctor";
  const [open, setOpen] = useState<null | "invite" | "team" | "replan" | "close">(null);
  const canInvite = ep.status !== "closed" && ep.status !== "cancelled";
  return (
    <div className="flex flex-wrap items-center gap-2">
      {canInvite && (
        <Button variant="secondary" size="sm" icon={<MailPlus aria-hidden className="size-4" />} onClick={() => setOpen("invite")}>
          {ep.invitation ? "Patient invitation" : "Invite patient"}
        </Button>
      )}
      {ep.permissions.can_assign_team && (
        <Button variant="secondary" size="sm" icon={<Users aria-hidden className="size-4" />} onClick={() => setOpen("team")}>
          Care team
        </Button>
      )}
      {ep.status === "active" && (
        <Button variant="secondary" size="sm" icon={<RefreshCw aria-hidden className="size-4" />} onClick={() => setOpen("replan")}>
          Request replan
        </Button>
      )}
      {ep.status === "awaiting_closure_review" && isDoctor && ep.permissions.can_close && (
        <Button size="sm" onClick={() => setOpen("close")}>
          Complete closure review
        </Button>
      )}
      <InviteDialog ep={ep} open={open === "invite"} onClose={() => setOpen(null)} />
      <TeamDialog ep={ep} open={open === "team"} onClose={() => setOpen(null)} />
      <ReplanDialog ep={ep} open={open === "replan"} onClose={() => setOpen(null)} />
      <CloseDialog ep={ep} open={open === "close"} onClose={() => setOpen(null)} />
    </div>
  );
}

function InviteDialog({ ep, open, onClose }: { ep: EpisodeDetail; open: boolean; onClose: () => void }) {
  const toast = useToast();
  const [result, setResult] = useState<{ link: string; message: MessageOut; invitation: { status: string; expires_at: string } } | null>(null);
  const m = useEpisodeMutation(ep.id, () =>
    api.post<{ link: string; message: MessageOut; invitation: { status: string; expires_at: string } }>(`/api/episodes/${ep.id}/invite`),
  );
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Patient invitation"
      description="Sends a short SMS with a sign-in link. It contains no medical information, and the link alone grants no access: the patient must also verify a one-time code sent to the registered phone."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button
            loading={m.isPending}
            onClick={() =>
              m.mutate(undefined, {
                onSuccess: (r) => {
                  setResult(r);
                  toast({ tone: "success", text: "Invitation recorded." });
                },
              })
            }
          >
            {ep.invitation ? "Send a new invitation" : "Send invitation"}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        {ep.invitation && (
          <p>
            Current invitation: <strong>{ep.invitation.status}</strong> · created {fmtDateTime(ep.invitation.created_at)} · expires{" "}
            {fmtDateTime(ep.invitation.expires_at)}
          </p>
        )}
        {m.isError && <Banner tone="warning">{errorMessage(m.error)}</Banner>}
        {result && (
          <Banner tone="info" title="Invitation sent">
            <p className="flex flex-wrap items-center gap-2">
              Message status: <StatusBadge domain="message" value={result.message.status} />
            </p>
            <p className="mt-1 break-all">
              Link: <code className="font-mono text-xs">{result.link}</code>
            </p>
          </Banner>
        )}
      </div>
    </Dialog>
  );
}

function TeamDialog({ ep, open, onClose }: { ep: EpisodeDetail; open: boolean; onClose: () => void }) {
  const toast = useToast();
  const doctors = useStaff("doctor");
  const nurses = useStaff("nurse");
  const [doc, setDoc] = useState(String(ep.team.doctor?.user_id ?? ""));
  const [nurse, setNurse] = useState(String(ep.team.nurse?.user_id ?? ""));
  const m = useEpisodeMutation(ep.id, () => api.put(`/api/episodes/${ep.id}/team`, { doctor_id: Number(doc), nurse_id: Number(nurse) }));
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Assign care team"
      description="Only assigned staff can open this episode. Changing the doctor changes who can approve actions."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={m.isPending}
            disabled={!doc || !nurse}
            onClick={() =>
              m.mutate(undefined, {
                onSuccess: () => {
                  toast({ tone: "success", text: "Care team updated." });
                  onClose();
                },
              })
            }
          >
            Save team
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Doctor" required>
          {({ id }) => (
            <Select id={id} value={doc} onChange={(e) => setDoc(e.target.value)}>
              <option value="">Choose…</option>
              {(doctors.data ?? []).map((d) => (
                <option key={d.id} value={d.id}>
                  {d.display_name}
                  {d.specialty ? ` (${d.specialty})` : ""}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Nurse" required>
          {({ id }) => (
            <Select id={id} value={nurse} onChange={(e) => setNurse(e.target.value)}>
              <option value="">Choose…</option>
              {(nurses.data ?? []).map((n) => (
                <option key={n.id} value={n.id}>
                  {n.display_name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        {m.isError && <Banner tone="warning" className="sm:col-span-2">{errorMessage(m.error)}</Banner>}
      </div>
    </Dialog>
  );
}

function ReplanDialog({ ep, open, onClose }: { ep: EpisodeDetail; open: boolean; onClose: () => void }) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const m = useEpisodeMutation(ep.id, () => api.post<PlanVersionOut>(`/api/episodes/${ep.id}/replan`, { reason: reason.trim() }));
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Request a replan"
      description="The agent creates a new plan version. Completed work is kept; only affected pending steps change. New or changed external actions come back for doctor approval."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={m.isPending}
            onClick={() => {
              if (!reason.trim()) {
                setErr("Say why a replan is needed.");
                return;
              }
              m.mutate(undefined, {
                onSuccess: (pv) => {
                  toast({ tone: "success", text: `Plan v${pv.version} created.` });
                  setReason("");
                  onClose();
                },
              });
            }}
          >
            Create new plan version
          </Button>
        </>
      }
    >
      <Field label="Reason" required error={err ?? (m.isError ? errorMessage(m.error) : null)}>
        {({ id, describedBy, invalid }) => (
          <TextArea
            id={id}
            aria-describedby={describedBy}
            invalid={invalid}
            rows={3}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              setErr(null);
            }}
          />
        )}
      </Field>
    </Dialog>
  );
}

function CloseDialog({ ep, open, onClose }: { ep: EpisodeDetail; open: boolean; onClose: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const m = useEpisodeMutation(ep.id, () => api.post(`/api/episodes/${ep.id}/close`, { note: note.trim() }));
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Complete closure review"
      description="Records that you reviewed the 96-hour summary. Closing the episode is NOT a medical clearance. Open urgent items must be resolved first."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={m.isPending}
            onClick={() => {
              if (!note.trim()) {
                setErr("Add a closure note.");
                return;
              }
              m.mutate(undefined, {
                onSuccess: () => {
                  toast({ tone: "success", text: "Episode closed after review." });
                  onClose();
                },
              });
            }}
          >
            Close episode
          </Button>
        </>
      }
    >
      <Field label="Closure note" required error={err ?? (m.isError ? errorMessage(m.error) : null)}>
        {({ id, describedBy, invalid }) => (
          <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={4} value={note} onChange={(e) => { setNote(e.target.value); setErr(null); }} />
        )}
      </Field>
    </Dialog>
  );
}
