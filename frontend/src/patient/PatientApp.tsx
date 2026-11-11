// Patient app (Agent D) — mounted by App.tsx at /p/*.
//   /p/sign-in, /p/invite/:token       public (phone + one-time code)
//   /p, /p/instructions, /p/messages,
//   /p/follow-up, /p/checkins/:id,
//   /p/concern                          patient session required (server enforces; this is UX only)
import { Navigate, Route, Routes } from "react-router";
import { RequireRole } from "../lib/auth";
import { CheckInPage } from "./CheckIn";
import { ConcernPage } from "./Concern";
import { FollowUpPage, InstructionsPage, MessagesPage } from "./InfoPages";
import { PatientLayout } from "./PatientLayout";
import { SignIn } from "./SignIn";
import { Today } from "./Today";

export default function PatientApp() {
  return (
    <Routes>
      <Route path="sign-in" element={<SignIn />} />
      <Route path="invite/:token" element={<SignIn invite />} />
      <Route
        element={
          <RequireRole roles={["patient"]}>
            <PatientLayout />
          </RequireRole>
        }
      >
        <Route index element={<Today />} />
        <Route path="instructions" element={<InstructionsPage />} />
        <Route path="messages" element={<MessagesPage />} />
        <Route path="follow-up" element={<FollowUpPage />} />
        <Route path="checkins/:id" element={<CheckInPage />} />
        <Route path="concern" element={<ConcernPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/p" replace />} />
    </Routes>
  );
}
