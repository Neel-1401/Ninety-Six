# Decisions log (lead)

The project owner gave the team a free hand on 11 Nov 2026; these are the calls made and why.

| # | Decision | Reason |
|---|---|---|
| D1 | Rebuild persistence on **SQLAlchemy 2.0 + Alembic**, move the old SQLModel scaffold to `backend/legacy/` as reference | The original code had never run; the brief requires episodes separate from patients, auth, payload-bound approvals and durable tasks — a redesign of every table. Logic (parser regexes, risk inputs, triage keywords, drug KB, Twilio/GCal/openFDA/SMTP calls, samples) is ported, not discarded. |
| D2 | **Shared working tree with strict per-agent file ownership** instead of git worktrees | Milestones must be integrated and runnable continuously; A↔B and C/D↔A depend on each other's live code. Ownership table in CONTRACTS.md §1 prevents conflicting edits; the lead owns all shared/dependency files. |
| D3 | Implementation agents are **forks of the lead** (inherit the full brief + contracts); the evaluator is a **fresh agent** given only `docs/BRIEF.md` + setup instructions | Forks avoid re-deriving context; a fresh evaluator stays independent of implementation reasoning. |
| D4 | Tailwind CSS v4 replaces the earlier "plain CSS" note in CLAUDE.md | The new brief explicitly prefers Tailwind. |
| D5 | Typography **Atkinson Hyperlegible** (ui-ux-pro-max design-system result for healthcare/accessibility); **rejected** that result's Neumorphism style and "Hero + Testimonials" pattern | Neumorphism is flagged high accessibility risk by the skill itself and the pattern is for landing pages; impeccable-lite "Operate" mode fits a clinical tool. |
| D6 | `impeccable-lite` read from **github.com/ilindaniel/impeccable-lite** | The URL in the brief (`ilmandude/impeccable-lite`) returns 404 (user does not exist); the only public repo of that name is ilindaniel's. Recorded so nobody claims the original URL was used. |
| D7 | Security timers (OTP, sessions, leases) use **real time**; scheduling uses the **simulated clock** | Fast-forwarding must not expire or extend credentials, and must not mix clocks for scheduling. |
| D8 | Routine authorisation covers only `checkin_reminder_v1` / `closing_v1` patient messages at doctor-chosen offsets; welcome/medication messages, caregiver messages, bookings, escalations and replies are always explicit | Brief §10; reminder content is non-clinical and template-exact. |
| D9 | Live sends refused to anything but `DEMO_PHONE` unless `ALLOW_LIVE_TO_REAL_RECIPIENTS=true` | Brief: never message arbitrary recipients; sample phone numbers are fictional. |
| D10 | Denied access to someone else's episode returns **404** (not 403) | Avoids revealing that an episode id exists. |
| D11 | Invitation and OTP SMS are sent on an explicit nurse/patient action without the doctor-approval gate | They carry no clinical content; the human click is the authorisation. |
| D12 | Urgent triage → review item immediately; patient safety message, care-team email and caregiver message are still individually approved | Brief §10: "External actions remain subject to approval." |
| D13 | TypeScript 6.0 (not 7.x native preview) | Stability of tooling under a deadline. |
| D14 | Medication-interaction finding gates the welcome message behind a doctor "medicine combination review" step (plan v1 → v2, trigger `tool_finding`) | Demo narrative step 3 without the agent inventing clinical advice. |
