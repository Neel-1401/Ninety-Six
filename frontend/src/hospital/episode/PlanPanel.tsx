// Plan rendering (Agent C): version metadata, validation/fallback visibility, step table and a
// step-by-step diff between versions (matched by step_key).
import { ArrowRight, GitCompare, Minus, Plus, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { fmtDateTime, fmtTPlus } from "../../lib/format";
import type { PlanStepOut, PlanVersionOut } from "../../lib/types";
import { Badge, Banner, Select, SourceBadge, StatusBadge, cn } from "../../ui";
import { actionLabel, stepMessageBody, stepTemplateId, TEMPLATE_LABEL, TRIGGER_LABEL } from "../util";

const REQ_LABEL: Record<string, string> = {
  none: "Internal — no approval",
  routine_eligible: "Routine-eligible",
  explicit: "Explicit approval",
};

export function PlanMeta({ plan }: { plan: PlanVersionOut }) {
  const v = plan.validation;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={plan.status === "active" ? "primary" : plan.status === "draft" ? "attention" : "neutral"}>
          Plan v{plan.version} · {plan.status}
        </Badge>
        <span className="text-muted">
          {TRIGGER_LABEL[plan.trigger] ?? plan.trigger} · source <strong>{plan.source}</strong> · created {fmtDateTime(plan.created_at)} by {plan.created_by}
          {plan.activated_by ? ` · activated by ${plan.activated_by} ${fmtDateTime(plan.activated_at)}` : ""}
        </span>
      </div>
      {plan.status === "draft" && (
        <p className="text-sm font-bold text-attention-ink">Agent-generated draft — not yet reviewed by a clinician.</p>
      )}
      {v.fallback_used && (
        <Banner tone="warning" title="Rule-based fallback plan">
          {v.llm_attempted
            ? "The language model's plan failed validation and was rejected, so the deterministic rule-based planner was used."
            : "No language model was used; the deterministic rule-based planner produced this plan."}
          {v.errors.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-sm">
              {v.errors.slice(0, 6).map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </Banner>
      )}
      {plan.rationale && <p className="max-w-[90ch] text-[15px] text-muted">{plan.rationale}</p>}
    </div>
  );
}

export function PlanStepsTable({ steps, highlightKeys }: { steps: PlanStepOut[]; highlightKeys?: Set<string> }) {
  if (steps.length === 0) return <p className="text-muted">This plan has no steps.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-left text-sm">
        <caption className="sr-only">Plan steps</caption>
        <thead className="bg-sunken text-xs uppercase tracking-wide text-muted">
          <tr>
            <th scope="col" className="px-3 py-2">When</th>
            <th scope="col" className="px-3 py-2">Step</th>
            <th scope="col" className="px-3 py-2">Type</th>
            <th scope="col" className="px-3 py-2">Approval</th>
            <th scope="col" className="px-3 py-2">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {steps.map((s) => {
            const body = stepMessageBody(s);
            const tpl = stepTemplateId(s);
            return (
              <tr key={s.id} id={`step-${s.id}`} className={cn("align-top", highlightKeys?.has(s.step_key) && "bg-attention/40")}>
                <td className="whitespace-nowrap px-3 py-2.5">
                  <span className="font-bold">{fmtTPlus(s.offset_hours)}</span>
                  <span className="block text-muted">{fmtDateTime(s.due_at)}</span>
                </td>
                <td className="px-3 py-2.5">
                  <span className="inline-flex flex-wrap items-center gap-1.5">
                    <span className="font-bold">{s.title}</span>
                    <SourceBadge
                      ai={s.origin === "ai"}
                      title={s.origin === "ai" ? "Condition-specific step suggested by the AI; validated (tool, timing, arguments) before it was added" : "From the deterministic rule plan"}
                    />
                  </span>
                  {s.purpose && <span className="block text-muted">{s.purpose}</span>}
                  {tpl && <span className="block text-xs text-muted">Template: {TEMPLATE_LABEL[tpl] ?? tpl}</span>}
                  {body && <span className="mt-1 block max-w-[70ch] whitespace-pre-wrap rounded bg-sunken px-2 py-1 text-xs">{body}</span>}
                  {s.depends_on.length > 0 && <span className="mt-1 block text-xs text-muted">Depends on: {s.depends_on.join(", ")}</span>}
                  {s.rationale && (
                    <details className="mt-1 text-xs text-muted">
                      <summary className="cursor-pointer font-bold text-primary-ink">Why</summary>
                      {s.rationale}
                    </details>
                  )}
                </td>
                <td className="px-3 py-2.5">{actionLabel(s.action_type)}</td>
                <td className="px-3 py-2.5">
                  <span className={cn(s.approval_requirement === "explicit" && "font-bold")}>{REQ_LABEL[s.approval_requirement] ?? s.approval_requirement}</span>
                  {s.action && (
                    <span className="mt-1 block">
                      <StatusBadge domain="approval" value={s.action.approval_state} />
                    </span>
                  )}
                </td>
                <td className="px-3 py-2.5">
                  <StatusBadge domain="step" value={s.status} />
                  {s.executed_at && <span className="mt-1 block text-xs text-muted">Ran {fmtDateTime(s.executed_at)}</span>}
                  {s.status_reason && <span className="mt-1 block text-xs text-muted">{s.status_reason}</span>}
                  {s.result_summary && <span className="mt-1 block text-xs">{s.result_summary}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ diff
interface StepDiff {
  key: string;
  kind: "added" | "removed" | "changed";
  title: string;
  changes: string[];
}

function stepDiffs(oldSteps: PlanStepOut[], newSteps: PlanStepOut[]): StepDiff[] {
  const o = new Map(oldSteps.map((s) => [s.step_key, s]));
  const n = new Map(newSteps.map((s) => [s.step_key, s]));
  const out: StepDiff[] = [];
  for (const [k, s] of n) {
    const prev = o.get(k);
    if (!prev) {
      out.push({ key: k, kind: "added", title: s.title, changes: [`${fmtTPlus(s.offset_hours)} · ${actionLabel(s.action_type)}`] });
      continue;
    }
    const ch: string[] = [];
    if (prev.title !== s.title) ch.push(`Title: “${prev.title}” → “${s.title}”`);
    if (prev.offset_hours !== s.offset_hours) ch.push(`Timing: ${fmtTPlus(prev.offset_hours)} → ${fmtTPlus(s.offset_hours)}`);
    if (prev.action_type !== s.action_type) ch.push(`Type: ${actionLabel(prev.action_type)} → ${actionLabel(s.action_type)}`);
    if (prev.approval_requirement !== s.approval_requirement) ch.push(`Approval: ${REQ_LABEL[prev.approval_requirement]} → ${REQ_LABEL[s.approval_requirement]}`);
    if (JSON.stringify(prev.depends_on) !== JSON.stringify(s.depends_on))
      ch.push(`Depends on: ${prev.depends_on.join(", ") || "nothing"} → ${s.depends_on.join(", ") || "nothing"}`);
    if (JSON.stringify(prev.arguments) !== JSON.stringify(s.arguments)) {
      const b0 = stepMessageBody(prev);
      const b1 = stepMessageBody(s);
      ch.push(b0 !== b1 && b1 ? "Message text changed" : "Arguments changed");
    }
    if (ch.length) out.push({ key: k, kind: "changed", title: s.title, changes: ch });
  }
  for (const [k, s] of o) if (!n.has(k)) out.push({ key: k, kind: "removed", title: s.title, changes: [`${fmtTPlus(s.offset_hours)} · ${actionLabel(s.action_type)}`] });
  return out;
}

export function PlanDiffView({ versions, current }: { versions: PlanVersionOut[]; current: PlanVersionOut }) {
  const older = useMemo(() => versions.filter((v) => v.version < current.version).sort((a, b) => b.version - a.version), [versions, current.version]);
  const defaultBase = older.find((v) => v.id === current.based_on_version_id) ?? older[0];
  const [baseId, setBaseId] = useState<number | null>(defaultBase?.id ?? null);
  const [showKept, setShowKept] = useState(false);
  const meaningful = current.change_summary.filter((c) => c.change !== "kept");
  const keptCount = current.change_summary.length - meaningful.length;
  const shownChanges = showKept ? current.change_summary : meaningful;
  const base = older.find((v) => v.id === baseId) ?? defaultBase;

  if (!base) {
    return <p className="text-sm text-muted">This is the first plan version — nothing to compare yet.</p>;
  }
  const diffs = stepDiffs(base.steps, current.steps);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <GitCompare aria-hidden className="size-4 text-primary" />
        <label htmlFor={`diff-base-${current.id}`} className="font-bold">
          Compare v{current.version} with
        </label>
        <Select id={`diff-base-${current.id}`} value={base.id} onChange={(e) => setBaseId(Number(e.target.value))} className="min-h-8 w-auto py-1">
          {older.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.version} ({v.status}, {TRIGGER_LABEL[v.trigger] ?? v.trigger})
            </option>
          ))}
        </Select>
      </div>
      {current.change_summary.length > 0 && (
        <div className="rounded-md border border-line bg-sunken/50 px-3 py-2">
          <p className="text-sm font-bold">Agent's explanation of what changed and why ({TRIGGER_LABEL[current.trigger] ?? current.trigger})</p>
          <ul className="mt-1 space-y-1 text-sm">
            {shownChanges.length === 0 && <li className="text-muted">No steps added, removed or changed.</li>}
            {shownChanges.map((c, i) => (
              <li key={i} className="flex items-start gap-2">
                <ChangeIcon kind={c.change} />
                <span>
                  <span className="font-bold">{c.change}</span> · <span className="font-mono text-xs">{c.step_key}</span> — {c.description}
                </span>
              </li>
            ))}
          </ul>
          {keptCount > 0 && (
            <button type="button" aria-expanded={showKept} onClick={() => setShowKept((v) => !v)} className="mt-1 text-xs font-bold text-primary-ink underline cursor-pointer">
              {showKept ? "Hide" : "Show"} {keptCount} unchanged step{keptCount === 1 ? "" : "s"}
            </button>
          )}
        </div>
      )}
      {diffs.length === 0 ? (
        <p className="text-sm text-muted">No step differences between v{base.version} and v{current.version}.</p>
      ) : (
        <ul className="space-y-1.5 text-sm" aria-label={`Step differences v${base.version} to v${current.version}`}>
          {diffs.map((d) => (
            <li key={d.key + d.kind} className="flex items-start gap-2 rounded-md border border-line px-3 py-2">
              <ChangeIcon kind={d.kind} />
              <div className="min-w-0">
                <p>
                  <span className="font-bold capitalize">{d.kind}</span>: {d.title} <span className="font-mono text-xs text-muted">({d.key})</span>
                </p>
                <ul className="text-muted">
                  {d.changes.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ChangeIcon({ kind }: { kind: string }) {
  const cls = "mt-0.5 size-4 shrink-0";
  if (kind === "added") return <Plus aria-hidden className={cn(cls, "text-primary")} />;
  if (kind === "removed") return <Minus aria-hidden className={cn(cls, "text-urgent")} />;
  if (kind === "changed") return <RefreshCw aria-hidden className={cn(cls, "text-attention-strong")} />;
  return <ArrowRight aria-hidden className={cn(cls, "text-muted")} />;
}
