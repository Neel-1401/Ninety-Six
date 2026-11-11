# Feature status

_Last reconciled from evidence: Wed 11 Nov 2026, 10:00 PM IST (branch `rebuild`)._

Columns are filled from evidence only:

- **Implemented**: the code exists.
- **Verified (sim)**: exercised by automated tests and/or the scripted HTTP journey against a running server, with simulated tools.
- **Verified (live)**: exercised against the real external provider, with a provider reference as evidence.

## Evidence

| Check | Result |
|---|---|
| `pytest -q` (backend) | **90 passed** in 73 s (11 Nov, 9:45 PM IST) |
| `npm run build` (frontend) | TypeScript + Vite production build passed |
| `backend/scripts/smoke_story.py` against `scripts/demo.ps1` on a fresh database | **All 30 steps passed**: the whole 3-minute demo story (below) over real HTTP, with the background worker running and openFDA live |
| `backend/scripts/verify_live.py` | openFDA `success/live`, labels found for lisinopril + spironolactone (+ furosemide in the smoke run). Twilio, SMTP and Calendar were **skipped: no credentials configured** |
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
| Agent | Extraction with source lines, "not provided" fields, ungrounded LLM items dropped, rule fallback | ✅ | ✅ `test_agent_integrity` | ⏳ LLM not configured (mock) |
| Agent | Transparent risk prioritisation | ✅ | ✅ smoke | n/a |
| Agent | 0–96 h plan generation + validation; invalid LLM plan rejected, fallback shown | ✅ | ✅ `test_agent_integrity` | ⏳ LLM |
| Agent | Versioned re-planning from a tool result (medication finding: v1 → v2) and from a patient reply (urgent: v3) with change summaries | ✅ | ✅ smoke + `test_api_flow` | ✅ v2 triggered by a **live** openFDA run |
| Agent | Check-in triage; rules can only raise the LLM's level; urgent review item appears before the LLM returns | ✅ | ✅ `test_llm_can_only_raise_triage`, `test_a07_*` | ⏳ LLM |
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
| Tools | Care-team email (SMTP) | ✅ | ✅ smoke (simulated, URGENT alert) | ⏳ needs SMTP credentials |
| Tools | LLM structured output (Anthropic / Gemini) | ✅ | ✅ mock path + fallbacks | ⏳ needs an API key |
| Hospital UI | Overview, patients, intake + extraction review, workspace + timeline, approvals, settings, demo drawer | ✅ | ◐ Codex browser walkthrough; approval-refresh retest pending | n/a |
| Patient UI | Sign-in, Today, guided check-in with drafts, instructions, messages, follow-up, receipt with warning signs + contacts | ✅ | ◐ Codex browser walkthrough; receipt retest pending | n/a |
| Ending | 96-hour summary → awaiting closure review → closure | ✅ | ✅ smoke + `test_96h_summary_and_closure` | n/a |
| Delivery | `setup.ps1`, `dev.ps1`, `demo.ps1`, README | ✅ | ◐ demo.ps1 used for the smoke run; clean-machine setup not tested | n/a |

## Open items before submission (12 Nov, 10:00 AM IST)

1. **Second live integration.** PS-07 asks for real actions through at least two tools. openFDA is verified live. Add Twilio (trial) or Gmail SMTP credentials to `backend/.env`, then run `scripts\verify_live.py` and the demo. See the README section "Going live".
2. Browser retest of approval refresh and the patient receipt (do it while rehearsing the video).
3. Mobile widths, keyboard/accessibility pass, clean-machine setup.
