# Feature status

## Verification checkpoint — 11 Nov 2026

- Frontend: `npm.cmd run build` passed (TypeScript and Vite production build).
- Backend: ` .\.venv\Scripts\python.exe -m pytest -q` passed: **87 passed in 78.13s**. The HTTP milestone journey now waits for the asynchronous medication review before fetching the doctor's current plan, matching the activation safety gate.
- The matrix below predates the implementation commits and needs reconciliation against acceptance evidence. Its Planned cells are not an accurate inventory of the current code.
- Live providers, browser acceptance journeys, and clean-machine setup have not been verified in this checkpoint.

Four separate columns, updated by the lead from evidence only:

- **Planned** — in scope, not built yet.
- **Implemented** — code exists and the owning agent reports it works; not yet independently verified.
- **Verified (sim)** — exercised by automated tests and/or the evaluator against the running app with simulated tools.
- **Verified (live)** — exercised against the real external provider (sandbox/live API) with evidence.

The previous repository had no FEATURES.md; its CLAUDE.md listed backend modules as "written", but none of them had ever run. That code now lives in `backend/legacy/` as reference only.

_Last updated: 11 Nov 2026, foundation committed; implementation agents A–D in progress._

| Area | Feature | Planned | Implemented | Verified (sim) | Verified (live) |
|---|---|---|---|---|---|
| Foundation | Data model (26 tables), persisted simulated clock, after-commit SSE bus, error format | | ✅ | ✅ smoke (tables created, clock offset persisted, event id assigned) | n/a |
| Foundation | Alembic migrations | ✅ | | | n/a |
| Auth | Staff login with provisioned accounts | ✅ | | | n/a |
| Auth | Patient invitation + phone + one-time code, cooldown, attempt limits, rate limits | ✅ | | | |
| Auth | Demo-only role switching / visible demo codes | ✅ | | | n/a |
| Permissions | Role + episode-assignment checks on every endpoint | ✅ | | | n/a |
| Intake | Nurse intake with samples (heart failure primary, appendectomy, DKA) | ✅ | | | n/a |
| Agent | Extraction with sources, "not provided" fields, deterministic fallback | ✅ | | | |
| Agent | Transparent demo risk prioritisation | ✅ | | | n/a |
| Agent | 0–96 h plan generation + validation + rule fallback | ✅ | | | |
| Agent | Versioned replanning with change explanations | ✅ | | | n/a |
| Agent | Triage of check-ins (rules; LLM may only raise) | ✅ | | | |
| Agent | Memory facts with provenance + usage log | ✅ | | | n/a |
| Approvals | Payload-bound approval, edit invalidates, reject, routine authorisation scope + revoke | ✅ | | | n/a |
| Execution | Durable tasks, atomic claim, idempotency, bounded retry, unknown-outcome handling, restart recovery | ✅ | | | n/a |
| Execution | Fast-forward without reminder bursts or approval bypass | ✅ | | | n/a |
| Tools | Medication information (openFDA + local interaction rules) | ✅ | | | |
| Tools | Messaging (Twilio SMS, DEMO_PHONE redirect) | ✅ | | | |
| Tools | Calendar (Google Calendar event) | ✅ | | | |
| Tools | Care-team email (SMTP) | ✅ | | | |
| Tools | LLM structured output (Anthropic / Gemini) | ✅ | | | |
| Hospital UI | Overview, patients, intake + extraction review, workspace + timeline, approvals, settings | ✅ | | | n/a |
| Patient UI | Sign-in, Today, guided check-in with drafts, instructions, messages, follow-up | ✅ | | | n/a |
| Demo | Demo drawer: role switch, samples, patient responses, +6/+24 h, tool modes, reset | ✅ | | | n/a |
| Ending | 96-hour summary, awaiting closure review, closure | ✅ | | | n/a |
| Delivery | setup.ps1 / dev.ps1, README, acceptance report | ✅ | | | n/a |
