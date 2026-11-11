// Patients (Agent C): every episode assigned to you, filterable by status.
import { Plus } from "lucide-react";
import { useSearchParams } from "react-router";
import { useMe } from "../lib/auth";
import { EmptyState, ErrorState, PageHeader, Skeleton, cn } from "../ui";
import { EpisodeTable } from "./EpisodeList";
import { useEpisodes } from "./hooks";
import { LinkButton } from "./LinkButton";

const FILTERS = [
  { v: "", label: "All" },
  { v: "draft", label: "Draft" },
  { v: "awaiting_review", label: "Awaiting review" },
  { v: "active", label: "Monitoring" },
  { v: "awaiting_closure_review", label: "Awaiting closure review" },
  { v: "closed", label: "Closed" },
];

export default function PatientsPage() {
  const { data: me } = useMe();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const q = useEpisodes(status || undefined);

  return (
    <div>
      <PageHeader
        title="Patients"
        description="Discharge episodes you are assigned to. The 96-hour window starts at the actual discharge time."
        actions={
          me?.user.role === "nurse" && (
            <LinkButton to="/hospital/intake" icon={<Plus aria-hidden className="size-4" />}>
              New discharge
            </LinkButton>
          )
        }
      />
      <div role="group" aria-label="Filter by status" className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.v}
            type="button"
            aria-pressed={status === f.v}
            onClick={() => setParams(f.v ? { status: f.v } : {})}
            className={cn(
              "min-h-9 rounded-md border px-3 text-sm font-bold cursor-pointer",
              status === f.v ? "border-primary bg-primary-soft text-primary-ink" : "border-line bg-surface text-muted hover:text-ink",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>
      {q.isLoading && <Skeleton className="h-40" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && (
        <EmptyState title="No episodes match this filter">
          {me?.user.role === "nurse" ? "Start a new discharge to create a draft episode." : "Episodes appear here once a nurse assigns you to them."}
        </EmptyState>
      )}
      {q.data && q.data.length > 0 && <EpisodeTable rows={q.data} caption="Discharge episodes" />}
    </div>
  );
}
