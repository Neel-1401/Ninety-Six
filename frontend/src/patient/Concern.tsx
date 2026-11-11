// Report a concern (Agent D): free text with an idempotent submission id and the same honest
// receipt as check-ins. Draft text survives reloads until the server confirms receipt.
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Send } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, errorMessage, isApiError, newSubmissionId } from "../lib/api";
import { qk } from "../lib/queryKeys";
import type { PatientCheckInOut } from "../lib/types";
import { Banner, Button, Field, TextArea } from "../ui";
import { monitoringState, store, useEpisode, useMyCheckin } from "./data";
import { SubmittedView } from "./CheckIn";
import { EmergencyLine, PageTitle, Panel } from "./parts";

interface ConcernDraft {
  text: string;
  submission_id: string;
}

export function ConcernPage() {
  const { episode, episodeId } = useEpisode();
  const qc = useQueryClient();
  const key = `ns.concern.${episodeId ?? "none"}`;
  const [text, setText] = useState(() => store.get<ConcernDraft>(key)?.text ?? "");
  const submissionId = useRef(store.get<ConcernDraft>(key)?.submission_id ?? newSubmissionId());
  const [error, setError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [sentId, setSentId] = useState<number | null>(null);
  const sent = useMyCheckin(sentId);
  const restored = useRef(!!store.get<ConcernDraft>(key)?.text);

  useEffect(() => {
    if (sentId == null) store.set(key, { text, submission_id: submissionId.current } satisfies ConcernDraft);
  }, [text, key, sentId]);

  if (!episode) return null;
  const mstate = monitoringState(episode);
  if (mstate !== "active") {
    return (
      <div>
        <PageTitle>Report a concern</PageTitle>
        <Banner
          tone="info"
          title={
            mstate === "not_started"
              ? "Your follow-up is still being set up"
              : mstate === "ended"
                ? "The monitoring period has ended"
                : "Online follow-up isn't available"
          }
        >
          Please contact your hospital directly on {episode.hospital_phone}. In an emergency call {episode.emergency_number}.
        </Banner>
      </div>
    );
  }

  if (sentId != null && sent.data) {
    return (
      <div>
        <PageTitle>Concern sent</PageTitle>
        <SubmittedView checkin={sent.data} />
      </div>
    );
  }

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    if (inFlight.current) return;
    if (!text.trim()) {
      setError("Please describe what's worrying you.");
      return;
    }
    setError(null);
    setSubmitError(null);
    inFlight.current = true;
    setBusy(true);
    try {
      const out = await api.post<PatientCheckInOut>(`/api/me/episodes/${episodeId}/concerns`, {
        submission_id: submissionId.current,
        text: text.trim(),
      });
      store.remove(key); // only after the server confirmed receipt
      qc.setQueryData(qk.myCheckin(out.id), out);
      void qc.invalidateQueries({ queryKey: ["checkins"] });
      void qc.invalidateQueries({ queryKey: ["today"] });
      setSentId(out.id);
    } catch (e) {
      if (isApiError(e) && e.status === 422) setError(e.message || "Please check what you wrote.");
      else setSubmitError(`${errorMessage(e)} Your message is still here — press Send again when you're ready.`);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div>
      <Link to="/p" className="mb-3 inline-flex min-h-11 items-center gap-1.5 font-bold text-primary-ink">
        <ArrowLeft aria-hidden className="size-4" />
        Today
      </Link>
      <PageTitle sub="Tell your care team in your own words. A person may not see this straight away.">
        Report a concern
      </PageTitle>
      <Panel className="mb-5 border-urgent/30 bg-urgent-soft">
        <EmergencyLine number={episode.emergency_number} />
      </Panel>
      {restored.current && (
        <Banner tone="info" className="mb-4">
          We restored the message you hadn't sent yet.
        </Banner>
      )}
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
        <Field label="What's worrying you?" hint="For example: a new symptom, a problem with your medicines, or a question." error={error} required>
          {({ id, describedBy, invalid }) => (
            <TextArea
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              rows={7}
              maxLength={2000}
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="text-base"
            />
          )}
        </Field>
        {submitError && (
          <Banner tone="warning" live title="Not sent yet">
            {submitError}
          </Banner>
        )}
        <Button type="submit" size="lg" loading={busy} icon={<Send aria-hidden className="size-5" />}>
          {busy ? "Sending…" : "Send to my care team"}
        </Button>
      </form>
    </div>
  );
}
