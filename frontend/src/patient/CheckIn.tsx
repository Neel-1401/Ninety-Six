// Guided check-in (Agent D): one question at a time, progress, back/next, optional free text,
// review, submit. Drafts persist locally on every change and to the server (debounced).
// A single submission_id per check-in makes retries and double taps idempotent; the local
// draft is only cleared once the server confirms it received the answers.
import { useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowLeft, ArrowRight, Check, ClipboardCheck, Pencil, Send } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { api, errorMessage, isApiError, newSubmissionId } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { qk } from "../lib/queryKeys";
import type { PatientCheckInOut, Question } from "../lib/types";
import { Banner, Button, cn, EmptyState, TextArea } from "../ui";
import { store, useEpisode, useInstructions, useMyCheckin } from "./data";
import { ContactBlock, EmergencyLine, LoadError, LoadingBlock, PageTitle, Panel, ReceiptStages } from "./parts";

interface LocalDraft {
  answers: Record<string, string>;
  free_text: string;
  step: number;
  submission_id: string;
  saved_at: number;
}

const draftKey = (id: number) => `ns.checkin.${id}`;

function optionsFor(q: Question): { value: string; label: string }[] {
  if (q.options?.length) return q.options;
  if (q.type === "yes_no")
    return [
      { value: "yes", label: "Yes" },
      { value: "no", label: "No" },
    ];
  return [];
}

function answerLabel(q: Question, value: string | undefined): string {
  if (value == null || value === "") return "Not answered";
  if (q.type === "text") return value;
  return optionsFor(q).find((o) => o.value === value)?.label ?? value;
}

// ------------------------------------------------------------------ one question
function QuestionStep({
  q,
  value,
  onChange,
  error,
  headingRef,
  index,
  total,
}: {
  q: Question;
  value: string | undefined;
  onChange: (v: string) => void;
  error: string | null;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  index: number;
  total: number;
}) {
  const id = useId();
  const errId = `${id}-err`;
  const helpId = `${id}-help`;
  const describedBy = [q.help ? helpId : null, error ? errId : null].filter(Boolean).join(" ") || undefined;
  return (
    <fieldset aria-describedby={describedBy} className="min-w-0">
      <legend className="sr-only">
        Question {index + 1} of {total}: {q.text}
      </legend>
      <h2 ref={headingRef} tabIndex={-1} className="text-[22px] font-bold leading-snug text-ink focus:outline-none">
        {q.text}
      </h2>
      <p className="mt-1 text-sm text-muted">{q.required ? "Required" : "Optional"}</p>
      {q.help && (
        <p id={helpId} className="mt-2 text-[15px] text-muted">
          {q.help}
        </p>
      )}
      {error && (
        <p id={errId} role="alert" className="mt-3 rounded-md border border-urgent/40 bg-urgent-soft px-3 py-2 font-bold text-urgent-ink">
          {error}
        </p>
      )}
      {q.type === "text" ? (
        <TextArea
          aria-label={q.text}
          invalid={!!error}
          rows={5}
          className="mt-4 text-base"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <div className="mt-4 flex flex-col gap-3">
          {optionsFor(q).map((o) => {
            const checked = value === o.value;
            return (
              <label
                key={o.value}
                className={cn(
                  "flex min-h-14 cursor-pointer items-center gap-3 rounded-lg border-2 px-4 py-3 text-lg font-bold transition-colors duration-150",
                  "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-primary",
                  checked ? "border-primary bg-primary-soft text-primary-ink" : "border-line-strong bg-surface text-ink hover:border-primary",
                )}
              >
                <input
                  type="radio"
                  name={`q-${q.id}`}
                  value={o.value}
                  checked={checked}
                  onChange={() => onChange(o.value)}
                  className="size-6 shrink-0 cursor-pointer accent-primary focus-visible:outline-none"
                />
                <span className="min-w-0 flex-1">{o.label}</span>
                {checked && <Check aria-hidden className="size-5 shrink-0" />}
              </label>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}

// ------------------------------------------------------------------ wizard
function Wizard({ checkin, emergency }: { checkin: PatientCheckInOut; emergency: string }) {
  const qc = useQueryClient();
  const reduce = useReducedMotion();
  const questions = checkin.questions ?? [];
  const total = questions.length;
  const FREE = total; // free-text step index
  const REVIEW = total + 1; // review step index

  // ---- restore draft (local first, then server), and a stable submission id
  const initial = useMemo(() => {
    const local = store.get<LocalDraft>(draftKey(checkin.id));
    const serverAnswers = checkin.draft?.answers ?? {};
    const serverText = checkin.draft?.free_text ?? "";
    const hasLocal = !!local && (Object.keys(local.answers ?? {}).length > 0 || !!local.free_text);
    const hasServer = Object.keys(serverAnswers).length > 0 || !!serverText;
    const answers = hasLocal ? local!.answers : serverAnswers;
    const free_text = hasLocal ? local!.free_text : serverText;
    const valid = new Set(questions.map((q) => q.id));
    const cleaned = Object.fromEntries(Object.entries(answers).filter(([k]) => valid.has(k)));
    return {
      answers: cleaned,
      free_text: free_text ?? "",
      step: hasLocal ? Math.min(Math.max(local!.step ?? 0, 0), REVIEW) : 0,
      submission_id: local?.submission_id ?? newSubmissionId(),
      restored: hasLocal || hasServer,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkin.id]);

  const [answers, setAnswers] = useState<Record<string, string>>(initial.answers);
  const [freeText, setFreeText] = useState(initial.free_text);
  const [step, setStep] = useState(initial.step);
  const [dir, setDir] = useState(1);
  const [returnToReview, setReturnToReview] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [serverDraft, setServerDraft] = useState<"idle" | "saving" | "saved" | "local-only">("idle");
  const [showRestored, setShowRestored] = useState(initial.restored);
  const submissionId = useRef(initial.submission_id);
  const inFlight = useRef(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);

  // ---- persist locally on every change (including the submission id)
  useEffect(() => {
    store.set(draftKey(checkin.id), {
      answers,
      free_text: freeText,
      step,
      submission_id: submissionId.current,
      saved_at: Date.now(),
    } satisfies LocalDraft);
  }, [answers, freeText, step, checkin.id]);

  // ---- debounced server draft
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    setServerDraft("saving");
    const t = window.setTimeout(() => {
      api
        .put(`/api/me/checkins/${checkin.id}/draft`, { answers, free_text: freeText || null })
        .then(() => setServerDraft("saved"))
        .catch(() => setServerDraft("local-only"));
    }, 900);
    return () => window.clearTimeout(t);
  }, [answers, freeText, checkin.id]);

  // ---- move focus to the new step's heading so screen readers announce it
  useEffect(() => {
    const t = window.setTimeout(() => headingRef.current?.focus(), reduce ? 0 : 260);
    return () => window.clearTimeout(t);
  }, [step, reduce]);

  const go = useCallback((to: number) => {
    setDir(to >= step ? 1 : -1);
    setError(null);
    setStep(to);
  }, [step]);

  function next() {
    if (step < total) {
      const q = questions[step];
      const v = answers[q.id];
      if (q.required && (v == null || v === "" || (q.type === "text" && !v.trim()))) {
        setError(q.type === "text" ? "Please type an answer to continue." : "Please choose an answer to continue.");
        headingRef.current?.focus();
        return;
      }
    }
    if (returnToReview) {
      setReturnToReview(false);
      go(REVIEW);
      return;
    }
    go(Math.min(step + 1, REVIEW));
  }

  function back() {
    if (step === 0) return;
    go(step - 1);
  }

  async function submit() {
    if (inFlight.current) return; // double-tap guard
    const missing = questions.findIndex((q) => q.required && !(answers[q.id] ?? "").toString().trim());
    if (missing >= 0) {
      go(missing);
      setError("Please answer this question before sending.");
      return;
    }
    inFlight.current = true;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const out = await api.post<PatientCheckInOut>(`/api/me/checkins/${checkin.id}/submit`, {
        submission_id: submissionId.current,
        answers: questions
          .filter((q) => answers[q.id] != null && answers[q.id] !== "")
          .map((q) => ({ question_id: q.id, value: answers[q.id] })),
        free_text: freeText.trim() || null,
      });
      // Server confirmed receipt: only now is it safe to drop the local copy.
      store.remove(draftKey(checkin.id));
      qc.setQueryData(qk.myCheckin(checkin.id), out);
      void qc.invalidateQueries({ queryKey: ["checkins"] });
      void qc.invalidateQueries({ queryKey: ["today"] });
    } catch (e) {
      if (isApiError(e) && e.status === 409) {
        // Already submitted (e.g. from another tab): show the existing receipt.
        store.remove(draftKey(checkin.id));
        await qc.invalidateQueries({ queryKey: qk.myCheckin(checkin.id) });
      } else if (isApiError(e) && e.status === 422) {
        const fields = e.fieldErrors;
        const badQ = questions.findIndex((q) => Object.keys(fields).some((f) => f.includes(q.id)));
        setSubmitError(e.message || "Some answers need attention.");
        if (badQ >= 0) go(badQ);
      } else {
        setSubmitError(
          `${errorMessage(e)} Your answers are still saved on this phone — press Send again when you're ready.`,
        );
      }
    } finally {
      inFlight.current = false;
      setSubmitting(false);
    }
  }

  const answeredCount = questions.filter((q) => answers[q.id] != null && answers[q.id] !== "").length;
  const progressNow = step >= REVIEW ? total : Math.min(step, total);
  const stepLabel =
    step < total ? `Question ${step + 1} of ${total}` : step === FREE ? "Anything else (optional)" : "Check your answers";

  const variants = {
    enter: (d: number) => ({ opacity: 0, x: reduce ? 0 : d * 24 }),
    center: { opacity: 1, x: 0, transition: { duration: 0.24, ease: [0.23, 1, 0.32, 1] as const } },
    exit: (d: number) => ({ opacity: 0, x: reduce ? 0 : d * -18, transition: { duration: 0.16, ease: "easeIn" as const } }),
  };

  return (
    <div className="flex flex-col">
      {showRestored && (
        <Banner
          tone="info"
          className="mb-4"
          actions={
            <Button variant="quiet" size="sm" onClick={() => setShowRestored(false)}>
              OK
            </Button>
          }
        >
          We restored your unfinished answers.
        </Banner>
      )}

      {/* progress */}
      <div className="mb-5">
        <div className="flex items-baseline justify-between gap-3">
          <p className="font-bold text-ink" aria-live="polite">
            {stepLabel}
          </p>
          <p className="text-sm text-muted">
            {answeredCount} of {total} answered
          </p>
        </div>
        <div
          role="progressbar"
          aria-label="Check-in progress"
          aria-valuemin={0}
          aria-valuemax={total + 1}
          aria-valuenow={step >= REVIEW ? total + 1 : progressNow}
          className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-sunken"
        >
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out"
            style={{ width: `${Math.round(((step >= REVIEW ? total + 1 : step) / (total + 1)) * 100)}%` }}
          />
        </div>
      </div>

      <div className="relative min-h-[18rem]">
        <AnimatePresence mode="wait" custom={dir} initial={false}>
          <motion.div key={step} custom={dir} variants={variants} initial="enter" animate="center" exit="exit">
            {step < total && (
              <QuestionStep
                q={questions[step]}
                value={answers[questions[step].id]}
                onChange={(v) => {
                  setAnswers((a) => ({ ...a, [questions[step].id]: v }));
                  setError(null);
                }}
                error={error}
                headingRef={headingRef}
                index={step}
                total={total}
              />
            )}
            {step === FREE && (
              <div>
                <h2 ref={headingRef} tabIndex={-1} className="text-[22px] font-bold leading-snug focus:outline-none">
                  Anything else you want your care team to know?
                </h2>
                <p className="mt-1 text-sm text-muted">Optional</p>
                <label htmlFor="free-text" className="sr-only">
                  Anything else you want your care team to know? (optional)
                </label>
                <TextArea
                  id="free-text"
                  rows={6}
                  maxLength={2000}
                  className="mt-4 text-base"
                  placeholder="For example: a new symptom, a question about your medicines, or anything you're unsure about."
                  value={freeText}
                  onChange={(e) => setFreeText(e.target.value)}
                />
                <p className="mt-1 text-right text-xs text-muted">{freeText.length} / 2000</p>
                <EmergencyLine number={emergency} className="mt-3" />
              </div>
            )}
            {step === REVIEW && (
              <div>
                <h2 ref={headingRef} tabIndex={-1} className="text-[22px] font-bold leading-snug focus:outline-none">
                  Check your answers
                </h2>
                <p className="mt-1 text-[15px] text-muted">Nothing is sent until you press Send.</p>
                <dl className="mt-4 flex flex-col divide-y divide-line rounded-lg border border-line bg-surface">
                  {questions.map((q, i) => (
                    <div key={q.id} className="flex items-start justify-between gap-3 px-4 py-3">
                      <div className="min-w-0">
                        <dt className="text-[15px] text-muted">{q.text}</dt>
                        <dd className="mt-0.5 break-words font-bold text-ink">{answerLabel(q, answers[q.id])}</dd>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="min-h-11 shrink-0"
                        icon={<Pencil aria-hidden className="size-4" />}
                        aria-label={`Change answer: ${q.text}`}
                        onClick={() => {
                          setReturnToReview(true);
                          go(i);
                        }}
                      >
                        Change
                      </Button>
                    </div>
                  ))}
                  <div className="flex items-start justify-between gap-3 px-4 py-3">
                    <div className="min-w-0">
                      <dt className="text-[15px] text-muted">Anything else</dt>
                      <dd className="mt-0.5 whitespace-pre-line break-words font-bold text-ink">{freeText.trim() || "Nothing added"}</dd>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="min-h-11 shrink-0"
                      icon={<Pencil aria-hidden className="size-4" />}
                      aria-label="Change your extra comments"
                      onClick={() => {
                        setReturnToReview(true);
                        go(FREE);
                      }}
                    >
                      Change
                    </Button>
                  </div>
                </dl>
                {submitError && (
                  <Banner tone="warning" className="mt-4" live title="Not sent yet">
                    {submitError}
                  </Banner>
                )}
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* actions */}
      <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-between">
        <Button
          variant="secondary"
          size="lg"
          onClick={back}
          disabled={step === 0 || submitting}
          icon={<ArrowLeft aria-hidden className="size-5" />}
        >
          Back
        </Button>
        {step < REVIEW ? (
          <Button size="lg" onClick={next} icon={<ArrowRight aria-hidden className="size-5" />}>
            {returnToReview ? "Back to review" : step === FREE ? "Review answers" : "Next"}
          </Button>
        ) : (
          <Button size="lg" onClick={() => void submit()} loading={submitting} icon={<Send aria-hidden className="size-5" />}>
            {submitting ? "Sending…" : "Send to my care team"}
          </Button>
        )}
      </div>

      <p className="mt-4 text-center text-sm text-muted" role="status">
        {serverDraft === "saving"
          ? "Saving your answers…"
          : serverDraft === "saved"
            ? "Answers saved. You can finish later."
            : serverDraft === "local-only"
              ? "Answers saved on this phone only (couldn't reach the server)."
              : "Your answers are saved as you go."}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ receipt view
export function SubmittedView({ checkin }: { checkin: PatientCheckInOut }) {
  const { episode, episodeId } = useEpisode();
  const instructions = useInstructions(episodeId);
  return (
    <div className="flex flex-col gap-5">
      <Banner tone="success" title="Your answers were received" live>
        Sent {fmtDateTime(checkin.submitted_at ?? checkin.receipt.received_at)}. Here's what has happened since.
      </Banner>
      <Panel>
        <ReceiptStages receipt={checkin.receipt} />
      </Panel>
      <section aria-label="Warning signs and contacts">
        <h2 className="mb-2 text-lg font-bold">While you wait for your care team</h2>
        {instructions.data?.reviewed && instructions.data.warning_signs.length > 0 && (
          <Panel className="mb-3">
            <h3 className="font-bold">Warning signs from your reviewed instructions</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {instructions.data.warning_signs.map((item, index) => <li key={`${item.source_ref}-${index}`}>{item.text}</li>)}
            </ul>
          </Panel>
        )}
        <ContactBlock hospitalPhone={episode?.hospital_phone ?? "Contact your hospital care team"} emergency={episode?.emergency_number ?? "108"} />
      </section>
      {checkin.answers.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-bold">What you sent</h2>
          <dl className="flex flex-col divide-y divide-line rounded-lg border border-line bg-surface">
            {checkin.answers.map((a) => (
              <div key={a.question_id} className="px-4 py-3">
                <dt className="text-[15px] text-muted">{a.question_text}</dt>
                <dd className="mt-0.5 break-words font-bold">{a.label}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {checkin.free_text && (
        <section>
          <h2 className="mb-2 text-lg font-bold">Your additional concern</h2>
          <Panel><p className="whitespace-pre-wrap break-words">{checkin.free_text}</p></Panel>
        </section>
      )}
      <Link
        to="/p"
        className="flex min-h-12 items-center justify-center rounded-md border border-line-strong bg-surface px-5 font-bold hover:bg-sunken"
      >
        Back to Today
      </Link>
    </div>
  );
}

// ------------------------------------------------------------------ page
export function CheckInPage() {
  const { id } = useParams();
  const checkinId = Number(id);
  const q = useMyCheckin(Number.isFinite(checkinId) ? checkinId : null);
  const { episode } = useEpisode();
  const emergency = episode?.emergency_number ?? "108";

  if (q.isLoading) return <LoadingBlock />;
  if (q.isError || !q.data) return <LoadError error={q.error} onRetry={() => void q.refetch()} what="this check-in" />;
  const c = q.data;

  return (
    <div>
      <Link to="/p" className="mb-3 inline-flex min-h-11 items-center gap-1.5 font-bold text-primary-ink">
        <ArrowLeft aria-hidden className="size-4" />
        Today
      </Link>
      <PageTitle sub={c.kind === "concern" ? "A concern you reported" : `Opened ${fmtDateTime(c.due_at)}`}>
        {c.title}
      </PageTitle>

      {c.receipt.received ? (
        <SubmittedView checkin={c} />
      ) : c.status === "upcoming" ? (
        <EmptyState icon={<ClipboardCheck aria-hidden className="size-6" />} title="This check-in isn't open yet">
          It opens on {fmtDateTime(c.due_at)}. It will appear on Today when it opens.
        </EmptyState>
      ) : c.status === "missed" || c.status === "cancelled" ? (
        <EmptyState
          icon={<ClipboardCheck aria-hidden className="size-6" />}
          title={c.status === "missed" ? "This check-in has closed" : "This check-in was cancelled"}
          action={
            <Link to="/p/concern" className="font-bold text-primary-ink underline underline-offset-2">
              Report a concern instead
            </Link>
          }
        >
          {c.status === "missed"
            ? `It was open until ${fmtDateTime(c.available_until)}. Your care team can see that it wasn't completed.`
            : "Your care team changed your plan."}
        </EmptyState>
      ) : !c.questions || c.questions.length === 0 ? (
        <LoadError error={null} what="the questions for this check-in" onRetry={() => void q.refetch()} />
      ) : (
        <>
          <p className="mb-5 text-[15px] text-muted">
            {c.questions.length} short questions from your care plan. It takes about two minutes.
          </p>
          <Wizard checkin={c} emergency={emergency} />
        </>
      )}
    </div>
  );
}
