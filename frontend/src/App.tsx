// App shell and top-level routing (lead-owned).
//   /hospital/*  -> Agent C (staff login at /hospital/login, everything else staff-only)
//   /p/*         -> Agent D (sign-in at /p/sign-in, invitation at /p/invite/:token)
//   DemoDrawer   -> Agent C (renders nothing unless the backend is in demo mode)
import { Building2, Smartphone } from "lucide-react";
import { lazy, Suspense } from "react";
import { Link, Navigate, Route, Routes } from "react-router";
import { homePathFor, useMe } from "./lib/auth";
import { LiveProvider } from "./lib/live";
import { Spinner } from "./ui";

const HospitalApp = lazy(() => import("./hospital/HospitalApp"));
const PatientApp = lazy(() => import("./patient/PatientApp"));
const DemoDrawer = lazy(() => import("./demo/DemoDrawer"));

export function App() {
  const { data: me } = useMe();
  return (
    <LiveProvider enabled={!!me}>
      <a href="#main" className="skip-link">
        Skip to main content
      </a>
      <Suspense fallback={<Spinner className="p-8" />}>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/hospital/*" element={<HospitalApp />} />
          <Route path="/p/*" element={<PatientApp />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
        <DemoDrawer />
      </Suspense>
    </LiveProvider>
  );
}

function Landing() {
  const { data: me, isLoading } = useMe();
  if (isLoading) return <Spinner className="p-8" label="Checking your session…" />;
  if (me) return <Navigate to={homePathFor(me.user.role)} replace />;
  return (
    <main id="main" className="mx-auto flex min-h-dvh max-w-3xl flex-col justify-center gap-8 px-5 py-12">
      <div>
        <p className="text-sm font-bold text-primary-ink">Prototype · fictional patients only</p>
        <h1 className="mt-1 text-3xl font-bold">Ninety-Six</h1>
        <p className="mt-2 max-w-[60ch] text-muted">
          Coordinates the first 96 hours after hospital discharge: clinician-reviewed instructions, guided check-ins and
          follow-up, with a doctor approving every external action. It does not diagnose or change treatment.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Link
          to="/hospital/login"
          className="group flex min-h-28 flex-col justify-between rounded-lg border border-line bg-surface p-5 hover:border-primary"
        >
          <Building2 aria-hidden className="size-6 text-primary" />
          <span>
            <span className="block text-lg font-bold group-hover:text-primary-ink">Hospital staff</span>
            <span className="text-sm text-muted">Doctors and nurses sign in with a provisioned account</span>
          </span>
        </Link>
        <Link
          to="/p/sign-in"
          className="group flex min-h-28 flex-col justify-between rounded-lg border border-line bg-surface p-5 hover:border-primary"
        >
          <Smartphone aria-hidden className="size-6 text-primary" />
          <span>
            <span className="block text-lg font-bold group-hover:text-primary-ink">Patients</span>
            <span className="text-sm text-muted">Sign in with the phone number your hospital registered</span>
          </span>
        </Link>
      </div>
    </main>
  );
}

function NotFound() {
  return (
    <main id="main" className="mx-auto max-w-xl px-5 py-16">
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p className="mt-2 text-muted">This page doesn't exist or you don't have access to it.</p>
      <Link to="/" className="mt-4 inline-block font-bold text-primary-ink underline">
        Go to the start page
      </Link>
    </main>
  );
}
