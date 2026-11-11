# Feature status

_Last reconciled from evidence: Thu 12 Nov 2026, 1:15 AM IST (hybrid LLM routing)._

Columns are filled from evidence only:

- **Implemented**: the code exists.
- **Verified (sim)**: exercised by automated tests and/or the scripted HTTP journey against a running server, with simulated tools.
- **Verified (live)**: exercised against the real external provider, with a provider reference as evidence.

## Evidence

| Check | Result |
|---|---|
| `pytest -q` (backend), 12 Nov 1:15 AM | **105 passed** with `LLM_PROVIDER=mock` (14 new hybrid-routing tests with a fake LLM: red-flag downgrade blocked, invalid JSON / timeout fall back, unknown tool dropped, simple check-ins never call the LLM, drafts still need approval, unsafe draft rejected, translation keeps the English original, cache) |
| `smoke_story.py`, 12 Nov 1:10 AM, fresh DBs | **All steps passed twice**: `LLM_PROVIDER=mock` (trace: "Rules used" everywhere) and **Gemini live** `gemini-3.1-flash-lite` (trace: AI used for plan additions 4238 ms, red-flag reply draft 3385 ms, 96-hour note 3837 ms; rules for parsing, the simple check-in and the red-flag level). The AI-drafted URGENT reply kept both phone numbers and was sent only after doctor approval. Separate live probe: Kannada translation 4069 ms, passed the vetting |
| `pytest -q` (backend), 11 Nov | **90 passed** (11 Nov, 10:30 PM IST, after the live credentials were added; tests force every provider to simulated) |
| `npm run build` (frontend) | TypeScript + Vite production build passed |
| `backend/scripts/smoke_story.py` on a fresh database | **All 30 steps passed** against `demo.ps1` (openFDA live). **All 31 steps passed** against a server reading `backend/.env` (openFDA + Gmail SMTP live): the URGENT care-team alert executed in LIVE mode, accepted by Gmail (11 Nov, 10:35 PM IST) |
| `backend/scripts/verify_live.py` | openFDA `success/live` (labels found). **Gmail SMTP `success/live`**: test email accepted by smtp.gmail.com:587 (11 Nov, 10:23 PM IST). Twilio and Calendar skipped: not configured |
| `GET /api/settings` (Settings page data) | `medication_info: live`, `care_team_email: live`; messaging, calendar and LLM `simulated` |
| Browser walkthrough (Comet, by Codex, 11 Nov) | Nurse intake → doctor activation → welcome approval → +24 h → patient OTP sign-in → guided check-in → urgent concern → plan v3 → urgent approval. A stale approval card was found and fixed (notice scopes). **A browser retest after that fix is still pending.** |

## Matrix

| Area | Feature | Implemented | Verified (sim) | Verified (live) |
|---|---|---|---|---|
| Foundation | Data model, persisted simulated clock, after-commit SSE bus, error format | ✅ | ✅ tests + smoke | n/a |
| Foundation | Alembic migrations (0001 on an empty DB, restart keeps data and clock offset) | ✅ | ✅ smoke (fresh DB) + Codex restart check | n/a |
| Auth | Staff login with provisioned accounts, cookies, CSRF header, logout revokes | ✅ | ✅ `test_auth` | n/a |
| Auth | Patient invitation + phone + one-time code, cooldown, attempt limit, rate limit, newest code only; a phone change revokes the old number | ✅ | ✅ `test_auth`, smoke | ⏳ OTP SMS needs Twilio |
| Auth | Demo mode explicit only; demo routes 404 and OTP bodies redacted in production | ✅ | ✅ `test_auth` | n/a |
| Permissions | Role + episode-assignment checks; other patient's episode returns 404 | ✅ | ✅ `test_permissions` | n/a |
| Intake | Nurse intake with samples (heart failure primary, appendectomy, DKA/Kannada) | ✅ | ✅ smoke (17 facts with sources) | n/a |
| Agent | Extraction with source lines, "not provided" fields; regex first, AI only when the regex result is weak (merged, ungrounded items dropped) | ✅ | ✅ `test_agent_integrity` | ✅ live run used rules (strong summary), as routed |
| Agent | Transparent risk prioritisation | ✅ | ✅ smoke | n/a |
| Agent | 0–96 h rule plan + AI condition-specific additions (each validated; unknown tool / out-of-window / bad args dropped) | ✅ | ✅ `test_agent_integrity`, `test_llm_hybrid` | ✅ Gemini added 2 steps (T+24 h weight check-in, T+72 h electrolytes review) |
| Agent | Versioned re-planning from a tool result (medication finding: v1 → v2) and from a patient reply (urgent: v3) with change summaries | ✅ | ✅ smoke + `test_api_flow` | ✅ v2 triggered by a **live** openFDA run |
| Agent | Check-in triage: rules red flag is final (AI not called); AI reads long/unrecognised free text; final = max(rules, AI) | ✅ | ✅ `test_llm_hybrid`, `test_a07_*` | ✅ live probe (concern) + smoke (red flag kept by rules) |
| Agent | AI reply drafts (concern / red flag) and translation for non-English patients; vetted (numbers kept, no doses, no triage wording); always approved | ✅ | ✅ `test_llm_hybrid` | ✅ urgent draft in live smoke; Kannada translation probe |
| Agent | 96-hour clinical note by AI, template note as fallback | ✅ | ✅ smoke (mock: template) | ✅ live smoke |
| Agent | Router + activity trace ("AI used / Rules used / AI failed -> rules"), 8 s timeout, schema, 1 retry, cache, Settings kill switch | ✅ | ✅ `test_llm_hybrid` | ✅ live smoke trace |
| Agent | Memory facts with provenance + usage log (78 usage rows in the smoke run) | ✅ | ✅ smoke | n/a |
| Approvals | Payload-bound approval (recipient, mode, episode); edit invalidates; reject; duplicate click is a no-op | ✅ | ✅ `test_execution_flow`, `test_a05_*`, smoke (edit-approve) | n/a |
| Approvals | Routine authorisation: listed reminder offsets only, fails closed, revocable | ✅ | ✅ `test_routine_scope_*`, `test_a06_*`, `test_revoked_routine_*` | n/a |
| Approvals | Rejection/revocation after dispatch but before execution prep prevents the send (`BEGIN IMMEDIATE`) | ✅ | ✅ `test_decision_between_dispatch_and_prepare_prevents_send` | n/a |
| Execution | Durable tasks, atomic claim, idempotency, bounded retry, unknown outcome with no blind retry, restart recovery, reconciliation | ✅ | ✅ `test_execution_flow`, `test_execution_fixes` | n/a |
| Execution | Fast-forward without reminder bursts or approval bypass | ✅ | ✅ `test_fast_forward_*`, `test_late_registration_*` | n/a |
| Execution | Demo fault switch (unknown/failure), disabled outside demo mode | ✅ | ✅ `test_a10_*` | n/a |
| Tools | Medication information (openFDA labels + local interaction rules IR-01/IR-04) | ✅ | ✅ | ✅ **openFDA live**, 11 Nov 2026 (`verify_live.py`, smoke run) |
| Tools | Messaging (Twilio SMS/WhatsApp, DEMO_PHONE redirect; accepted ≠ delivered) | ✅ | ✅ `test_policy_adapters` | ⏳ needs Twilio credentials |
| Tools | Calendar (Google Calendar event, deterministic id, no double-booking) | ✅ | ✅ smoke (simulated) | ⏳ needs a service account |
| Tools | Care-team email (SMTP) | ✅ | ✅ smoke (simulated, URGENT alert) | ✅ **Gmail SMTP live**, 11 Nov 2026: `verify_live.py` plus the URGENT alert in the smoke run (SMTP accepted; inbox arrival not yet confirmed by Neel) |
| Tools | LLM structured output (Gemini / Anthropic) | ✅ | ✅ mock path + fallbacks | ✅ **Gemini live** (`gemini-3.1-flash-lite`), 12 Nov 2026; Anthropic not tested (no key) |
| Hospital UI | Overview, patients, intake + extraction review, workspace + timeline, approvals, settings, demo drawer | ✅ | ◐ Codex browser walkthrough; approval-refresh retest pending | n/a |
| Patient UI | Sign-in, Today, guided check-in with drafts, instructions, messages, follow-up, receipt with warning signs + contacts | ✅ | ◐ Codex browser walkthrough; receipt retest pending | n/a |
| Ending | 96-hour summary → awaiting closure review → closure | ✅ | ✅ smoke + `test_96h_summary_and_closure` | n/a |
| Delivery | `setup.ps1`, `dev.ps1`, `demo.ps1`, README | ✅ | ◐ demo.ps1 used for the smoke run; clean-machine setup not tested | n/a |

## Open items before submission (12 Nov, 10:00 AM IST)

1. ~~Second live integration~~: done. openFDA and Gmail SMTP are live (PS-07 asks for at least two real tools).
2. Browser retest of (a) the approval card refreshing after a send and (b) the patient receipt screen. The app was launched for manual testing; results not yet reported. Do this while rehearsing the video.
3. Mobile widths, keyboard/accessibility pass, clean-machine setup.
