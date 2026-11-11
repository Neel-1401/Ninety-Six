# Ninety-Six 🏥

**An AI agent for the first 96 hours after hospital discharge.**

Ninety-Six reads a discharge summary, plans the patient's follow-up, books the visit, sends check-ins by SMS/WhatsApp, watches the replies for warning signs, and asks a clinician before doing anything irreversible.

> Built for **TheAgentX Hackathon — PS-07 Open Track** by Neel (RVCE, Bengaluru).

---

## Why an agent, not a chatbot?
The job lasts four days. It involves waiting, scheduled check-ins, reacting to replies, and chaining several tools (drug lookup → messaging → calendar → escalation) without anyone supervising it. A chatbot only answers when asked; Ninety-Six keeps working on its own.

**Who it's for:** discharge nurses and care coordinators who hand off 20–40 patients a day and can't personally follow up with each one.

## What it does
| Agent ability | How |
|---|---|
| Plans its own steps | Turns the discharge summary into a timed plan across 0–96 h, and re-plans when a reply or a tool result changes things |
| Real tool use | Twilio (SMS/WhatsApp), Google Calendar, openFDA, SMTP email |
| Human-in-the-loop | Every message, booking and escalation waits for clinician approval; one sign-off covers routine check-ins |
| Memory | Diagnosis, red flags, medicine alerts and reported symptoms are stored and re-read at every step |
| Safe by default | If the AI model or an API fails, it falls back to rules or simulated mode, and rules can only raise a triage level, never lower it |

## Architecture
```
React + Vite UI  ⇄  FastAPI (REST + SSE)
                     └─ Agent: Parser → Planner → Executor ⇄ Approval gate
                               Monitor (reply triage + clock tick)
                     └─ Tools: Twilio · Google Calendar · openFDA · SMTP
                     └─ SQLite: memory, plans, approvals, full trace
```
Full design: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md)

## Quick start (Windows PowerShell)
```powershell
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # works with no keys (simulated mode)
uvicorn app.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev                 # open http://localhost:5173
```

## Demo flow
1. **New Discharge** → load the *Heart failure, 68 M* sample → the agent parses it and plans its steps
2. The medication review flags an interaction, and the agent adds a pharmacist-review step
3. **Approvals** → approve the welcome SMS, the follow-up booking and the check-in schedule
4. Fast-forward **+24 h** → the check-in sends itself; reply as the patient
5. Reply "breathless lying flat" → **RED FLAG** → urgent care-team alert waiting for approval
6. Fast-forward to 96 h → a summary for the doctor

## Tech stack
Python · FastAPI · SQLModel · SQLite · httpx · React · TypeScript · Vite · Gemini / Claude · Twilio · Google Calendar API · openFDA

## Disclaimer
This is a hackathon prototype, not a medical device. It does not diagnose or change medication; it routes concerns to clinicians. All patient data in this repo is fictional.

## License
MIT
