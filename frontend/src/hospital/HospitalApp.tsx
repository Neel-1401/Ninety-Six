// Hospital workspace routes (Agent C). Everything except /hospital/login requires a staff
// session; the backend enforces every permission regardless of what the UI shows.
import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router";
import { RequireRole } from "../lib/auth";
import { Spinner } from "../ui";
import { HospitalLayout } from "./Layout";
import LoginPage from "./LoginPage";

const OverviewPage = lazy(() => import("./OverviewPage"));
const PatientsPage = lazy(() => import("./PatientsPage"));
const IntakePage = lazy(() => import("./IntakePage"));
const EpisodePage = lazy(() => import("./episode/EpisodePage"));
const ApprovalsPage = lazy(() => import("./approvals/ApprovalsPage"));
const SettingsPage = lazy(() => import("./SettingsPage"));

export default function HospitalApp() {
  return (
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route
        element={
          <RequireRole roles={["doctor", "nurse"]}>
            <HospitalLayout />
          </RequireRole>
        }
      >
        <Route index element={<Lazy el={<OverviewPage />} />} />
        <Route path="patients" element={<Lazy el={<PatientsPage />} />} />
        <Route path="patients/:id" element={<Lazy el={<EpisodePage />} />} />
        <Route path="intake" element={<Lazy el={<IntakePage />} />} />
        <Route path="approvals" element={<Lazy el={<ApprovalsPage />} />} />
        <Route path="settings" element={<Lazy el={<SettingsPage />} />} />
        <Route
          path="*"
          element={
            <div className="py-10">
              <h1 className="text-xl font-bold">Page not found</h1>
              <p className="text-muted">Use the navigation to continue.</p>
            </div>
          }
        />
      </Route>
    </Routes>
  );
}

function Lazy({ el }: { el: React.ReactNode }) {
  return <Suspense fallback={<Spinner className="py-8" />}>{el}</Suspense>;
}
