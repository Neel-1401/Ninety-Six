// Small patient-side building blocks (Agent D). Built on the shared ui kit.
import {
  CheckCircle2,
  Circle,
  Cpu,
  Hourglass,
  Inbox,
  Loader2,
  MessageSquareText,
  Phone,
  PhoneCall,
  TriangleAlert,
  UserCheck,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { isApiError } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import type { CheckInReceipt } from "../lib/types";
import { Banner, Button, cn, Skeleton } from "../ui";

export function PageTitle({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-[26px] font-bold leading-tight text-ink">{children}</h1>
      {sub && <p className="mt-1 text-muted">{sub}</p>}
    </div>
  );
}

export function Section({
  title,
  children,
  className,
  action,
}: {
  title: ReactNode;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <section className={cn("mt-7", className)}>
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h2 className="text-lg font-bold text-ink">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("rounded-lg border border-line bg-surface p-4", className)}>{children}</div>;
}

export function LoadingBlock({ lines = 3 }: { lines?: number }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-3">
      <Skeleton className="h-8 w-2/3" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-20 w-full" />
      ))}
    </div>
  );
}

/** Error with retry; a 401 is handled by the layout (back to sign-in), 404 gets a plain message. */
export function LoadError({ error, onRetry, what = "this" }: { error: unknown; onRetry?: () => void; what?: string }) {
  const notFound = isApiError(error) && error.status === 404;
  return (
    <Banner
      tone="warning"
      title={notFound ? `We couldn't find ${what}` : `We couldn't load ${what}`}
      actions={
        onRetry && !notFound ? (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      {notFound
        ? "It may have been removed, or it isn't part of your care plan."
        : "Check your connection and try again. Nothing you entered has been lost."}
    </Banner>
  );
}

export function EmergencyLine({ number, className }: { number: string; className?: string }) {
  return (
    <p className={cn("flex items-start gap-2 text-[15px] text-ink", className)}>
      <TriangleAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-urgent" />
      <span>
        This service is not monitored around the clock. In an emergency call{" "}
        <a href={`tel:${number}`} className="font-bold text-urgent-ink underline underline-offset-2">
          {number}
        </a>
        .
      </span>
    </p>
  );
}

export function ContactBlock({ hospitalPhone, emergency }: { hospitalPhone: string; emergency: string }) {
  const tel = hospitalPhone.replace(/[^\d+]/g, "");
  return (
    <Panel className="flex flex-col gap-4">
      <a
        href={`tel:${emergency}`}
        className="flex min-h-14 items-center gap-3 rounded-md bg-urgent px-4 text-white hover:bg-urgent-hover"
      >
        <PhoneCall aria-hidden className="size-6 shrink-0" />
        <span>
          <span className="block text-lg font-bold leading-tight">Emergency: call {emergency}</span>
          <span className="block text-sm">Chest pain, severe breathlessness, fainting or confusion</span>
        </span>
      </a>
      <div className="flex items-start gap-3">
        <Phone aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />
        <div>
          <p className="font-bold">Your hospital's care team</p>
          {tel ? (
            <a href={`tel:${tel}`} className="text-primary-ink underline underline-offset-2">
              {hospitalPhone}
            </a>
          ) : (
            <p>{hospitalPhone}</p>
          )}
          <p className="mt-1 text-sm text-muted">For questions about your instructions or follow-up during working hours.</p>
        </div>
      </div>
      <EmergencyLine number={emergency} />
    </Panel>
  );
}

// ------------------------------------------------------------------ Receipt
type StageState = "done" | "current" | "pending" | "problem";

function Stage({
  icon: Icon,
  state,
  title,
  detail,
  spin,
}: {
  icon: LucideIcon;
  state: StageState;
  title: ReactNode;
  detail?: ReactNode;
  spin?: boolean;
}) {
  const StateIcon = state === "done" ? CheckCircle2 : state === "problem" ? TriangleAlert : state === "current" ? Icon : Circle;
  const stateLabel =
    state === "done" ? "Done" : state === "problem" ? "Problem" : state === "current" ? "In progress" : "Not yet";
  return (
    <li className="flex gap-3">
      <span
        className={cn(
          "mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full border",
          state === "done" && "border-primary bg-primary-soft text-primary-ink",
          state === "current" && "border-attention-strong bg-attention text-attention-ink",
          state === "problem" && "border-attention-strong bg-attention text-attention-ink",
          state === "pending" && "border-line-strong bg-surface text-faint",
        )}
      >
        <StateIcon aria-hidden className={cn("size-4", spin && state === "current" && "animate-spin")} />
      </span>
      <div className="min-w-0 pb-1">
        <p className={cn("font-bold", state === "pending" ? "text-muted" : "text-ink")}>
          {title} <span className="sr-only">({stateLabel})</span>
        </p>
        {detail && <p className="text-[15px] text-muted">{detail}</p>}
      </div>
    </li>
  );
}

/**
 * The five receipt stages are deliberately separate. "Agent processing complete" is automatic
 * and is never described as a review; "Reviewed by staff" only appears when a person did it.
 */
export function ReceiptStages({ receipt, className }: { receipt: CheckInReceipt; className?: string }) {
  const proc = receipt.agent_processing;
  const staffDone = receipt.staff_review === "reviewed";
  return (
    <ol aria-label="What has happened to your answers" className={cn("flex flex-col gap-3", className)}>
      <Stage
        icon={Inbox}
        state={receipt.received ? "done" : "current"}
        title="Received"
        detail={
          receipt.received
            ? `Saved by the hospital system${receipt.received_at ? ` at ${fmtDateTime(receipt.received_at)}` : ""}.`
            : "Sending your answers…"
        }
      />
      <Stage
        icon={proc === "processing" || proc === "not_started" ? Loader2 : Cpu}
        spin
        state={proc === "complete" ? "done" : proc === "failed" ? "problem" : receipt.received ? "current" : "pending"}
        title={proc === "complete" ? "Agent processing complete" : proc === "failed" ? "Automatic processing hit a problem" : "Agent processing"}
        detail={
          proc === "complete"
            ? staffDone
              ? "Your answers were processed automatically."
              : "Your answers were processed automatically — not yet seen by a person."
            : proc === "failed"
              ? "Your answers are saved and your care team can still see them."
              : "The system is checking your answers automatically. This is not a review by a person."
        }
      />
      {receipt.staff_review === "not_requested" && !staffDone ? (
        <Stage
          icon={UserCheck}
          state="pending"
          title="Staff review not requested"
          detail="Nothing in your answers asked for a nurse or doctor to look at them. Your care team can still see them."
        />
      ) : (
        <Stage
          icon={staffDone ? UserCheck : Hourglass}
          state={staffDone ? "done" : "current"}
          title={staffDone ? "Reviewed by staff" : "Awaiting staff review"}
          detail={
            staffDone
              ? `A member of your care team reviewed your answers${receipt.staff_reviewed_at ? ` at ${fmtDateTime(receipt.staff_reviewed_at)}` : ""}.`
              : "A member of your care team has been asked to look at your answers. They have not reviewed them yet."
          }
        />
      )}
      <Stage
        icon={MessageSquareText}
        state={receipt.response_available ? "done" : "pending"}
        title={receipt.response_available ? "Response available" : "No response yet"}
        detail={
          receipt.response_available ? (
            <Link to="/p/messages" className="font-bold text-primary-ink underline underline-offset-2">
              Read the response in Messages
            </Link>
          ) : (
            "If your care team sends you a message, it will appear in Messages."
          )
        }
      />
    </ol>
  );
}
