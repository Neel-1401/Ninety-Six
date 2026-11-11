// 96-hour timeline (Agent C). Lanes by step type; markers carry an icon whose SHAPE encodes the
// status (plus colour as a secondary cue), every marker has a text label for assistive tech and a
// tooltip, and a visible legend maps icons to words. Current-time marker uses the SERVER's
// simulated clock; plan-version markers show when the plan changed.
import { useMemo } from "react";
import { fmtDateTime, fmtTPlus } from "../../lib/format";
import type { PlanStepOut, PlanVersionOut } from "../../lib/types";
import { cn, statusMeta } from "../../ui";
import { laneOf } from "../util";

const LANES: { id: ReturnType<typeof laneOf>; label: string }[] = [
  { id: "messages", label: "Messages" },
  { id: "checkins", label: "Check-ins" },
  { id: "external", label: "Bookings & alerts" },
  { id: "internal", label: "Internal steps" },
];
const TICKS = [0, 12, 24, 36, 48, 60, 72, 84, 96];
const ROW_H = 30;

const toneCls: Record<string, string> = {
  neutral: "bg-surface border-line-strong text-neutral-ink",
  primary: "bg-primary-soft border-primary/50 text-primary-ink",
  attention: "bg-attention border-attention-strong text-attention-ink",
  urgent: "bg-urgent text-white border-urgent",
  done: "bg-primary text-white border-primary",
};

const LEGEND = ["planned", "blocked", "awaiting_approval", "ready", "executed", "failed", "unknown", "skipped", "superseded"];

export function Timeline({
  steps,
  dischargeAt,
  nowIso,
  versions,
  onSelect,
}: {
  steps: PlanStepOut[];
  dischargeAt: string;
  nowIso: string | undefined;
  versions: PlanVersionOut[];
  onSelect: (stepId: number) => void;
}) {
  const nowH = nowIso ? (new Date(nowIso).getTime() - new Date(dischargeAt).getTime()) / 3_600_000 : null;
  // Padded track: 22px before T+0 and 34px after T+96 so edge markers/labels are never clipped.
  const pct = (h: number) => `calc(22px + (100% - 56px) * ${Math.min(Math.max(h, 0), 96) / 96})`;

  const lanes = useMemo(() => {
    return LANES.map((lane) => {
      const items = steps.filter((s) => laneOf(s.action_type) === lane.id).sort((a, b) => a.offset_hours - b.offset_hours);
      const levels: number[] = []; // last offset placed at each level
      const placed = items.map((s) => {
        let lvl = levels.findIndex((last) => s.offset_hours - last >= 4.5);
        if (lvl === -1) {
          lvl = levels.length;
          levels.push(s.offset_hours);
        } else levels[lvl] = s.offset_hours;
        return { s, lvl };
      });
      return { ...lane, placed, rows: Math.max(1, levels.length) };
    });
  }, [steps]);

  // Versions activated/created within ~2 h of each other share one marker ("v1→v2").
  const versionMarks = versions
    .filter((v) => v.status !== "discarded" && v.status !== "draft")
    .map((v) => ({ v: v.version, h: (new Date(v.activated_at ?? v.created_at).getTime() - new Date(dischargeAt).getTime()) / 3_600_000 }))
    .filter((m) => m.h >= -48)
    .sort((a, b) => a.v - b.v)
    .reduce<{ label: string; h: number; key: number }[]>((acc, m) => {
      const last = acc[acc.length - 1];
      if (last && Math.abs(Math.max(m.h, 0) - Math.max(last.h, 0)) < 2) {
        last.label = `${last.label.split("→")[0]}→v${m.v}`;
        last.h = m.h;
      } else acc.push({ label: `v${m.v}`, h: m.h, key: m.v });
      return acc;
    }, []);

  const blocked = steps.filter((s) => s.status === "awaiting_approval").length;

  return (
    <div>
      <p className="sr-only">
        96-hour timeline. {nowH != null ? `Current time ${fmtTPlus(nowH)}.` : ""} {steps.length} steps, {blocked} waiting for approval. The step list below has the
        same information as text.
      </p>
      <div className="overflow-x-auto pb-2">
        <div className="relative min-w-[760px] pt-5">
          {/* ticks */}
          <div className="relative ml-36 h-7 border-b border-line">
            {TICKS.map((t) => (
              <span key={t} className="absolute top-0 -translate-x-1/2 text-xs whitespace-nowrap text-muted" style={{ left: pct(t) }}>
                T+{t} h
              </span>
            ))}
          </div>
          {/* lanes */}
          <div className="relative">
            {lanes.map((lane) => (
              <div key={lane.id} className="flex border-b border-line/70">
                <div className="flex w-36 shrink-0 items-center py-1.5 pr-2 text-sm font-bold text-muted">{lane.label}</div>
                <div className="relative flex-1" style={{ height: lane.rows * ROW_H + 10 }}>
                  {TICKS.map((t) => (
                    <span key={t} aria-hidden className="absolute top-0 bottom-0 w-px bg-line/60" style={{ left: pct(t) }} />
                  ))}
                  {lane.placed.map(({ s, lvl }) => {
                    const m = statusMeta("step", s.status);
                    const Icon = m.icon;
                    const label = `${fmtTPlus(s.offset_hours)} · ${s.title} · ${m.label}${s.executed_at ? ` · ran ${fmtDateTime(s.executed_at)}` : ""}`;
                    return (
                      <button
                        key={s.id}
                        type="button"
                        onClick={() => onSelect(s.id)}
                        aria-label={label}
                        title={label}
                        className={cn(
                          "absolute inline-flex h-6 -translate-x-1/2 items-center gap-1 rounded-full border px-1.5 text-[11px] font-bold whitespace-nowrap shadow-[var(--shadow-card)] cursor-pointer hover:z-10 hover:scale-105 transition-transform duration-150",
                          toneCls[m.tone],
                          s.status === "awaiting_approval" && "ring-2 ring-attention-strong/60",
                        )}
                        style={{ left: pct(s.offset_hours), top: 5 + lvl * ROW_H }}
                      >
                        <Icon aria-hidden className={cn("size-3.5", m.spin && "animate-spin")} />
                        <span>{Number.isInteger(s.offset_hours) ? s.offset_hours : s.offset_hours.toFixed(1)}h</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
            {/* plan version markers + now marker span all lanes */}
            <div aria-hidden className="pointer-events-none absolute inset-y-0 right-0 left-36">
              {versionMarks.map((m) => (
                <div key={m.key} className="absolute inset-y-0 border-l-2 border-dashed border-primary/50" style={{ left: pct(m.h) }}>
                  <span className="absolute -top-5 left-1 rounded bg-primary-soft px-1 text-[11px] font-bold whitespace-nowrap text-primary-ink">Plan {m.label}</span>
                </div>
              ))}
              {nowH != null && (
                <div className="absolute inset-y-0 border-l-2 border-ink" style={{ left: pct(nowH) }}>
                  <span className="absolute -bottom-6 -translate-x-1/2 rounded bg-ink px-1.5 py-0.5 text-[11px] font-bold whitespace-nowrap text-white">
                    {nowH > 96 ? "Window ended" : `Now · ${fmtTPlus(nowH)}`}
                  </span>
                </div>
              )}
            </div>
          </div>
          <div className="h-7" />
        </div>
      </div>
      {/* legend */}
      <ul aria-label="Timeline legend" className="mt-1 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted">
        {LEGEND.map((k) => {
          const m = statusMeta("step", k);
          const Icon = m.icon;
          return (
            <li key={k} className="flex items-center gap-1">
              <span className={cn("inline-flex size-5 items-center justify-center rounded-full border", toneCls[m.tone])}>
                <Icon aria-hidden className="size-3" />
              </span>
              {m.label}
            </li>
          );
        })}
        <li className="flex items-center gap-1">
          <span aria-hidden className="inline-block h-4 border-l-2 border-dashed border-primary/60" /> Plan version change
        </li>
        <li className="flex items-center gap-1">
          <span aria-hidden className="inline-block h-4 border-l-2 border-ink" /> Current (simulated) time
        </li>
      </ul>
    </div>
  );
}
