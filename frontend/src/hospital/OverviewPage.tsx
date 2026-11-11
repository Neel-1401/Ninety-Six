// Overview (Agent C): operational counts and work queues, ordered by role. No decorative charts.
import { ArrowRight, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useMe } from "../lib/auth";
import { fmtDateTime } from "../lib/format";
import type { ActionOut, OverviewOut } from "../lib/types";
import { Banner, Card, CardHeader, ErrorState, PageHeader, PriorityBadge, Skeleton, StatusBadge, cn } from "../ui";
import { EpisodeMiniList } from "./EpisodeList";
import { LinkButton } from "./LinkButton";
import { useOverview } from "./hooks";
import { ReviewItemCard } from "./ReviewItemCard";
import { actionLabel } from "./util";

export default function OverviewPage() {
  const { data: me } = useMe();
  const q = useOverview();
  const isDoctor = me?.user.role === "doctor";

  return (
    <div>
      <PageHeader
        title={isDoctor ? "Your reviews and approvals" : "Discharges and follow-up"}
        description={
          isDoctor
            ? "Urgent concerns first, then actions waiting for your decision and plans waiting for review."
            : "Urgent concerns first, then discharges to prepare and patients who need follow-up."
        }
        actions={
          !isDoctor && (
            <LinkButton to="/hospital/intake" icon={<Plus aria-hidden className="size-4" />}>
              New discharge
            </LinkButton>
          )
        }
      />
      {q.isLoading && <Skeleton className="h-24" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && <OverviewBody data={q.data} isDoctor={isDoctor} />}
    </div>
  );
}

function OverviewBody({ data, isDoctor }: { data: OverviewOut; isDoctor: boolean }) {
  const c = data.counts;
  const urgent = data.urgent_reviews;
  return (
    <div className="space-y-5">
      {urgent.length > 0 && (
        <Banner tone="urgent" live title={`${urgent.length} urgent concern${urgent.length === 1 ? " needs" : "s need"} a clinician now`}>
          Review below. External replies still require doctor approval before anything is sent.
        </Banner>
      )}

      <section aria-label="Operational counts">
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          <Count n={c.urgent_reviews} label="Urgent concerns" tone={c.urgent_reviews ? "urgent" : undefined} />
          <Count n={c.pending_approvals} label="Pending approvals" href="/hospital/approvals" tone={c.pending_approvals ? "attention" : undefined} />
          <Count n={c.awaiting_review} label="Plans awaiting doctor review" tone={c.awaiting_review && isDoctor ? "attention" : undefined} />
          <Count n={c.overdue_checkins} label="Overdue check-ins" tone={c.overdue_checkins ? "attention" : undefined} />
          <Count n={c.failed_actions + c.unknown_outcomes} label="Failed or unknown actions" tone={c.failed_actions + c.unknown_outcomes ? "urgent" : undefined} />
          <Count n={isDoctor ? c.active : c.drafts} label={isDoctor ? "Episodes being monitored" : "Drafts to prepare"} href="/hospital/patients" />
        </ul>
      </section>

      {urgent.length > 0 && (
        <Section title="Urgent concerns" description="Created the moment the agent detects a red flag. Acknowledge to take ownership.">
          <div className="space-y-3 p-4">
            {urgent.map((r) => (
              <ReviewItemCard key={r.id} item={r} />
            ))}
          </div>
        </Section>
      )}

      <div className="grid gap-5 xl:grid-cols-2">
        {isDoctor ? (
          <>
            <Section title="Waiting for your approval" href="/hospital/approvals" linkLabel="Open approval queue">
              <ActionMiniList actions={data.pending_approvals} empty="No actions waiting for approval." />
            </Section>
            <Section title="Plans awaiting your review" description="Check the extracted information, confirm it and activate the plan.">
              <EpisodeMiniList rows={data.awaiting_review} empty="No plans waiting for review." />
            </Section>
            <Section title="Failed or uncertain actions">
              <ActionMiniList actions={data.failed_actions} empty="No failed or uncertain actions." />
            </Section>
            <Section title="Patients needing follow-up">
              <EpisodeMiniList rows={data.needs_follow_up} empty="No patients need follow-up right now." />
            </Section>
          </>
        ) : (
          <>
            <Section title="Discharges to prepare" description="Drafts not yet submitted for doctor review." href="/hospital/intake" linkLabel="New discharge">
              <EpisodeMiniList rows={data.drafts} empty="No drafts. Start a new discharge when a patient is ready to go home." />
            </Section>
            <Section title="Patients needing follow-up" description="Overdue check-ins, open concerns and failed actions.">
              <EpisodeMiniList rows={data.needs_follow_up} empty="No patients need follow-up right now." />
            </Section>
            <Section title="Unassigned review items" description="No one owns these yet — acknowledge or route them.">
              {data.unassigned_reviews.length === 0 ? (
                <p className="px-4 py-3 text-sm text-muted">Everything has an owner.</p>
              ) : (
                <div className="space-y-3 p-4">
                  {data.unassigned_reviews.map((r) => (
                    <ReviewItemCard key={r.id} item={r} />
                  ))}
                </div>
              )}
            </Section>
            <Section title="Failed or uncertain actions">
              <ActionMiniList actions={data.failed_actions} empty="No failed or uncertain actions." />
            </Section>
            <Section title="Waiting for the doctor" description="Submitted plans and actions pending doctor approval (read-only for nurses).">
              <EpisodeMiniList rows={data.awaiting_review} empty="No plans waiting for doctor review." />
            </Section>
          </>
        )}
      </div>
    </div>
  );
}

function Count({ n, label, tone, href }: { n: number; label: string; tone?: "urgent" | "attention"; href?: string }) {
  const body = (
    <>
      <span className={cn("block text-2xl font-bold tabular-nums", tone === "urgent" ? "text-urgent-ink" : tone === "attention" ? "text-attention-ink" : "text-ink")}>
        {n}
      </span>
      <span className="text-sm text-muted">{label}</span>
    </>
  );
  const cls = cn(
    "block h-full rounded-lg border bg-surface px-3 py-2.5",
    tone === "urgent" ? "border-urgent/40" : tone === "attention" ? "border-attention-line" : "border-line",
    href && "hover:border-primary",
  );
  return <li>{href ? <Link to={href} className={cls}>{body}</Link> : <div className={cls}>{body}</div>}</li>;
}

function Section({
  title,
  description,
  href,
  linkLabel,
  children,
}: {
  title: string;
  description?: string;
  href?: string;
  linkLabel?: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader
        title={title}
        description={description}
        actions={
          href && (
            <Link to={href} className="inline-flex items-center gap-1 text-sm font-bold text-primary-ink hover:underline">
              {linkLabel ?? "Open"} <ArrowRight aria-hidden className="size-4" />
            </Link>
          )
        }
      />
      {children}
    </Card>
  );
}

function ActionMiniList({ actions, empty }: { actions: ActionOut[]; empty: string }) {
  if (actions.length === 0) return <p className="px-4 py-3 text-sm text-muted">{empty}</p>;
  return (
    <ul className="divide-y divide-line">
      {actions.map((a) => (
        <li key={a.id} className="flex flex-wrap items-start justify-between gap-2 px-4 py-2.5">
          <div className="min-w-0">
            <p className="font-bold">
              {actionLabel(a.action_type)} — {a.step_title}
            </p>
            <p className="text-sm text-muted">
              <Link to={`/hospital/patients/${a.episode_id}`} className="font-bold text-primary-ink hover:underline">
                {a.patient.full_name}
              </Link>{" "}
              · due {fmtDateTime(a.due_at)}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <PriorityBadge priority={a.priority} />
            {a.execution_state === "failed" || a.execution_state === "unknown" ? (
              <StatusBadge domain="execution" value={a.execution_state} />
            ) : (
              <StatusBadge domain="approval" value={a.approval_state} />
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
