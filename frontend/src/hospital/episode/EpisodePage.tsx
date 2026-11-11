// Patient workspace (Agent C). Draft/awaiting-review episodes show extraction review + plan
// review + (doctor) activation. Active and ended episodes show the 96-hour timeline, current
// steps, proposed changes, pending approvals, review items and supporting tabs.
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router";
import { isApiError } from "../../lib/api";
import { Banner, ErrorState, Skeleton } from "../../ui";
import { useEpisode } from "../hooks";
import { EpisodeHeader } from "./EpisodeHeader";
import { MonitorView } from "./MonitorView";
import { PrepareView } from "./PrepareView";

export default function EpisodePage() {
  const { id: raw } = useParams();
  const id = Number(raw);
  const q = useEpisode(Number.isFinite(id) ? id : 0);

  return (
    <div>
      <Link to="/hospital/patients" className="mb-3 inline-flex min-h-8 items-center gap-1 text-sm font-bold text-primary-ink hover:underline">
        <ArrowLeft aria-hidden className="size-4" /> All patients
      </Link>
      {q.isLoading && <Skeleton className="h-40" />}
      {q.isError &&
        (isApiError(q.error) && q.error.status === 404 ? (
          <Banner tone="warning" title="Episode not found">
            It doesn't exist, or you are not assigned to it. Access to episodes follows care-team assignment.
          </Banner>
        ) : (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ))}
      {q.data && (
        <div className="space-y-5">
          <EpisodeHeader ep={q.data} />
          {q.data.status === "draft" || q.data.status === "awaiting_review" ? <PrepareView ep={q.data} /> : <MonitorView ep={q.data} />}
        </div>
      )}
    </div>
  );
}
