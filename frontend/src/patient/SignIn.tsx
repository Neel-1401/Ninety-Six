// Patient sign-in: phone number + one-time code (Agent D). No passwords, no self-registration.
// Copy never reveals whether a number is registered. Invitation tokens stay in the URL/memory
// only and grant nothing by themselves.
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowLeft, KeyRound, ShieldCheck, Smartphone } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router";
import { api, errorMessage, isApiError } from "../lib/api";
import { homePathFor, useAfterSignIn, useMe } from "../lib/auth";
import type { MeOut } from "../lib/types";
import { Banner, Button, Field, Spinner, TextInput } from "../ui";
import { useDemoMode } from "./data";

/** "98765 43210" / "+91 98765-43210" / "919876543210" -> "+919876543210". Returns null if implausible. */
export function normalisePhone(input: string): string | null {
  const trimmed = input.trim();
  const digits = trimmed.replace(/\D/g, "");
  if (!digits) return null;
  let e164: string;
  if (trimmed.startsWith("+")) e164 = `+${digits}`;
  else if (digits.length === 10) e164 = `+91${digits}`;
  else if (digits.length === 12 && digits.startsWith("91")) e164 = `+${digits}`;
  else if (digits.length === 11 && digits.startsWith("0")) e164 = `+91${digits.slice(1)}`;
  else e164 = `+${digits}`;
  return /^\+\d{8,15}$/.test(e164) ? e164 : null;
}

function useCountdown(until: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!until) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [until]);
  return until ? Math.max(0, Math.ceil((until - now) / 1000)) : 0;
}

interface RequestCodeOut {
  message: string;
  resend_after_seconds: number;
}

export function SignIn({ invite = false }: { invite?: boolean }) {
  const { token } = useParams();
  const { data: me, isLoading } = useMe();
  const { data: demo } = useDemoMode();
  const after = useAfterSignIn();
  const navigate = useNavigate();
  const loc = useLocation();
  const reduce = useReducedMotion();

  const [step, setStep] = useState<"phone" | "code">("phone");
  const [phoneInput, setPhoneInput] = useState("");
  const [phone, setPhone] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resendAt, setResendAt] = useState<number | null>(null);
  const secondsLeft = useCountdown(resendAt);
  const codeRef = useRef<HTMLInputElement>(null);
  const phoneRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (step === "code") codeRef.current?.focus();
    else phoneRef.current?.focus();
  }, [step]);

  if (isLoading) return <Spinner className="p-8" label="Checking your session…" />;
  if (me?.user.role === "patient") {
    const from = (loc.state as { from?: string } | null)?.from;
    return <Navigate to={from && from.startsWith("/p") ? from : "/p"} replace />;
  }

  async function requestCode(target: string) {
    setBusy(true);
    setFormError(null);
    try {
      const body: Record<string, string> = { phone: target };
      if (invite && token) body.invitation_token = token;
      const r = await api.post<RequestCodeOut>("/api/auth/patient/request-code", body);
      setNotice(r.message || "If this number is registered with us, we've sent a code.");
      setResendAt(Date.now() + (r.resend_after_seconds ?? 60) * 1000);
      setStep("code");
    } catch (e) {
      if (isApiError(e) && e.status === 429) {
        const wait = Number(e.details?.retry_after_seconds ?? 60);
        setResendAt(Date.now() + wait * 1000);
        setFormError(`Too many attempts. Please wait ${wait} seconds and try again.`);
        if (step === "phone") setStep("code");
      } else if (isApiError(e) && e.status === 422) {
        setPhoneError("Enter the mobile number your hospital registered, for example +91 98765 43210.");
      } else {
        setFormError(errorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function onPhoneSubmit(ev: FormEvent) {
    ev.preventDefault();
    const p = normalisePhone(phoneInput);
    if (!p) {
      setPhoneError("Enter the mobile number your hospital registered, for example +91 98765 43210.");
      return;
    }
    setPhoneError(null);
    setPhone(p);
    void requestCode(p);
  }

  async function onCodeSubmit(ev: FormEvent) {
    ev.preventDefault();
    const c = code.replace(/\D/g, "");
    if (c.length !== 6) {
      setCodeError("Enter the 6-digit code from the text message.");
      return;
    }
    if (!phone) return;
    setCodeError(null);
    setFormError(null);
    setBusy(true);
    try {
      const out = await api.post<MeOut>("/api/auth/patient/verify", { phone, code: c });
      after(out);
      if (out.user.role !== "patient") {
        navigate(homePathFor(out.user.role), { replace: true });
        return;
      }
      navigate("/p", { replace: true });
    } catch (e) {
      if (isApiError(e) && e.status === 429) {
        const wait = Number(e.details?.retry_after_seconds ?? 60);
        setFormError(`Too many attempts. Please wait ${wait} seconds, then request a new code.`);
      } else if (isApiError(e) && (e.code === "invalid_code" || e.status === 400 || e.status === 401)) {
        setCodeError(e.message || "That code didn't work. Check it, or request a new code.");
      } else {
        setFormError(errorMessage(e));
      }
      setCode("");
      codeRef.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  const slide = {
    initial: { opacity: 0, x: reduce ? 0 : 16 },
    animate: { opacity: 1, x: 0, transition: { duration: 0.24, ease: [0.23, 1, 0.32, 1] as const } },
    exit: { opacity: 0, x: reduce ? 0 : -12, transition: { duration: 0.16, ease: "easeIn" as const } },
  };

  return (
    <main id="main" className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-5 pb-10 pt-8">
      <Link to="/" className="mb-8 inline-flex items-center gap-2 self-start text-sm font-bold text-primary-ink">
        <span aria-hidden className="inline-flex size-8 items-center justify-center rounded-md bg-primary text-sm text-white">
          96
        </span>
        Ninety-Six
      </Link>

      <h1 className="text-[28px] font-bold leading-tight">
        {invite ? "Your hospital invited you" : "Sign in to your recovery plan"}
      </h1>
      <p className="mt-2 text-muted">
        {invite
          ? "Your hospital set up online follow-up for the days after you went home. To protect your information, sign in with the mobile number they registered — the link alone doesn't open anything."
          : "Use the mobile number your hospital registered. We'll text you a 6-digit code. There's no password."}
      </p>

      {(loc.state as { expired?: boolean } | null)?.expired && (
        <Banner tone="info" className="mt-5">
          You've been signed out. Please sign in again.
        </Banner>
      )}

      {demo && (
        <Banner tone="info" className="mt-5" title="Demo mode">
          No real text messages are sent. The code appears in the demo drawer's outbox.
        </Banner>
      )}

      {formError && (
        <Banner tone="warning" className="mt-5" live>
          {formError}
        </Banner>
      )}

      <div className="mt-6">
        <AnimatePresence mode="wait" initial={false}>
          {step === "phone" ? (
            <motion.form key="phone" {...slide} onSubmit={onPhoneSubmit} noValidate className="flex flex-col gap-5">
              <Field
                label="Mobile number"
                hint="Include the country code if you're not in India, for example +44 7700 900123."
                error={phoneError}
                required
              >
                {({ id, describedBy, invalid }) => (
                  <TextInput
                    ref={phoneRef}
                    id={id}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    type="tel"
                    inputMode="tel"
                    autoComplete="tel"
                    placeholder="+91 98765 43210"
                    value={phoneInput}
                    onChange={(e) => setPhoneInput(e.target.value)}
                    className="min-h-12 text-lg"
                  />
                )}
              </Field>
              <Button type="submit" size="lg" loading={busy} icon={<Smartphone aria-hidden className="size-5" />}>
                Text me a code
              </Button>
            </motion.form>
          ) : (
            <motion.form key="code" {...slide} onSubmit={onCodeSubmit} noValidate className="flex flex-col gap-5">
              {notice && (
                <p role="status" className="text-[15px] text-ink">
                  {notice}
                  {phone && (
                    <>
                      {" "}
                      Number entered: <span className="font-bold">{phone}</span>
                    </>
                  )}
                </p>
              )}
              <Field label="6-digit code" hint="You can paste the code. It expires after 10 minutes and works once." error={codeError} required>
                {({ id, describedBy, invalid }) => (
                  <TextInput
                    ref={codeRef}
                    id={id}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="[0-9]*"
                    maxLength={12}
                    placeholder="123456"
                    value={code}
                    onChange={(e) => setCode(e.target.value.replace(/[^\d\s-]/g, ""))}
                    className="min-h-12 text-center font-mono text-2xl tracking-[0.4em]"
                  />
                )}
              </Field>
              <Button type="submit" size="lg" loading={busy} icon={<KeyRound aria-hidden className="size-5" />}>
                Sign in
              </Button>
              <div className="flex flex-col gap-2 sm:flex-row sm:justify-between">
                <Button
                  variant="ghost"
                  size="lg"
                  disabled={secondsLeft > 0 || busy || !phone}
                  onClick={() => phone && void requestCode(phone)}
                >
                  {secondsLeft > 0 ? `Send a new code in ${secondsLeft} s` : "Send a new code"}
                </Button>
                <Button
                  variant="quiet"
                  size="lg"
                  icon={<ArrowLeft aria-hidden className="size-5" />}
                  onClick={() => {
                    setStep("phone");
                    setCode("");
                    setCodeError(null);
                    setFormError(null);
                  }}
                >
                  Change number
                </Button>
              </div>
            </motion.form>
          )}
        </AnimatePresence>
      </div>

      <div className="mt-auto pt-10">
        <p className="flex items-start gap-2 text-sm text-muted">
          <ShieldCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
          Only your hospital can register you. If you didn't receive a code, check the number or ask your hospital to
          confirm the number they have for you.
        </p>
        <p className="mt-3 text-sm text-muted">
          Hospital staff?{" "}
          <Link to="/hospital/login" className="font-bold text-primary-ink underline underline-offset-2">
            Staff sign-in
          </Link>
        </p>
      </div>
    </main>
  );
}
