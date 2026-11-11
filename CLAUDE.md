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

## 4. Architecture in one breath

React + Vite UI ⇄ FastAPI (REST + SSE) → **Agent core:** Parser → Planner → Executor ⇄ Approval gate, plus a Monitor (triage of replies + a 5-second clock tick) → Tools (Twilio / Google Calendar / openFDA / SMTP) · SQLite holds memory, plan and the full trace.

**Agent loop:** parse summary → write memory → plan timed steps (0–96 h) → run due steps → gate irreversible ones → patient reply is triaged against memory → re-plan (escalate / reply / caregiver) → 96-hour summary.

---

## 5. Stack and conventions

| Area | Choice / rule |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLModel, httpx, python-dotenv |
| DB | SQLite `backend/ninety_six.db`; JSON columns for `args`, `result`, `payload`, `detail` (always **reassign** dicts, never mutate in place) |
| Times | Store naive UTC from `clock.now()`; serialize with `iso()` (adds `Z`). **The UI shows 12-hour times (e.g. 8:00 PM)** and `T+24h` labels. |
| Tools | Subclass `tools/base.py::Tool` → set `name`, `irreversible`, `args_doc`; implement `live()`, `preview()`, `run()`; register in `tools/__init__.py`. A tool may return `followups` (step specs) to extend the plan. |
| Trace | Every meaningful action calls `events.log_event(session, kind, title, patient_id, detail)`; that also pushes SSE to the UI. |
| Memory | Write with `memory.remember()`, read with `memory.recall()` (reads are logged so the demo shows memory in use). |
| Concurrency | Agent work runs under `executor._lock` (RLock), which SQLite needs. |
| Frontend | React 18 + TypeScript + Vite, react-router, plain CSS (no Tailwind). Vite proxies `/api` → `localhost:8000`. One `EventSource` in `LiveProvider`; pages refetch on each event. |
| Secrets | Only in `backend/.env` (git-ignored). Never commit keys or `service-account.json`. |

---

## 6. Build status (as of Wed 11 Nov 2026, 1:45 PM)

### ✅ Written (backend core, not yet run or tested)
```
backend/app/config.py · clock.py · db.py · models.py · events.py · memory.py · seed.py
backend/app/llm/__init__.py
backend/app/agent/prompts.py · parser.py · planner.py · executor.py · monitor.py
backend/app/tools/base.py · messaging.py · calendar.py · drug_info.py · care_team.py · __init__.py
backend/requirements.txt · backend/.env.example
CLAUDE.md · README.md · docs/BLUEPRINT.md · docs/INSTRUCTIONS.md · .gitignore · LICENSE
```

### ⏳ To build next (in order)
1. `backend/app/serializers.py`, `routers/` (patients, approvals, simulate, system, stream), `main.py` (lifespan: init_db, bind bus, tick task; serve `frontend/dist`)
2. `tests/test_flow.py` (intake → approvals → +24 h → red flag → urgent escalation)
3. Frontend: layout (Sidebar, TopBar, ClockControl) → Dashboard → Intake → Patient Detail (Timeline96, PlanView, MessageThread, PatientSimulator, MemoryPanel, ActivityLog) → Approvals → Settings
4. Live keys: Gemini → Twilio WhatsApp sandbox (`DEMO_PHONE`) → Google Calendar service account → SMTP
5. `scripts/setup.ps1`, `scripts/dev.ps1`, README with screenshots, 6-slide deck, 3-min video

### Timeline to deadline
| When | Goal |
|---|---|
| Wed 11 Nov, 2:00 PM – 5:00 PM | Backend runs end to end; test passes |
| Wed 11 Nov, 5:00 PM – 10:00 PM | Frontend complete |
| Wed 11 Nov, 10:00 PM – 12:00 AM | Real integrations (Gemini, Twilio sandbox; Calendar if time) |
| Thu 12 Nov, 6:00 AM – 8:30 AM | README screenshots, deck, record video |
| Thu 12 Nov by 9:00 AM | Final push and submit (hard deadline 10:00 AM) |

### Cut list if behind
Drop SMTP → drop Google Calendar (Twilio + openFDA still meet "≥2 tools") → drop the Twilio inbound webhook (use the in-app Patient Simulator).

---

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
# backend
cd ninety-six\backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000

# frontend (new terminal)
cd ninety-six\frontend
npm install; npm run dev   # http://localhost:5173
```

Key env vars: `LLM_PROVIDER`, `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `DEMO_PHONE`, `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_CALENDAR_ID`, `SMTP_*`, `CARE_TEAM_EMAIL`, `OPENFDA_ENABLED`, `HOSPITAL_NAME`, `EMERGENCY_NUMBER=108`. With no keys at all, everything runs in simulated mode.

---

## 9. Rules for anyone (or any AI) working on this repo

- Don't change the decisions in §3 without asking Neel.
- The demo must never depend on a single external call succeeding: keep the fallbacks.
- Keep irreversible actions behind `run_step`'s approval gate; never call a tool's `run()` directly from a router.
- Prefer finishing the list in §6 over adding features.
- Windows commands only; 12-hour times in any UI or docs.
- Update §6 of this file whenever something gets built.
- Commit small and often (`feat:`, `fix:`, `docs:` prefixes); never commit `.env`, `*.db` or `service-account.json`.
- Before saying a task is done: run `pytest` in `backend/` and `npm run build` in `frontend/`.
