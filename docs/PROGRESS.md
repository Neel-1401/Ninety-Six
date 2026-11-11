# Recovery progress — 11 Nov 2026

## Current state

Continued the existing `rebuild` branch at e970d9f; one worktree was registered. Preserved all existing changes. No reset, clean, branch switch, push or deployment. No AGENTS.md or project-local UI SKILL.md was found. CLAUDE.md, BRIEF, CONTRACTS, DECISIONS, FEATURES and setup scripts were inspected; Claude's saved project memory confirms the shared-tree implementation. The unrelated saved plan was not used.

The modular backend, hospital UI and patient UI are implemented. The simulated HTTP journey now covers intake, review, activation with bounded routine scope, a welcome message, patient invitation and OTP, guided urgent check-in, triage, a revised plan, doctor approval and one response execution. Nurse and doctor message results agree; patient messages contain the same response. Live integrations are implemented adapters, not verified live integrations.

## Evidence and checks

- Baseline: 87 backend tests passed; TypeScript/Vite production build passed.
- Extended HTTP milestone: passed before evaluator review; covers duplicate approval, one execution and role views.
- Full suite after execution notice changes: 87 passed in 85.84 seconds. Frontend build passed.
- Browser (native Comet, isolated localhost:8010): nurse password sign-in, heart-failure sample intake, extraction with source lines, submission, plan v1 to v2 medication finding, doctor sign-in, confirmation of 24 facts, bounded routine permission, activation, medication review resolution, welcome approval and execution, +24-hour advance, patient phone-code authentication via simulated outbox, six guided questions and optional concern, accurate receipt, urgent hospital concern and plan v3, urgent response approval. Only simulated tools were enabled. Existing provider credentials were not read or used.
- New empty database startup applied Alembic migration 0001; subsequent server restart preserved the episode and +24-hour offset.
- Direct read of the isolated database confirmed welcome and urgent response succeeded, with exactly one simulated urgent execution.
- Browser exposed a stale approval card: tool completion omitted the approvals query scope. Fixed notice scopes to include approvals and check-ins; final browser retest pending.
- Patient receipt now includes doctor-reviewed warning signs, hospital/emergency contacts and the submitted optional concern. Priority wording changed from 'Routine priority' to 'Normal priority'. Production build passed; browser retest pending.
- Independent evaluator performed a source review, without modifying files. Two findings reproduced in regression tests: rejection between dispatch and execution preparation still sent; executed replies never set the check-in response link. Fixes added; targeted and full regression results pending.

## Remaining tasks, in priority order

1. Finish regression tests for atomic execution preparation and response receipt linkage; run full pytest and frontend build.
2. Retest browser approval refresh, final patient response receipt/message, nurse view, refresh persistence and SSE reconnection after server restart.
3. Have evaluator review the fixes and reconcile FEATURES.md against the evidence.
4. Wider acceptance: mobile widths, keyboard/accessibility, all failure/reconciliation UI paths, clean-machine setup.
5. Live provider verification with an explicitly configured test recipient. Simulation does not satisfy the hackathon requirement for two real integrations.

## Known failures and limits

No claim that the complete acceptance matrix is verified. Approval refresh and receipt linkage were observed/reproduced failures; retest status above governs readiness. Live Twilio, Calendar, SMTP, openFDA and LLM calls have not been verified in this recovery. Browser work was interrupted twice by physical Escape; user subsequently authorised resumption. The required animate and impeccable skill files were not found locally. A cached UI/UX design package and the installed computer-use skill were read; this is not a claim to have applied the missing skills.

## Exact Windows commands

From the repository root, install dependencies only if needed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Start the isolated demo with external tools forced to simulation, using a separate persisted database and serving the built UI and API together:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1
```

Open http://localhost:8010. Demo staff: `nurse.asha` and `dr.mehta`, password `demo1234`. Patient: use the registered fictional number, request a code, then read the simulated outbox. Stop this terminal with Ctrl+C. Relaunching preserves `backend/recovery_demo.db`; it does not reset other databases.

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/test_api_flow.py tests/test_execution_fixes.py -q
Set-Location ..\frontend
npm.cmd run build
```

The normal development launcher is `powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1` (API :8000, Vite :5173); it uses the existing environment configuration. Prefer `demo.ps1` for the simulated verification journey.
