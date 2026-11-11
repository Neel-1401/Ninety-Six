// Doctor activation (Agent C): confirm each extracted fact, optionally grant a BOUNDED routine
// authorisation (exact templates, recipient, schedule, max/day, expiry = window end), activate.
// A 409 stale_version means the draft plan changed during review: refetch and say so.
import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, errorMessage, isApiError } from "../../lib/api";
import { fmtDateTime, fmtTPlus } from "../../lib/format";
import type { EpisodeDetail, PlanVersionOut } from "../../lib/types";
import { Banner, Button, Card, CardHeader, Checkbox, Field, TextInput, cn, useToast } from "../../ui";
import { invalidateScopes } from "../hooks";
import { CATEGORY_LABEL, factText, stepTemplateId, TEMPLATE_LABEL } from "../util";

const ROUTINE_TEMPLATES = ["checkin_reminder_v1", "closing_v1"];

export function ActivationPanel({ ep, plan }: { ep: EpisodeDetail; plan: PlanVersionOut }) {
  const qc = useQueryClient();
  const toast = useToast();
  const facts = ep.facts.filter((f) => f.active && !f.is_missing && f.review_state !== "rejected");
  const missing = ep.facts.filter((f) => f.active && f.is_missing);
  const [confirmed, setConfirmed] = useState<Set<number>>(new Set());
  const [stale, setStale] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // drop confirmations for facts that no longer exist (e.g. after a correction)
  useEffect(() => {
    setConfirmed((c) => new Set([...c].filter((id) => facts.some((f) => f.id === id))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ep.facts]);

  // Routine-eligible steps in this plan define the offsets that CAN be authorised.
  const eligible = useMemo(
    () => plan.steps.filter((s) => s.approval_requirement === "routine_eligible"),
    [plan.steps],
  );
  const templatesInPlan = useMemo(() => {
    const ts = new Set(eligible.map((s) => stepTemplateId(s)).filter((t): t is string => !!t));
    return ts.size ? [...ts] : ROUTINE_TEMPLATES;
  }, [eligible]);
  const offsetsInPlan = useMemo(() => [...new Set(eligible.map((s) => s.offset_hours))].sort((a, b) => a - b), [eligible]);

  const [routineOn, setRoutineOn] = useState(false);
  const [templates, setTemplates] = useState<Set<string>>(new Set());
  const [offsets, setOffsets] = useState<Set<number>>(new Set());
  const [maxPerDay, setMaxPerDay] = useState("3");

  function enableRoutine(on: boolean) {
    setRoutineOn(on);
    if (on) {
      setTemplates(new Set(templatesInPlan));
      setOffsets(new Set(offsetsInPlan));
    }
  }

  const allConfirmed = facts.length > 0 && facts.every((f) => confirmed.has(f.id));
  const maxN = Number(maxPerDay);
  const routineValid = !routineOn || (templates.size > 0 && offsets.size > 0 && Number.isInteger(maxN) && maxN >= 1 && maxN <= 12);

  const scopeText = routineOn
    ? `Ninety-Six may send ${[...templates].map((t) => `“${TEMPLATE_LABEL[t] ?? t}”`).join(" and ") || "(no template)"} to the patient's registered number only, at ${
        [...offsets].sort((a, b) => a - b).map(fmtTPlus).join(", ") || "(no times)"
      }, at most ${maxPerDay}/day, until the monitoring window ends (${fmtDateTime(ep.window_ends_at)}). The exact template wording is fixed; any other message, any wording change, caregiver messages, bookings and escalations still need your individual approval. You can revoke this at any time.`
    : "No routine authorisation: every external action, including each check-in reminder, will wait for your individual approval.";

  async function activate() {
    if (!allConfirmed) {
      setErr("Confirm every extracted item first (or correct it / send the draft back).");
      return;
    }
    if (!routineValid) {
      setErr("Complete the routine authorisation scope, or switch it off.");
      return;
    }
    setBusy(true);
    setErr(null);
    setStale(null);
    try {
      await api.post(`/api/episodes/${ep.id}/activate`, {
        plan_version_id: plan.id,
        confirm_fact_ids: facts.map((f) => f.id),
        routine: routineOn
          ? { template_ids: [...templates], recipient_roles: ["patient"], offsets_hours: [...offsets].sort((a, b) => a - b), max_per_day: maxN }
          : null,
      });
      toast({ tone: "success", text: `Plan v${plan.version} activated. Monitoring has started.` });
      await invalidateScopes(qc, ["episode", "episodes", "plan", "actions", "approvals", "overview", "activity", "memory", "routine"], ep.id);
    } catch (e) {
      if (isApiError(e) && e.status === 409) {
        setStale(e.message);
        await invalidateScopes(qc, ["episode", "plan"], ep.id);
      } else setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-primary/40">
      <CardHeader
        title={`Doctor review: activate plan v${plan.version}`}
        description="Activating confirms the extracted information as reviewed instructions (what the patient will see) and starts the plan. External actions still follow the approval policy."
      />
      <div className="space-y-5 px-4 py-4">
        {stale && (
          <Banner tone="warning" live title="The draft plan changed while you were reviewing">
            {stale} The latest version is now shown above — review it, then activate again.
          </Banner>
        )}

        <section aria-labelledby="confirm-facts">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id="confirm-facts" className="font-bold">
              1. Confirm extracted information ({confirmed.size} of {facts.length})
            </h3>
            <Button size="sm" variant="secondary" onClick={() => setConfirmed(new Set(facts.map((f) => f.id)))}>
              I have checked every item against the summary
            </Button>
          </div>
          <ul className="mt-2 grid gap-1.5 md:grid-cols-2">
            {facts.map((f) => (
              <li key={f.id} className={cn("rounded-md border px-3 py-2", confirmed.has(f.id) ? "border-primary/40 bg-primary-soft/40" : "border-line")}>
                <Checkbox
                  label={`${CATEGORY_LABEL[f.category] ?? f.category}: ${factText(f)}`}
                  description={`${f.source_ref ?? "no source"} · ${f.source_kind}`}
                  checked={confirmed.has(f.id)}
                  onChange={(e) =>
                    setConfirmed((c) => {
                      const n = new Set(c);
                      if (e.target.checked) n.add(f.id);
                      else n.delete(f.id);
                      return n;
                    })
                  }
                />
              </li>
            ))}
          </ul>
          {missing.length > 0 && (
            <p className="mt-2 text-sm text-attention-ink">
              Not provided ({missing.length}): {missing.map((m) => m.label).join(", ")}. These will be shown to the patient as “not provided”.
            </p>
          )}
        </section>

        <section aria-labelledby="routine-scope">
          <h3 id="routine-scope" className="font-bold">
            2. Routine authorisation (optional)
          </h3>
          <div className="mt-2 space-y-3 rounded-md border border-line p-3">
            <Checkbox
              label="Authorise routine, non-clinical reminders for this episode"
              description="Off by default. Only templated reminders to the patient can be covered."
              checked={routineOn}
              onChange={(e) => enableRoutine(e.target.checked)}
            />
            {routineOn && (
              <div className="grid gap-4 md:grid-cols-3">
                <fieldset>
                  <legend className="text-sm font-bold">Templates</legend>
                  <div className="mt-1 space-y-1.5">
                    {templatesInPlan.map((t) => (
                      <Checkbox
                        key={t}
                        label={TEMPLATE_LABEL[t] ?? t}
                        checked={templates.has(t)}
                        onChange={(e) =>
                          setTemplates((s) => {
                            const n = new Set(s);
                            if (e.target.checked) n.add(t);
                            else n.delete(t);
                            return n;
                          })
                        }
                      />
                    ))}
                  </div>
                </fieldset>
                <fieldset>
                  <legend className="text-sm font-bold">Schedule (hours after discharge)</legend>
                  {offsetsInPlan.length === 0 ? (
                    <p className="mt-1 text-sm text-muted">This plan has no routine-eligible steps.</p>
                  ) : (
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1.5">
                      {offsetsInPlan.map((o) => (
                        <Checkbox
                          key={o}
                          label={fmtTPlus(o)}
                          checked={offsets.has(o)}
                          onChange={(e) =>
                            setOffsets((s) => {
                              const n = new Set(s);
                              if (e.target.checked) n.add(o);
                              else n.delete(o);
                              return n;
                            })
                          }
                        />
                      ))}
                    </div>
                  )}
                </fieldset>
                <div className="space-y-2">
                  <p className="text-sm">
                    <span className="font-bold">Recipient:</span> patient only. Caregiver messages always need individual approval.
                  </p>
                  <Field label="Maximum per day">
                    {({ id }) => <TextInput id={id} inputMode="numeric" value={maxPerDay} onChange={(e) => setMaxPerDay(e.target.value)} className="w-24" />}
                  </Field>
                  <p className="text-sm text-muted">Expires: {fmtDateTime(ep.window_ends_at)}</p>
                </div>
              </div>
            )}
            <p className="flex items-start gap-2 rounded-md bg-sunken px-3 py-2 text-sm">
              <ShieldCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
              <span>{scopeText}</span>
            </p>
          </div>
        </section>

        {err && (
          <Banner tone="warning" live>
            {err}
          </Banner>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button loading={busy} disabled={!allConfirmed || !routineValid} onClick={activate}>
            Activate plan v{plan.version}
          </Button>
          {!allConfirmed && <span className="text-sm text-muted">Confirm all {facts.length} items to activate.</span>}
        </div>
      </div>
    </Card>
  );
}
