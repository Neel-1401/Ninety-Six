// Staff sign-in (Agent C). Provisioned accounts only; the error never says which field was wrong.
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { api, errorMessage, isApiError } from "../lib/api";
import { homePathFor, useAfterSignIn, useMe } from "../lib/auth";
import type { MeOut } from "../lib/types";
import { Banner, Button, Field, TextInput } from "../ui";

export default function LoginPage() {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const loc = useLocation();
  const afterSignIn = useAfterSignIn();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState<{ u?: string; p?: string }>({});

  const login = useMutation({
    mutationFn: () => api.post<MeOut>("/api/auth/staff/login", { username: username.trim(), password }),
    onSuccess: (m) => {
      afterSignIn(m);
      const from = (loc.state as { from?: string } | null)?.from;
      navigate(from && from.startsWith("/hospital") ? from : homePathFor(m.user.role), { replace: true });
    },
  });

  if (me && (me.user.role === "doctor" || me.user.role === "nurse")) return <Navigate to="/hospital" replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const m: typeof missing = {};
    if (!username.trim()) m.u = "Enter your username.";
    if (!password) m.p = "Enter your password.";
    setMissing(m);
    if (m.u || m.p) return;
    login.mutate();
  };

  const err = login.error;
  const errText = isApiError(err) && (err.status === 401 || err.status === 400) ? "Those details didn't match an active staff account." : err ? errorMessage(err) : null;

  return (
    <main id="main" className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-5 py-12">
      <div className="mb-6 flex items-center gap-2.5">
        <span aria-hidden className="inline-flex size-9 items-center justify-center rounded-md bg-primary font-bold text-white">
          96
        </span>
        <div>
          <h1 className="text-2xl font-bold leading-tight">Staff sign in</h1>
          <p className="text-sm text-muted">Ninety-Six · hospital workspace</p>
        </div>
      </div>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-5">
        {errText && (
          <Banner tone="warning" live>
            {errText}
          </Banner>
        )}
        <Field label="Username" error={missing.u}>
          {({ id, describedBy, invalid }) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          )}
        </Field>
        <Field label="Password" error={missing.p}>
          {({ id, describedBy, invalid }) => (
            <TextInput
              id={id}
              type="password"
              aria-describedby={describedBy}
              invalid={invalid}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" loading={login.isPending} className="w-full">
          Sign in
        </Button>
        <p className="text-sm text-muted">
          Staff accounts are provisioned by the hospital. There is no self-registration and roles cannot be chosen here.
        </p>
      </form>
      <p className="mt-4 text-sm text-muted">
        Are you a patient?{" "}
        <Link to="/p/sign-in" className="font-bold text-primary-ink underline">
          Sign in with your phone number
        </Link>
      </p>
    </main>
  );
}
