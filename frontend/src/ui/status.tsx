// Single source of truth for status wording (lead-owned). Status is ALWAYS text + icon, with
// colour as a third cue. Labels are deliberately honest: "accepted by provider" is not
// "delivered", "simulated" is not "sent", a calendar event is not a confirmed appointment,
// agent processing is not staff review.
import {
  AlertOctagon,
  AlertTriangle,
  Ban,
  CalendarCheck,
  CalendarPlus,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  Clock,
  FlaskConical,
  HelpCircle,
  Hourglass,
  Loader2,
  Lock,
  type LucideIcon,
  Mail,
  PauseCircle,
  PlayCircle,
  Radio,
  Send,
  ShieldCheck,
  Sparkles,
  Cog,
  SkipForward,
  Undo2,
  UserCheck,
  XCircle,
} from "lucide-react";
import { cn } from "./primitives";

export type Tone = "neutral" | "primary" | "attention" | "urgent" | "done";

const toneCls: Record<Tone, string> = {
  neutral: "bg-neutral-soft text-neutral-ink border-line",
  primary: "bg-primary-soft text-primary-ink border-primary/25",
  attention: "bg-attention text-attention-ink border-attention-line",
  urgent: "bg-urgent-soft text-urgent-ink border-urgent/30",
  done: "bg-surface text-primary-ink border-primary/40",
};

export interface StatusMeta {
  label: string;
  tone: Tone;
  icon: LucideIcon;
  hint?: string;
  spin?: boolean;
}

type Domain =
  | "step"
  | "approval"
  | "execution"
  | "task"
  | "episode"
  | "message"
  | "followup"
  | "review"
  | "checkin"
  | "processing"
  | "staffReview"
  | "fact"
  | "routine";

const M = (label: string, tone: Tone, icon: LucideIcon, hint?: string, spin?: boolean): StatusMeta => ({
  label,
  tone,
  icon,
  hint,
  spin,
});

const MAP: Record<Domain, Record<string, StatusMeta>> = {
  step: {
    planned: M("Planned", "neutral", CircleDashed),
    blocked: M("Blocked", "attention", PauseCircle, "Waiting on an earlier step or review"),
    awaiting_approval: M("Awaiting approval", "attention", Lock, "Needs a doctor's decision before it can run"),
    ready: M("Ready", "primary", PlayCircle),
    running: M("Running", "primary", Loader2, undefined, true),
    executed: M("Done", "done", CheckCircle2),
    failed: M("Failed", "urgent", XCircle),
    unknown: M("Outcome unknown", "attention", HelpCircle, "The provider may or may not have acted; needs reconciliation"),
    skipped: M("Skipped", "neutral", SkipForward),
    superseded: M("Superseded", "neutral", Undo2, "Replaced by a newer plan version"),
    cancelled: M("Cancelled", "neutral", Ban),
  },
  approval: {
    not_required: M("No approval needed", "neutral", CircleDot, "Internal step with no external effect"),
    pending: M("Awaiting doctor approval", "attention", Lock),
    approved: M("Approved", "done", ShieldCheck),
    routine: M("Covered by routine authorisation", "primary", ShieldCheck),
    rejected: M("Rejected", "neutral", XCircle),
    invalidated: M("Approval invalidated", "attention", Undo2, "The action or its context changed after approval"),
  },
  execution: {
    not_started: M("Not run yet", "neutral", CircleDashed),
    running: M("Running", "primary", Loader2, undefined, true),
    succeeded: M("Succeeded", "done", CheckCircle2),
    failed: M("Failed", "urgent", XCircle),
    unknown: M("Outcome unknown", "attention", HelpCircle),
    cancelled: M("Cancelled", "neutral", Ban),
  },
  task: {
    scheduled: M("Scheduled", "neutral", Clock),
    awaiting_approval: M("Awaiting approval", "attention", Lock),
    ready: M("Ready", "primary", PlayCircle),
    running: M("Running", "primary", Loader2, undefined, true),
    succeeded: M("Succeeded", "done", CheckCircle2),
    failed: M("Failed", "urgent", XCircle),
    unknown: M("Outcome unknown", "attention", HelpCircle),
    cancelled: M("Cancelled", "neutral", Ban),
    superseded: M("Superseded", "neutral", Undo2),
    skipped: M("Skipped", "neutral", SkipForward),
  },
  episode: {
    draft: M("Draft — being prepared", "neutral", CircleDashed),
    awaiting_review: M("Awaiting doctor review", "attention", Hourglass),
    active: M("Monitoring active", "primary", Radio),
    awaiting_closure_review: M("Window ended — awaiting closure review", "attention", Hourglass, "Not a medical clearance"),
    closed: M("Closed after review", "neutral", CheckCircle2, "Closure review complete; not a medical clearance"),
    cancelled: M("Cancelled", "neutral", Ban),
  },
  message: {
    draft: M("Draft", "neutral", CircleDashed),
    queued: M("Queued", "neutral", Clock),
    provider_accepted: M("Accepted by provider — delivery not confirmed", "primary", Send),
    delivered: M("Delivered (provider confirmed)", "done", CheckCircle2),
    failed: M("Failed to send", "urgent", XCircle),
    unknown: M("Send outcome unknown", "attention", HelpCircle),
    simulated: M("Simulated — not actually sent", "neutral", FlaskConical),
  },
  followup: {
    proposed: M("Proposed — not booked", "neutral", CircleDashed),
    calendar_event_created: M("Calendar event created — not confirmed by hospital", "primary", CalendarPlus),
    confirmed_by_hospital: M("Confirmed by the hospital", "done", CalendarCheck),
    failed: M("Booking failed", "urgent", XCircle),
    cancelled: M("Cancelled", "neutral", Ban),
  },
  review: {
    open: M("Open", "attention", AlertTriangle),
    acknowledged: M("Acknowledged", "primary", UserCheck),
    resolved: M("Resolved", "done", CheckCircle2),
  },
  checkin: {
    upcoming: M("Upcoming", "neutral", Clock),
    due: M("Due now", "attention", Mail),
    submitted: M("Submitted", "done", CheckCircle2),
    missed: M("Missed", "attention", AlertTriangle),
    cancelled: M("Cancelled", "neutral", Ban),
  },
  processing: {
    not_started: M("Not submitted", "neutral", CircleDashed),
    received: M("Received", "primary", CheckCircle2),
    processing: M("Agent processing", "primary", Loader2, undefined, true),
    processed: M("Agent processing complete", "done", CheckCircle2, "Processed automatically — not a staff review"),
    failed: M("Processing failed — answers saved", "attention", AlertTriangle),
  },
  staffReview: {
    not_requested: M("No staff review requested", "neutral", CircleDot),
    awaiting: M("Awaiting staff review", "attention", Hourglass),
    reviewed: M("Reviewed by staff", "done", UserCheck),
  },
  fact: {
    unreviewed: M("Not yet reviewed", "attention", Hourglass),
    confirmed: M("Confirmed by doctor", "done", ShieldCheck),
    rejected: M("Rejected", "neutral", XCircle),
  },
  routine: {
    active: M("Active", "primary", ShieldCheck),
    revoked: M("Revoked", "neutral", Ban),
    expired: M("Expired", "neutral", Clock),
  },
};

export function statusMeta(domain: Domain, value: string | null | undefined): StatusMeta {
  if (!value) return M("—", "neutral", CircleDashed);
  return MAP[domain][value] ?? M(value.replace(/_/g, " "), "neutral", CircleDot);
}

export function Badge({
  tone = "neutral",
  icon: Icon,
  children,
  className,
  title,
  spin,
}: {
  tone?: Tone;
  icon?: LucideIcon;
  children: React.ReactNode;
  className?: string;
  title?: string;
  spin?: boolean;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-0.5 text-[13px] font-bold leading-5",
        toneCls[tone],
        className,
      )}
    >
      {Icon && <Icon aria-hidden className={cn("size-3.5 shrink-0", spin && "animate-spin")} />}
      <span className="min-w-0">{children}</span>
    </span>
  );
}

export function StatusBadge({ domain, value, className }: { domain: Domain; value: string | null | undefined; className?: string }) {
  const m = statusMeta(domain, value);
  return (
    <Badge tone={m.tone} icon={m.icon} spin={m.spin} title={m.hint} className={className}>
      {m.label}
    </Badge>
  );
}

export function PriorityBadge({ priority }: { priority: "urgent" | "high" | "normal" | string }) {
  if (priority === "urgent")
    return (
      <Badge tone="urgent" icon={AlertOctagon} className="uppercase tracking-wide">
        Urgent
      </Badge>
    );
  if (priority === "high")
    return (
      <Badge tone="attention" icon={AlertTriangle}>
        High priority
      </Badge>
    );
  return <Badge tone="neutral">Normal priority</Badge>;
}

export function RiskBadge({ category }: { category: string | null | undefined }) {
  if (!category) return <Badge tone="neutral">Risk not set</Badge>;
  const tone: Tone = category === "high" ? "urgent" : category === "medium" ? "attention" : "neutral";
  const Icon = category === "high" ? AlertOctagon : category === "medium" ? AlertTriangle : CircleDot;
  return (
    <Badge tone={tone} icon={Icon} title="Demo prioritisation from defined inputs — not a validated clinical prediction">
      {category[0].toUpperCase() + category.slice(1)} priority (demo)
    </Badge>
  );
}

export function ModeBadge({ mode }: { mode: "live" | "simulated" | string | null | undefined }) {
  if (mode === "live")
    return (
      <Badge tone="primary" icon={Radio} title="A real external provider is called">
        LIVE
      </Badge>
    );
  return (
    <Badge tone="neutral" icon={FlaskConical} className="border-dashed" title="Recorded locally; no real provider is called">
      SIMULATED
    </Badge>
  );
}

/** Hybrid provenance tag: "AI" (model output that passed validation) vs "Rules" (deterministic). */
export function SourceBadge({ ai, title, className }: { ai: boolean; title?: string; className?: string }) {
  return ai ? (
    <Badge tone="primary" icon={Sparkles} title={title ?? "Written by the language model, validated by rules"} className={cn("!px-1.5 !py-0 text-[11px]", className)}>
      AI
    </Badge>
  ) : (
    <Badge tone="neutral" icon={Cog} title={title ?? "Deterministic rules / fixed template"} className={cn("!px-1.5 !py-0 text-[11px]", className)}>
      Rules
    </Badge>
  );
}
