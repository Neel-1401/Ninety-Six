# CLAUDE.md — Ninety-Six (project context)

> Read this first. It is the single source of truth for what this project is, what's decided, what's built, and how to work on it.
> Full design detail: `docs/BLUEPRINT.md` · Step-by-step build instructions: `docs/INSTRUCTIONS.md`

---

## 1. Snapshot

| | |
|---|---|
| **Project** | Ninety-Six (formerly CareBridge) |
| **One-liner** | An AI agent that runs the first 96 hours after hospital discharge: it plans the follow-up, messages the patient, books the visit, watches for warning signs, and asks a clinician before anything irreversible. |
| **Event** | TheAgentX Hackathon, **PS-07 Open Track** ("build any agent") |
| **Builder** | Neel, 2nd-year CSE (AI & ML), RVCE Bengaluru (solo) |
| **Idea round** | Submitted (30 Oct 2026) |
| **Final deadline** | **Thu 12 Nov 2026, 10:00 AM IST**: GitHub repo + README, demo video (max 3 min), 6-slide deck |
| **Build scope** | Light-to-medium agent. A reliable demo beats extra features. |
| **Dev machine** | Windows. Give PowerShell commands only. |

---

## 2. Hackathon rules we must satisfy (PS-07)

1. The agent **plans its own steps**.
2. It takes **real actions through at least two tools/APIs**.
3. It **asks for human confirmation before anything irreversible**.
4. The deck says **who the user is** and **why this needs an agent rather than a chatbot**.

**Judging:** problem fit & impact 25% · agent design (planning, tool use, memory) 25% · working demo & reliability 20% · innovation 15% · presentation 15%.

---

## 3. Product decisions (locked)

- **Primary user:** discharge nurse / care coordinator at a mid-size Indian hospital. **Secondary:** patient and caregiver, reached by SMS/WhatsApp.
- **Why an agent:** the job spans 96 hours of waiting, scheduled check-ins, reacting to replies, and chaining tools. Nobody has to supervise it.
- **Name meaning:** the first 96 hours after discharge, when most early problems show up.
- **Tools:** Twilio (SMS/WhatsApp sandbox), Google Calendar, openFDA, SMTP email. Every tool has a **simulated fallback**.
- **Irreversible = gated:** sending any message, booking any appointment, alerting the care team.
- **Batch sign-off:** one "Activate routine check-in schedule" approval pre-approves *templated* check-ins only. Agent-drafted replies, caregiver messages and escalations are always approved one by one. Red flags are marked **URGENT**.
- **Triage safety rule:** the rules engine can only *raise* the LLM's triage level, never lower it.
- **LLM:** Gemini 2.5 Flash (free tier) or Claude, selected by env var. **Every LLM call has a rule-based fallback.**
- **Demo time:** a simulated clock with fast-forward (+6 h / +24 h) compresses 96 hours into 3 minutes.
- **Not a diagnostic tool.** It never changes doses; it routes to clinicians. All sample data is fictional.

---

## 4. Architecture (rebuild, 11 Nov 2026 — supersedes the original scaffold)

React + Vite + Tailwind UI ⇄ FastAPI (REST + SSE) → modular backend: `api/` routes + `auth/` permission checks (Agent A) → `services/agent_ops.py` → `agent/` extraction, risk, planning, triage, replanning, memory + `policy/` approval rules + `execution/` durable task worker + `tools/` adapters (Agent B) → SQLAlchemy 2 / Alembic / SQLite. The LLM only *proposes*; deterministic code validates and authorises. Full contracts: `docs/CONTRACTS.md`; decisions: `docs/DECISIONS.md`; original brief: `docs/BRIEF.md`; status: `FEATURES.md`.

Roles: **doctor** (reviews extraction, activates plan, approves/edits/rejects external actions, revokes routine authorisation, closes episodes), **nurse** (intake, corrections, team, follow-up, review items, notes), **patient** (invitation + phone + one-time code; instructions, guided check-ins, messages, follow-up).

---

## 5. Stack and conventions

| Area | Choice / rule |
|---|---|
| Backend | Python 3.11+ (dev box: 3.14), FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, httpx |
| DB | SQLite `backend/ninety_six.db` (WAL). JSON columns: always **reassign**, never mutate in place. Keep transactions short; never call a provider inside a transaction. |
| Times | Aware UTC everywhere via `app.clock.clock.now()` (simulated, persisted offset). Security timers use `clock.real_now()`. API emits ISO `...Z`; UI shows Asia/Kolkata 12-hour times and `T+24 h` labels. |
| Audit/SSE | `app.events.record_event(session, kind, title, episode_id=..., scopes=[...])` — persisted in the transaction, published after commit. SSE is a refetch hint only. |
| Errors | `{"error": {"code", "message", "details"}}` via `app.errors`. Other patient's / unassigned episode → 404; wrong role → 403. |
| Frontend | React 19 + TypeScript 6 + Vite 8 + Tailwind v4 + React Router 7 + TanStack Query 5 + Framer Motion (reduced-motion aware) + lucide icons. Shared kit in `frontend/src/ui`, API/types/SSE/auth/format in `frontend/src/lib` (lead-owned). |
| Secrets | Only in `backend/.env` (git-ignored). Never commit keys or `service-account.json`. |

---

## 6. Build status

See `FEATURES.md` (Planned / Implemented / Verified sim / Verified live). Do not mark anything verified without evidence.

## 7. Demo story (3 minutes)

1. Load the sample **"Heart failure, 68 M"** → watch the plan appear, memory written.
2. The medication review flags **lisinopril + spironolactone** → the agent adds a pharmacist-review step (re-plan from a tool result).
3. Approve the welcome SMS (edit it first), the booking and the check-in schedule → the message arrives on the phone and the event appears in the calendar.
4. +24 h → the check-in sends itself → reply "All good" → auto-acknowledgement.
5. +24 h → reply "breathless lying flat" → **RED FLAG** (memory recalled) → URGENT approval → care-team email.
6. Fast-forward to 96 h → summary for the doctor.

Sample patients in `seed.py`: heart failure (high risk), appendectomy (low), diabetes/DKA (medium, Kannada).

---

## 8. Run (Windows PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1   # venv, pip, .env, migrations, npm ci
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1     # backend :8000 + frontend :5173
```

Tests: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` and `cd frontend; npm run build`.
With no keys at all, everything runs in simulated mode (`APP_MODE=demo`). See `backend/.env.example`.

---

## 9. Rules for anyone (or any AI) working on this repo

- Don't change the decisions in §3 without asking Neel.
- The demo must never depend on a single external call succeeding: keep the fallbacks.
- Keep external actions behind the approval policy + executor pre-flight; routers never call tool adapters directly (only OTP/invitation SMS, see CONTRACTS §11).
- Prefer finishing the list in §6 over adding features.
- Windows commands only; 12-hour times in any UI or docs.
- Update `FEATURES.md` whenever something gets built or verified.
- Commit small and often (`feat:`, `fix:`, `docs:` prefixes); never commit `.env`, `*.db` or `service-account.json`.
- Before saying a task is done: run `pytest` in `backend/` and `npm run build` in `frontend/`.
