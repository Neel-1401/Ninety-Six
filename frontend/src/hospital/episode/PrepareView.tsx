// Draft / awaiting-review episode (Agent C): extraction review, draft plan (+ diff), nurse submit,
// doctor return-to-draft and activation.
import { RotateCcw, Send, Sparkles } from "lucide-react";
import { useState } from "react";
import { api, errorMessage, isApiError } from "../../lib/api";
import { useMe } from "../../lib/auth";
import type { EpisodeDetail, PlanVersionOut } from "../../lib/types";
import { Banner, Button, Card, CardHeader, Dialog, ErrorState, Field, Skeleton, TextArea, useToast } from "../../ui";
import { useCurrentPlan, useEpisodeMutation, usePlans } from "../hooks";
import { ActivationPanel } from "./ActivationPanel";
import { FactsReview } from "./FactsReview";
import { PlanDiffView, PlanMeta, PlanStepsTable } from "./PlanPanel";

export function PrepareView({ ep }: { ep: EpisodeDetail }) {
  const { data: me } = useMe();
  const toast = useToast();
  const isDoctor = me?.user.role === "doctor";
  const plan = useCurrentPlan(ep.id);
  const plans = usePlans(ep.id);
  const [dialog, setDialog] = useState<null | "regen" | "return">(null);
  const [reason, setReason] = useState("");
  const [reasonErr, setReasonErr] = useState<string | null>(null);

  const submit = useEpisodeMutation(ep.id, () => api.post<EpisodeDetail>(`/api/episodes/${ep.id}/submit`));
  const regen = useEpisodeMutation(ep.id, (r: string) => api.post<PlanVersionOut>(`/api/episodes/${ep.id}/plan/regenerate`, { reason: r || null }));
  const back = useEpisodeMutation(ep.id, (r: string) => api.post<EpisodeDetail>(`/api/episodes/${ep.id}/return-to-draft`, { reason: r }));

  const noPlanYet = plan.isError && isApiError(plan.error) && plan.error.status === 404;

  return (
    <div className="space-y-5">
      {ep.status === "draft" ? (
        <Banner
          tone="info"
          title="Draft — being prepared"
          actions={
            ep.permissions.can_submit && (
              <Button
                icon={<Send aria-hidden className="size-4" />}
                loading={submit.isPending}
                onClick={() => submit.mutate(undefined, { onSuccess: () => toast({ tone: "success", text: "Submitted. The agent drafted a plan for doctor review." }) })}
              >
                Submit for doctor review
              </Button>
            )
          }
        >
          {isDoctor
            ? "A nurse is checking the extracted information. You can review once it is submitted."
            : "Check every extracted item against the summary, correct anything wrong and enter missing items from the record. Submitting asks the agent to draft the 96-hour plan for the doctor."}
          {submit.isError && <span className="mt-1 block font-bold">{errorMessage(submit.error)}</span>}
        </Banner>
      ) : (
        <Banner
          tone="warning"
          title="Awaiting doctor review"
          actions={
            isDoctor && (
              <Button variant="secondary" icon={<RotateCcw aria-hidden className="size-4" />} onClick={() => { setReason(""); setReasonErr(null); setDialog("return"); }}>
                Return to draft…
              </Button>
            )
          }
        >
          {isDoctor
            ? "Review the extracted information and the draft plan, confirm each item, choose any routine authorisation, then activate."
            : `Waiting for ${ep.team.doctor?.display_name ?? "the doctor"} to review and activate. Editing information sends it back to draft.`}
        </Banner>
      )}

      <FactsReview ep={ep} editable={ep.permissions.can_edit_draft} />

      <Card>
        <CardHeader
          title="Draft 96-hour plan"
          description="Proposed by the agent and validated by deterministic rules. Nothing external happens until the doctor activates it and approves each external action (or a bounded routine authorisation covers it)."
          actions={
            (ep.permissions.can_submit || ep.permissions.can_activate || ep.permissions.can_edit_draft) &&
            !noPlanYet && (
              <Button size="sm" variant="secondary" icon={<Sparkles aria-hidden className="size-4" />} onClick={() => { setReason(""); setDialog("regen"); }}>
                Regenerate draft
              </Button>
            )
          }
        />
        <div className="space-y-4 px-4 py-4">
          {plan.isLoading && <Skeleton className="h-32" />}
          {noPlanYet && <p className="text-muted">No plan yet. The agent drafts the plan when the nurse submits the episode for doctor review.</p>}
          {plan.isError && !noPlanYet && <ErrorState error={plan.error} onRetry={() => plan.refetch()} />}
          {plan.data && (
            <>
              <PlanMeta plan={plan.data} />
              {plans.data && plans.data.length > 1 && (
                <details open className="rounded-md border border-line px-3 py-2">
                  <summary className="cursor-pointer font-bold text-primary-ink">What changed between plan versions</summary>
                  <div className="pt-3">
                    <PlanDiffView versions={plans.data} current={plan.data} />
                  </div>
                </details>
              )}
              <PlanStepsTable steps={plan.data.steps} />
            </>
          )}
        </div>
      </Card>

      {ep.status === "awaiting_review" && isDoctor && ep.permissions.can_activate && plan.data && <ActivationPanel ep={ep} plan={plan.data} />}

      <Dialog
        open={dialog === "regen"}
        onClose={() => setDialog(null)}
        title="Regenerate the draft plan"
        description="Creates a new draft version from the current (corrected) information. The previous draft is discarded and kept for comparison."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              loading={regen.isPending}
              onClick={() =>
                regen.mutate(reason.trim(), {
                  onSuccess: (pv) => {
                    toast({ tone: "success", text: `Draft plan v${pv.version} created.` });
                    setDialog(null);
                  },
                })
              }
            >
              Regenerate
            </Button>
          </>
        }
      >
        <Field label="Reason (optional)" error={regen.isError ? errorMessage(regen.error) : null}>
          {({ id, describedBy, invalid }) => <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
      </Dialog>

      <Dialog
        open={dialog === "return"}
        onClose={() => setDialog(null)}
        title="Return to draft"
        description="Sends the episode back to the nurse. Tell them what needs to change."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              loading={back.isPending}
              onClick={() => {
                if (!reason.trim()) {
                  setReasonErr("A reason is required.");
                  return;
                }
                back.mutate(reason.trim(), {
                  onSuccess: () => {
                    toast({ tone: "success", text: "Returned to draft." });
                    setDialog(null);
                  },
                });
              }}
            >
              Return to draft
            </Button>
          </>
        }
      >
        <Field label="What needs to change" required error={reasonErr ?? (back.isError ? errorMessage(back.error) : null)}>
          {({ id, describedBy, invalid }) => (
            <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={3} value={reason} onChange={(e) => { setReason(e.target.value); setReasonErr(null); }} />
          )}
        </Field>
      </Dialog>
    </div>
  );
}
