// Episode table/list (Agent C), used by Patients and Overview.
import { AlertOctagon } from "lucide-react";
import { Link } from "react-router";
import { fmtDateTime, fmtTPlus } from "../lib/format";
import type { EpisodeRow } from "../lib/types";
import { RiskBadge, StatusBadge } from "../ui";
import { sexLabel } from "./util";

export function EpisodeTable({ rows, caption }: { rows: EpisodeRow[]; caption: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full min-w-[920px] text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-sunken text-xs uppercase tracking-wide text-muted">
          <tr>
            <th scope="col" className="px-3 py-2 font-bold">Patient</th>
            <th scope="col" className="px-3 py-2 font-bold">Status</th>
            <th scope="col" className="px-3 py-2 font-bold">Since discharge</th>
            <th scope="col" className="px-3 py-2 font-bold">Priority (demo)</th>
            <th scope="col" className="px-3 py-2 font-bold">Team</th>
            <th scope="col" className="px-3 py-2 font-bold">Needs attention</th>
            <th scope="col" className="px-3 py-2 font-bold">Next step</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r) => (
            <tr key={r.id} className="align-top hover:bg-sunken/50">
              <th scope="row" className="px-3 py-2.5 font-normal">
                <Link to={`/hospital/patients/${r.id}`} className="font-bold text-primary-ink underline-offset-2 hover:underline">
                  {r.patient.full_name}
                </Link>
                <span className="block text-muted">
                  {[r.patient.age != null ? `${r.patient.age} y` : null, sexLabel(r.patient.sex)].filter(Boolean).join(" · ")}
                </span>
              </th>
              <td className="px-3 py-2.5">
                <StatusBadge domain="episode" value={r.status} />
              </td>
              <td className="px-3 py-2.5 whitespace-nowrap">
                {r.hours_since_discharge >= 0 ? fmtTPlus(Math.min(r.hours_since_discharge, 999)) : "Not yet discharged"}
                <span className="block text-muted">{fmtDateTime(r.discharge_at)}</span>
              </td>
              <td className="px-3 py-2.5">
                <RiskBadge category={r.risk_category} />
                {r.risk_reasons.length > 0 && <span className="mt-1 block max-w-[24ch] text-xs text-muted">{r.risk_reasons.join("; ")}</span>}
              </td>
              <td className="px-3 py-2.5">
                <span className="block">{r.team.doctor?.display_name ?? <em className="text-attention-ink not-italic font-bold">No doctor</em>}</span>
                <span className="block text-muted">{r.team.nurse?.display_name ?? "No nurse"}</span>
              </td>
              <td className="px-3 py-2.5">
                <AttentionCounts r={r} />
              </td>
              <td className="px-3 py-2.5">
                {r.next_step ? (
                  <>
                    {r.next_step.title}
                    <span className="block text-muted">{fmtDateTime(r.next_step.due_at)}</span>
                  </>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AttentionCounts({ r }: { r: EpisodeRow }) {
  const c = r.counts;
  const items: { n: number; label: string; urgent?: boolean }[] = [
    { n: c.urgent_reviews, label: "urgent", urgent: true },
    { n: c.pending_approvals, label: "awaiting approval" },
    { n: c.overdue_checkins, label: "overdue check-in" },
    { n: c.failed_actions, label: "failed action" },
    { n: Math.max(0, c.open_reviews - c.urgent_reviews), label: "open review" },
  ].filter((x) => x.n > 0);
  if (items.length === 0) return <span className="text-muted">Nothing outstanding</span>;
  return (
    <ul className="space-y-0.5">
      {items.map((x) => (
        <li key={x.label} className={x.urgent ? "flex items-center gap-1 font-bold text-urgent-ink" : ""}>
          {x.urgent && <AlertOctagon aria-hidden className="size-3.5" />}
          {x.n} {x.label}
        </li>
      ))}
    </ul>
  );
}

export function EpisodeMiniList({ rows, empty }: { rows: EpisodeRow[]; empty: string }) {
  if (rows.length === 0) return <p className="px-4 py-3 text-sm text-muted">{empty}</p>;
  return (
    <ul className="divide-y divide-line">
      {rows.map((r) => (
        <li key={r.id} className="flex flex-wrap items-start justify-between gap-2 px-4 py-2.5">
          <div className="min-w-0">
            <Link to={`/hospital/patients/${r.id}`} className="font-bold text-primary-ink hover:underline">
              {r.patient.full_name}
            </Link>
            <span className="ml-2 text-sm text-muted">{fmtTPlus(r.hours_since_discharge)}</span>
            <div className="mt-0.5 text-sm">
              <AttentionCounts r={r} />
            </div>
          </div>
          <StatusBadge domain="episode" value={r.status} />
        </li>
      ))}
    </ul>
  );
}
