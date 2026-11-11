# INSTRUCTIONS.md — How to build and ship Ninety-Six

Follow the phases in order. Each phase has a **copy-paste prompt for Claude Code** and a **done check**. Times are IST, working back from the **Thu 12 Nov, 10:00 AM** deadline.

---

## Phase 0 — Create the GitHub repo (10 min)

**Repo name:** `ninety-six`
**Description:** `AI agent for the first 96 hours after hospital discharge — plans follow-up, messages patients, books visits, escalates red flags with human approval. TheAgentX PS-07.`
**Topics:** `ai-agent` `healthcare` `fastapi` `react` `llm` `twilio` `hackathon` `human-in-the-loop`
**Visibility:** Public (judges need access)

### Option A: GitHub CLI (fastest)
```powershell
cd ninety-six
git init
git add .
git commit -m "chore: project scaffold, context and blueprint"
git branch -M main
gh repo create ninety-six --public --source . --push --description "AI agent for the first 96 hours after hospital discharge. TheAgentX PS-07."
```

### Option B: Website
1. github.com → **New repository** → name `ninety-six` → Public → **don't** add a README, .gitignore or license (they're already here) → Create.
2. Then:
```powershell
cd ninety-six
git init
git add .
git commit -m "chore: project scaffold, context and blueprint"
git branch -M main
git remote add origin https://github.com/<your-username>/ninety-six.git
git push -u origin main
```

**Done check:** the repo page shows README.md rendered, and `.env` is NOT in the file list.

---

## Phase 1 — Finish the backend (Wed, 2:00 PM – 5:00 PM)

**Prompt for Claude Code:**
> Read CLAUDE.md and docs/BLUEPRINT.md. Build the remaining backend: `app/serializers.py`, `app/routers/` (patients, approvals, simulate, system, stream) exactly per BLUEPRINT §9, and `app/main.py` (lifespan: init_db, bind the event bus to the loop, background tick every TICK_SECONDS calling `run_due_steps` via `asyncio.to_thread`; CORS for FRONTEND_ORIGIN; serve `frontend/dist` if it exists). Intake must run `executor.kickoff` as a BackgroundTask. Then write `tests/test_flow.py` (temp SQLite DB, LLM_PROVIDER=mock, OPENFDA_ENABLED=false): intake the heart-failure sample → assert pending approvals exist → approve all → advance clock 24 h → assert a check-in message was sent → simulate reply "I get breathless lying flat" → assert an URGENT approval exists. Fix any bugs in the existing agent code you find. Run pytest until it passes. Update CLAUDE.md §6.

**Done check:** `pytest` passes; `uvicorn app.main:app --reload` starts; http://localhost:8000/docs lists all endpoints.

Commit: `git add . ; git commit -m "feat: backend API, SSE and e2e test" ; git push`

---

## Phase 2 — Build the frontend (Wed, 5:00 PM – 10:00 PM)

**Prompt for Claude Code:**
> Read CLAUDE.md and docs/BLUEPRINT.md §8. Create `frontend/` with React 18 + TypeScript + Vite + react-router, plain CSS (clean clinical look: dark navy sidebar, teal accent, white cards). Vite proxy `/api` → `http://localhost:8000`. Build `lib/` (api.ts, types.ts, live.tsx with one EventSource and a version counter, format.ts with 12-hour times and T+h labels), all 5 pages and every component listed in §8. The Patient Detail page is the demo centrepiece: Timeline96 across the top, then Plan, Messages + PatientSimulator, Medications, Appointments, Memory, Activity log. Approvals page: urgent first, editable message body, approve/reject with a note. TopBar shows the sim clock with +6 h / +24 h buttons. Run `npm run build` until it passes with no type errors.

**Done check:** run both servers, load the sample, and click through the whole demo flow in README without errors in the browser console.

Commit: `git commit -am "feat: frontend dashboard, patient view, approvals" ; git push`

---

## Phase 3 — Connect real tools (Wed, 10:00 PM – 12:00 AM)

Do these in order and stop when time runs out; each one is optional because of the simulated fallbacks.

| # | Tool | Steps | Env vars |
|---|---|---|---|
| 1 | **Gemini** | aistudio.google.com → Get API key | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY` |
| 2 | **Twilio WhatsApp sandbox** | twilio.com free trial → Messaging → Try WhatsApp → send the join code from your phone | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM=whatsapp:+14155238886`, `DEMO_PHONE=whatsapp:+91XXXXXXXXXX` |
| 3 | **Google Calendar** | Cloud Console → enable Calendar API → service account → download JSON as `backend/service-account.json` → share your calendar with the service-account email ("Make changes to events") | `GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json`, `GOOGLE_CALENDAR_ID=<your gmail>` ; `pip install google-api-python-client google-auth` |
| 4 | **SMTP** | Gmail → App passwords | `SMTP_HOST=smtp.gmail.com`, `SMTP_USER`, `SMTP_PASSWORD`, `CARE_TEAM_EMAIL` |

**Done check:** the Settings page shows LIVE for each connected tool; the welcome SMS arrives on your phone.

---

## Phase 4 — Ship (Thu, 6:00 AM – 9:00 AM)

1. **Screenshots:** Dashboard, Patient Detail, Approvals → save to `docs/screenshots/` and embed them in README.
2. **Deck (6 slides):** Problem & user · Why an agent · How it works (architecture) · Human-in-the-loop & safety · Demo highlights · Impact & next steps.
3. **Video (max 3:00):** follow the demo script in `docs/BLUEPRINT.md` §11. Reset the demo first (Settings → Reset). Record two takes with OBS or the Xbox Game Bar (Win + G).
4. **Final push:** `git add . ; git commit -m "docs: screenshots, deck and video links" ; git push`
5. **Submit by 9:00 AM:** repo link + video link + deck link.

---

## Golden rules
- Working demo > more features. Use the cut list in CLAUDE.md §6 if you're behind.
- Never commit `.env`, `ninety_six.db` or `service-account.json`.
- Click Reset before every demo recording.
