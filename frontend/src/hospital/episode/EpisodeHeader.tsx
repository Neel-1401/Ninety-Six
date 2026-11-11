// Episode header (Agent C): who, status, team, time since discharge, demo risk with reasons,
// urgent outstanding items, and episode-level actions.
import { AlertOctagon } from "lucide-react";
import { useSimClock } from "../../lib/clock";
import { fmtDateTime, fmtTPlus, maskPhone } from "../../lib/format";
import type { EpisodeDetail } from "../../lib/types";
import { Banner, RiskBadge, StatusBadge } from "../../ui";
import { useEpisodeReviews } from "../hooks";
import { sexLabel } from "../util";
import { EpisodeActions } from "./EpisodeActions";

export function EpisodeHeader({ ep }: { ep: EpisodeDetail }) {
  const { data: clock } = useSimClock();
  const reviews = useEpisodeReviews(ep.id);
  const hours = clock ? (new Date(clock.now).getTime() - new Date(ep.discharge_at).getTime()) / 3_600_000 : ep.hours_since_discharge;
  const urgentOpen = (reviews.data ?? []).filter((r) => r.priority === "urgent" && r.status !== "resolved");
  const p = ep.patient;

  return (
    <section aria-labelledby="ep-title" className="rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-start justify-between gap-4 px-4 pt-4 pb-3">
        <div className="min-w-0">
          <h1 id="ep-title" className="text-2xl font-bold">
            {p.full_name}
          </h1>
          <p className="text-muted">
            {[p.age != null ? `${p.age} y` : null, sexLabel(p.sex), p.hospital_number ? `Hospital no. ${p.hospital_number}` : null, `Language: ${p.preferred_language}`]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <StatusBadge domain="episode" value={ep.status} />
            <RiskBadge category={ep.risk_category} />
            {ep.current_plan_version != null && <span className="text-sm text-muted">Plan v{ep.current_plan_version}</span>}
          </div>
        </div>
        <EpisodeActions ep={ep} />
      </div>

      <dl className="grid gap-x-6 gap-y-3 border-t border-line px-4 py-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <dt className="text-muted">Discharged</dt>
          <dd className="font-bold">
            {fmtDateTime(ep.discharge_at)}
            <span className="block font-normal text-muted">
              {hours >= 0 ? `${fmtTPlus(hours)} since discharge${hours > 96 ? " — 96-hour window ended" : ""}` : "Discharge time is in the future"}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted">Care team</dt>
          <dd>
            <span className="block font-bold">{ep.team.doctor?.display_name ?? "No doctor assigned"}</span>
            <span className="block">{ep.team.nurse?.display_name ?? "No nurse assigned"}</span>
          </dd>
        </div>
        <div>
          <dt className="text-muted">Contact</dt>
          <dd>
            <span className="block">Patient {maskPhone(p.phone_e164)}</span>
            <span className="block text-muted">
              {p.caregiver_name
                ? `${p.caregiver_name}${p.caregiver_relation ? ` (${p.caregiver_relation})` : ""} · ${p.caregiver_authorised ? "authorised contact" : "NOT authorised for updates"}`
                : "No caregiver recorded"}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted">Why this priority (demo prioritisation, not a validated prediction)</dt>
          <dd>
            {ep.risk_reasons.length ? (
              <ul className="list-disc pl-4">
                {ep.risk_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : (
              <span className="text-muted">No reasons recorded</span>
            )}
          </dd>
        </div>
      </dl>

      {urgentOpen.length > 0 && (
        <div className="border-t border-line px-4 py-3">
          <Banner tone="urgent" live title={`${urgentOpen.length} urgent item${urgentOpen.length === 1 ? "" : "s"} outstanding`}>
            <ul className="mt-1 space-y-0.5">
              {urgentOpen.map((r) => (
                <li key={r.id} className="flex items-start gap-1.5">
                  <AlertOctagon aria-hidden className="mt-0.5 size-4 shrink-0" />
                  <span>
                    {r.title} — {r.status === "open" ? "not yet acknowledged" : `owned by ${r.owner?.display_name ?? "someone"}`}
                  </span>
                </li>
              ))}
            </ul>
          </Banner>
        </div>
      )}
    </section>
  );
}
