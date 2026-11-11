# Ninety-Six 🏥

**An AI agent for the first 96 hours after hospital discharge.**

Ninety-Six reads a discharge summary and plans the patient's follow-up across 0–96 hours. It checks the medicines, books the visits, sends check-ins and watches the replies for warning signs. It asks a doctor before anything irreversible: a message, a booking or a care-team alert.

> Built for **TheAgentX Hackathon, PS-07 Open Track** by Neel (RVCE, Bengaluru). Prototype only; all patient data is fictional.

---

## Who it's for
**Discharge nurses and care coordinators** at mid-size Indian hospitals. They hand off 20–40 patients a day and can't personally follow up with each one. **Patients and caregivers** are reached by SMS/WhatsApp and a simple patient web app.

## Why an agent, not a chatbot?
The job lasts four days. It means waiting, sending scheduled check-ins, reacting to replies, and chaining tools (medicine lookup → re-plan → message → calendar → escalation) with nobody supervising. A chatbot only answers when asked. Ninety-Six keeps working on its own and stops for a human only where it must.

## What makes it an agent
| PS-07 requirement | How Ninety-Six does it |
|---|---|
| **Plans its own steps** | Turns the discharge summary into a versioned, timed plan (0–96 h). It **re-plans** when a tool result or a patient reply changes things, and records a change summary each time (v1 → v2 after the openFDA review, v3 after a red-flag reply). |
| **Real tool use** | openFDA drug labels and Gmail SMTP care-team email (**both live**), plus Twilio SMS/WhatsApp and Google Calendar. Every adapter has a simulated fallback and shows its mode (LIVE / SIMULATED) on every card. |
| **Human confirmation before anything irreversible** | Every message, booking and escalation is a payload-bound approval: editing the payload, recipient or mode invalidates it. One "routine check-ins" sign-off covers only template reminders at doctor-chosen times. Red flags are approved one by one and marked **URGENT**. |
| **Memory** | Confirmed facts (diagnosis, medicines, red flags, reported symptoms) carry provenance. The agent re-reads them at every step and logs each use. |
| **Safe by default** | The LLM only *proposes*: deterministic code validates plans and authorises actions. Rules can only **raise** a triage level, never lower it. It is not diagnostic and never changes doses. |

## Architecture
```
React 19 + Vite + Tailwind UI (hospital app + patient app)
        ⇅  REST + Server-Sent Events
FastAPI ── api/ + auth/ (roles, episode assignment, OTP, CSRF)
        └─ services/agent_ops ── agent/  extraction · risk · planning+validation · triage · replanning · memory
                                 policy/ approval rules (payload hash, routine scope)
                                 execution/ durable task worker (atomic claim, idempotency, retry, restart recovery)
                                 tools/  openFDA · Twilio · Google Calendar · SMTP · LLM (Claude / Gemini / mock)
        └─ SQLAlchemy 2 + Alembic + SQLite: episodes, plans, approvals, executions, full audit trail
```
More detail: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md) · contracts: [`docs/CONTRACTS.md`](docs/CONTRACTS.md) · decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md) · verified status: [`FEATURES.md`](FEATURES.md)

## Quick start (Windows PowerShell)
Needs Python 3.11+ and Node 20+.
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1   # venv, packages, backend\.env, migrations, npm ci
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1    # builds the UI and serves everything at http://localhost:8010
```
`demo.ps1` runs fully offline-safe: messaging, calendar, email and the LLM are simulated, and openFDA is queried live (add `-Offline` to turn that off). For development with hot reload, use `scripts\dev.ps1` (API on :8000, UI on :5173); it reads `backend\.env`.

**Sign in:** staff `nurse.asha` / `dr.mehta`, password `demo1234`. **Patient:** enter the sample patient's phone, request a code, then read the code from the demo drawer's *simulated outbox*.

## Demo story (3 minutes)
1. **Nurse → New discharge →** load *Heart failure, 68 M*. The agent extracts 17 facts with source lines, scores risk and drafts a plan.
2. The live **openFDA** review flags **lisinopril + spironolactone** (potassium) and **furosemide + lisinopril** (blood pressure). The agent re-plans and adds a doctor medicine-review step (plan v2).
3. **Doctor →** confirms the facts, activates the plan with a bounded routine check-in scope, resolves the medicine review, **edits then approves** the welcome SMS, and approves both calendar bookings.
4. **+24 h →** the check-in reminder sends itself. The patient replies "All good" → triage OK, no review needed.
5. **+24 h →** the patient reports breathlessness lying flat → **RED FLAG** (memory recalled), plan v3, and three **URGENT** approvals: patient safety message, care-team email, caregiver message.
6. **Fast-forward to 96 h →** a summary for the doctor → close the episode.

The same story runs headless as a smoke test (31 steps): start `demo.ps1`, then in another terminal `cd backend; .\.venv\Scripts\python.exe scripts\smoke_story.py http://localhost:8010`.

## Connect real services
Ninety-Six currently runs **two live tools: openFDA** (drug labels, public, no key) **and Gmail SMTP** (the URGENT care-team alert lands in a real inbox). Twilio SMS, Google Calendar and the LLM are built and tested, but run on their simulated fallback until keys are added. Every card in the UI shows LIVE or SIMULATED.

Credentials go in `backend\.env` only (git-ignored; `scripts\setup.ps1` creates it from `.env.example`). `scripts\dev.ps1` reads it; `scripts\demo.ps1` always keeps messaging, calendar, email and the LLM simulated.

| Integration | `.env` keys | Status |
|---|---|---|
| openFDA | `OPENFDA_ENABLED=true` | **Live** (public, no key) |
| Care-team email (Gmail) | `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASSWORD` (a [Gmail app password](https://myaccount.google.com/apppasswords), needs 2-Step Verification), `CARE_TEAM_EMAIL` | **Live** |
| Twilio SMS | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `DEMO_PHONE` | Simulated. A trial works; `DEMO_PHONE` must be a number verified in Twilio, and every patient/caregiver message is redirected to it. |
| Google Calendar | `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_CALENDAR_ID` | Simulated. Share the calendar with the service-account email. |
| LLM | `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`, or `gemini` + `GEMINI_API_KEY` | Rules (mock). Every call has a rule-based fallback. |

Check each configured integration once (it sends one test email or SMS to your own test recipient and prints the provider reference):
```powershell
cd backend; .\.venv\Scripts\python.exe scripts\verify_live.py
```
The Settings page (`/api/settings`) shows each tool's mode.

## Tests
```powershell
cd backend; .\.venv\Scripts\python.exe -m pytest -q      # 90 tests
cd ..\frontend; npm.cmd run build
```

## Tech stack
Python · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · SQLite · httpx · React 19 · TypeScript · Vite · Tailwind v4 · TanStack Query · Claude / Gemini · openFDA · Twilio · Google Calendar API · SMTP

## Disclaimer
This is a hackathon prototype, not a medical device. It does not diagnose or change medication; it routes concerns to clinicians. All patient data in this repo is fictional.

## License
MIT
