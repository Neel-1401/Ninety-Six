// My Instructions, Messages and Follow-up (Agent D). Content is exactly what the backend
// returns as doctor-reviewed / patient-visible; nothing is generated or reworded here.
import { CalendarClock, CalendarX2, FileCheck2, MapPin, MessageSquare, Pill, Stethoscope, TriangleAlert, UserRound } from "lucide-react";
import type { ReactNode } from "react";
import { fmtDateTime } from "../lib/format";
import type { InstructionItem, InstructionsOut } from "../lib/types";
import { Badge, Banner, EmptyState, StatusBadge } from "../ui";
import { useEpisode, useInstructions, useMyFollowups, useMyMessages } from "./data";
import { ContactBlock, LoadError, LoadingBlock, PageTitle, Panel, Section } from "./parts";

const NOT_PROVIDED = "Not provided";

function isMissing(text: string | null | undefined) {
  return !text || /^not provided$/i.test(text.trim());
}

function Value({ text }: { text: string | null | undefined }) {
  if (isMissing(text)) return <span className="italic text-muted">{NOT_PROVIDED}</span>;
  return <>{text}</>;
}

function ItemList({ items, empty, icon }: { items: InstructionItem[]; empty: string; icon?: ReactNode }) {
  if (items.length === 0) return <p className="italic text-muted">{empty}</p>;
  return (
    <ul className="flex flex-col gap-2">
      {items.map((it, i) => (
        <li key={i} className="flex items-start gap-2.5 text-[16px]">
          {icon}
          <span className="min-w-0">
            <Value text={it.text} />
          </span>
        </li>
      ))}
    </ul>
  );
}

export function InstructionsPage() {
  const { episodeId, episode } = useEpisode();
  const q = useInstructions(episodeId);
  if (q.isLoading) return <LoadingBlock />;
  if (q.isError || !q.data) return <LoadError error={q.error} onRetry={() => void q.refetch()} what="your instructions" />;
  const d: InstructionsOut = q.data;

  if (!d.reviewed) {
    return (
      <div>
        <PageTitle>My instructions</PageTitle>
        <EmptyState icon={<FileCheck2 aria-hidden className="size-6" />} title="Not ready yet">
          Your care team hasn't finished reviewing your instructions yet. Until then, follow the written discharge
          instructions you were given at the hospital.
        </EmptyState>
        {episode && (
          <div className="mt-6">
            <ContactBlock hospitalPhone={episode.hospital_phone} emergency={episode.emergency_number} />
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      <PageTitle sub="From your discharge summary, as reviewed by your doctor. Don't change how you take your medicines without talking to your care team.">
        My instructions
      </PageTitle>

      <Section title="Why you were in hospital" className="mt-2">
        <Panel>
          <ItemList items={d.diagnosis} empty={NOT_PROVIDED} icon={<Stethoscope aria-hidden className="mt-1 size-4 shrink-0 text-primary" />} />
        </Panel>
      </Section>

      <Section title="Your medicines">
        {d.medicines.length === 0 ? (
          <Panel>
            <p className="italic text-muted">No medicines were listed in your reviewed discharge summary.</p>
          </Panel>
        ) : (
          <ul className="flex flex-col gap-3">
            {d.medicines.map((m, i) => (
              <li key={i} className="rounded-lg border border-line bg-surface p-4">
                <p className="flex items-center gap-2 text-lg font-bold">
                  <Pill aria-hidden className="size-5 shrink-0 text-primary" />
                  {m.name}
                </p>
                <dl className="mt-2 grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5 text-[15px]">
                  <dt className="text-muted">Dose</dt>
                  <dd>
                    <Value text={m.dose} />
                  </dd>
                  <dt className="text-muted">How often</dt>
                  <dd>
                    <Value text={m.frequency} />
                  </dd>
                  <dt className="text-muted">Instructions</dt>
                  <dd>
                    <Value text={m.instructions} />
                  </dd>
                </dl>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Allergies">
        <Panel>
          <ItemList items={d.allergies} empty={NOT_PROVIDED} />
        </Panel>
      </Section>

      <Section title="Warning signs — get help">
        <div className="rounded-lg border border-urgent/30 bg-urgent-soft p-4">
          <p className="mb-3 font-bold text-urgent-ink">
            If you notice any of these, contact your hospital. If it's severe, call {episode?.emergency_number ?? "108"} now.
          </p>
          <ItemList
            items={d.warning_signs}
            empty="Your discharge summary didn't list specific warning signs. If you feel worse, contact your hospital."
            icon={<TriangleAlert aria-hidden className="mt-1 size-4 shrink-0 text-urgent" />}
          />
        </div>
      </Section>

      <Section title="Follow-up">
        <Panel>
          <ItemList items={d.follow_up} empty={NOT_PROVIDED} icon={<CalendarClock aria-hidden className="mt-1 size-4 shrink-0 text-primary" />} />
        </Panel>
      </Section>

      {d.other.length > 0 && (
        <Section title="Other instructions">
          <Panel>
            <ItemList items={d.other} empty="" />
          </Panel>
        </Section>
      )}

      <footer className="mt-8 rounded-lg bg-sunken px-4 py-3 text-sm text-muted">
        <p className="flex items-start gap-2">
          <FileCheck2 aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            {d.source_note ||
              `From your discharge summary, reviewed by ${d.reviewed_by ?? "your doctor"} on ${fmtDateTime(d.reviewed_at)}.`}
            {d.plan_version != null && ` (Care plan version ${d.plan_version}.)`}
          </span>
        </p>
      </footer>
    </div>
  );
}

// ------------------------------------------------------------------ Messages
export function MessagesPage() {
  const { episodeId } = useEpisode();
  const q = useMyMessages(episodeId);
  return (
    <div>
      <PageTitle sub="Messages your care team approved for you. Replies aren't monitored here — use Report a concern on Today.">
        Messages
      </PageTitle>
      {q.isLoading ? (
        <LoadingBlock />
      ) : q.isError ? (
        <LoadError error={q.error} onRetry={() => void q.refetch()} what="your messages" />
      ) : (q.data ?? []).length === 0 ? (
        <EmptyState icon={<MessageSquare aria-hidden className="size-6" />} title="No messages yet">
          When your care team sends you a message, it will appear here.
        </EmptyState>
      ) : (
        <ol className="flex flex-col gap-3">
          {[...(q.data ?? [])]
            .sort((a, b) => (b.sent_at ?? b.created_at).localeCompare(a.sent_at ?? a.created_at))
            .map((m) => (
              <li key={m.id} className="rounded-lg border border-line bg-surface p-4">
                <p className="whitespace-pre-line break-words text-[16px] text-ink">{m.body}</p>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted">
                  <StatusBadge domain="message" value={m.status} />
                  <span>{fmtDateTime(m.sent_at ?? m.created_at)}</span>
                  {m.channel && <span className="uppercase">· {m.channel}</span>}
                </div>
              </li>
            ))}
        </ol>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Follow-up
const followupExplain: Record<string, string> = {
  proposed: "Your care team plans this follow-up. It has not been booked yet.",
  calendar_event_created:
    "A calendar entry was created for this time. The hospital has not confirmed this appointment — please wait for the hospital to confirm before you travel.",
  confirmed_by_hospital: "The hospital has confirmed this appointment.",
  failed: "Booking this follow-up didn't work. It has been flagged to your care team; please contact the hospital if you haven't heard from them.",
  cancelled: "This follow-up was cancelled.",
};

export function FollowUpPage() {
  const { episodeId, episode } = useEpisode();
  const q = useMyFollowups(episodeId);
  return (
    <div>
      <PageTitle sub="Visits and tests your care team arranged after you went home.">Follow-up</PageTitle>
      {q.isLoading ? (
        <LoadingBlock />
      ) : q.isError ? (
        <LoadError error={q.error} onRetry={() => void q.refetch()} what="your follow-up details" />
      ) : (q.data ?? []).length === 0 ? (
        <EmptyState icon={<CalendarX2 aria-hidden className="size-6" />} title="No follow-up arranged yet">
          Your care team will add follow-up visits here. Check My Instructions for what your discharge summary says.
        </EmptyState>
      ) : (
        <ul className="flex flex-col gap-3">
          {(q.data ?? []).map((f) => (
            <li key={f.id} className="rounded-lg border border-line bg-surface p-4">
              <p className="text-lg font-bold">{f.title}</p>
              <div className="mt-2">
                <StatusBadge domain="followup" value={f.status} />
              </div>
              <p className="mt-2 text-[15px] text-ink">{followupExplain[f.status] ?? ""}</p>
              <dl className="mt-3 flex flex-col gap-1.5 text-[15px]">
                <div className="flex items-start gap-2">
                  <dt className="sr-only">When</dt>
                  <CalendarClock aria-hidden className="mt-0.5 size-4 shrink-0 text-muted" />
                  <dd>{f.start_at ? fmtDateTime(f.start_at) : <span className="italic text-muted">Time not set yet</span>}</dd>
                </div>
                {f.provider_name && (
                  <div className="flex items-start gap-2">
                    <dt className="sr-only">With</dt>
                    <UserRound aria-hidden className="mt-0.5 size-4 shrink-0 text-muted" />
                    <dd>{f.provider_name}</dd>
                  </div>
                )}
                {f.location && (
                  <div className="flex items-start gap-2">
                    <dt className="sr-only">Where</dt>
                    <MapPin aria-hidden className="mt-0.5 size-4 shrink-0 text-muted" />
                    <dd>{f.location}</dd>
                  </div>
                )}
              </dl>
              {!f.availability_checked && f.status !== "confirmed_by_hospital" && f.start_at && (
                <div className="mt-3">
                  <Badge tone="attention" icon={TriangleAlert}>
                    Clinic availability not checked
                  </Badge>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {episode && (
        <Banner tone="info" className="mt-6">
          Need to change a visit? Call your hospital on {episode.hospital_phone}.
        </Banner>
      )}
    </div>
  );
}
