// Approval queue (Agent C). The order is SNAPSHOTTED while the page is open so cards never move
// under the doctor's cursor: new proposals wait behind a "show N new" button and decided cards
// stay in place showing their new state until the doctor refreshes the list.
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMe } from "../../lib/auth";
import type { ActionOut } from "../../lib/types";
import { Banner, Button, EmptyState, ErrorState, PageHeader, Skeleton } from "../../ui";
import { useApprovals } from "../hooks";
import { ActionCard } from "./ActionCard";

const RANK: Record<string, number> = { urgent: 0, high: 1, normal: 2 };
const sortActions = (xs: ActionOut[]) =>
  [...xs].sort((a, b) => (RANK[a.priority] ?? 3) - (RANK[b.priority] ?? 3) || a.due_at.localeCompare(b.due_at) || a.id - b.id);

export default function ApprovalsPage() {
  const { data: me } = useMe();
  const isDoctor = me?.user.role === "doctor";
  // "all" so that cards already on screen keep showing their CURRENT state after a decision.
  const q = useApprovals("all");
  const [order, setOrder] = useState<number[] | null>(null);
  const initialised = useRef(false);

  const byId = useMemo(() => new Map((q.data ?? []).map((a) => [a.id, a])), [q.data]);
  const pendingNow = useMemo(() => sortActions((q.data ?? []).filter((a) => a.approval_state === "pending")), [q.data]);

  useEffect(() => {
    if (!initialised.current && q.data) {
      initialised.current = true;
      setOrder(pendingNow.map((a) => a.id));
    }
  }, [q.data, pendingNow]);

  const shown = (order ?? []).map((id) => byId.get(id)).filter((a): a is ActionOut => !!a);
  const shownIds = new Set(order ?? []);
  const newcomers = pendingNow.filter((a) => !shownIds.has(a.id));
  const newUrgent = newcomers.filter((a) => a.priority === "urgent").length;
  const decidedOnScreen = shown.filter((a) => a.approval_state !== "pending").length;

  const refresh = () => setOrder(pendingNow.map((a) => a.id));

  return (
    <div>
      <PageHeader
        title="Approvals"
        description={
          isDoctor
            ? "Every external action waits here until you approve it. Urgent items are listed first. Approval binds to the exact text you see."
            : "Read-only for nurses: only the assigned doctor can approve, edit or reject. You can follow what is waiting and route concerns."
        }
        actions={
          <Button variant="secondary" icon={<RefreshCw aria-hidden className="size-4" />} onClick={refresh} disabled={!q.data}>
            Refresh list
          </Button>
        }
      />

      {newcomers.length > 0 && (
        <Banner
          tone={newUrgent > 0 ? "urgent" : "info"}
          live
          className="mb-4"
          title={`${newcomers.length} new item${newcomers.length === 1 ? "" : "s"} waiting${newUrgent ? ` (${newUrgent} urgent)` : ""}`}
          actions={
            <Button size="sm" variant={newUrgent ? "danger" : "primary"} onClick={refresh}>
              Show {newcomers.length} new
            </Button>
          }
        >
          New proposals are held here so the list doesn't move while you review.
        </Banner>
      )}
      {decidedOnScreen > 0 && (
        <p role="status" className="mb-3 text-sm text-muted">
          {decidedOnScreen} decided item{decidedOnScreen === 1 ? "" : "s"} still shown in place. Use “Refresh list” to clear them.
        </p>
      )}

      {q.isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      )}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && shown.length === 0 && newcomers.length === 0 && (
        <EmptyState title="Nothing waiting for approval">
          When the agent proposes an external action — a message, a booking or a care-team alert — it appears here before anything is sent.
        </EmptyState>
      )}
      <ol className="space-y-4" aria-label="Actions awaiting decision">
        {shown.map((a) => (
          <li key={a.id}>
            <ActionCard action={a} />
          </li>
        ))}
      </ol>
    </div>
  );
}
