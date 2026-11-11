# Original implementation brief (verbatim from the project owner, 11 Nov 2026)

Part 1 is the product/implementation brief. Part 2 is the team-process brief (including the evaluator
instructions). Both are the source of truth for acceptance.

---

# PART 1 — Ninety-Six — Full App Implementation Plan

Build Ninety-Six: a hackathon prototype for coordinating the first 96 hours after hospital discharge.

The app serves three roles:
1. Doctors review plans and authorise actions.
2. Nurses prepare discharges and coordinate follow-up.
3. Patients view instructions and submit guided check-ins.

Use fictional patient data only. This is a coordination prototype, not a diagnostic system.

## 1. Start by inspecting the repository

Repository: https://github.com/Neel-1401/Ninety-Six

Inspect the existing code, dependencies and documentation before making changes.

The previous FEATURES.md contains unverified "code written" statuses. Do not assume those features exist or work.

Report:
- What is implemented and runnable.
- What is partial or mocked.
- What is missing.
- What existing code can be reused.

Then implement the app in working increments. Preserve useful existing code. Do not replace the project wholesale without a concrete reason.

Maintain FEATURES.md with separate statuses:
- Planned
- Implemented
- Verified in simulation
- Verified with a live integration

## 2. Product boundaries

The app coordinates clinician-provided discharge instructions.

It must not:
- Diagnose conditions.
- Recommend dose changes or new medicines.
- Claim an interaction check proves a medicine is safe.
- Imply someone has reviewed a message when only the agent has processed it.
- Claim a calendar event is a confirmed hospital appointment.
- Claim completing 96 hours means the patient is medically cleared.

Exclude from the initial build:
- EHR/HIS integration
- Voice calls
- Multiple hospitals
- PDF parsing
- Real inbound WhatsApp
- Unreviewed multilingual clinical generation

## 3. Design skills

Use these skills for UI and UX:

- https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
- https://github.com/delphi-ai/animate-skill
- https://github.com/ilmandude/impeccable-lite

Read their actual SKILL.md files and applicable references.
Do not claim to have applied a skill that could not be accessed.

Design direction:
- Light, calm interface with strong text contrast.
- Teal primary actions.
- Amber and red reserved for attention and urgency.
- Spacious, mobile-first patient screens.
- Denser, task-focused hospital screens.
- Clear labels, accessible focus states and large touch targets.
- Status must be conveyed through text/icons as well as colour.
- Subtle transitions with reduced-motion support.
- Urgent information must appear immediately.
- Avoid decorative charts and excessive animation.

Use one consistent design system across all roles.

## 4. Technical architecture

Prefer this stack unless the repository already has a suitable working alternative:

Frontend:
- React + TypeScript
- Vite
- Tailwind CSS
- React Router
- TanStack Query
- Framer Motion where useful

Backend:
- FastAPI
- Pydantic validation
- SQLAlchemy
- Alembic migrations
- SQLite for the local hackathon build

Execution:
- Database-backed scheduled tasks.
- A background worker that checks for due work.
- One worker process for the initial build.
- Server-Sent Events for UI updates.

Keep this a modular application, not microservices.

Separate:
- API routes
- Permission checks
- Agent planning/triage
- Approval policies
- Task execution
- Tool adapters
- Persistence
- Audit events

The LLM proposes structured actions.
Deterministic backend code validates and authorises execution.

Keep API keys and provider credentials on the server.

## 5. Accounts and permissions

### Patients

Use hospital invitation + phone number + one-time code.
No patient password or public self-registration.

Flow:
1. Nurse records the patient's phone number.
2. Hospital initiates an invitation.
3. Patient verifies the code.
4. Backend creates an authenticated session.
5. Patient sees only their own discharge episodes.

Implement:
- Expiring, single-use codes.
- Attempt limits and resend cooldown.
- Rate limiting.
- Secure session handling.
- Generic responses that do not reveal whether a phone is registered.
- A clear sign-out option for shared devices.

Invitation links must not expose medical information or grant access by themselves.

Provide a separate, explicitly enabled demo authentication mode.
Do not let demo codes or role switching work in production mode.

### Hospital staff

Use provisioned staff accounts.
Doctors and nurses cannot self-select privileged roles.

For the demo, provide seeded doctor and nurse accounts.

Enforce all permissions on the backend, including episode assignments.
Hiding buttons is not authorisation.

### Role permissions

Doctor:
- Review extracted discharge information.
- Review and activate the initial plan.
- Approve, edit or reject proposed external actions.
- Review escalations.
- Revoke routine-action permission.
- Review the episode summary.

Nurse:
- Create draft discharge episodes.
- Enter and correct draft information.
- Assign the care team.
- Monitor responses and overdue tasks.
- Acknowledge and route concerns.
- Add coordination notes.
- Cannot authorise clinical actions reserved for the doctor.

Patient:
- View reviewed instructions.
- Submit check-ins and concerns.
- Read approved messages.
- View follow-up details.
- Cannot access internal agent traces, staff notes or other patients.

## 6. Core data model

Model discharge episodes separately from patients.

Suggested entities: User, StaffAssignment, Patient, DischargeEpisode, DischargeDocument, PatientFact,
PlanVersion, PlanStep, ProposedAction, ApprovalDecision, RoutineAuthorisation, ScheduledTask, CheckIn,
CheckInResponse, Message, FollowUpEvent, ToolExecution, ReviewItem, AuditEvent

Important fields:
- Source and timestamp for each extracted fact.
- Assigned doctor and nurse for each episode.
- Actual discharge timestamp.
- Episode status and current plan version.
- Exact action payload and recipient.
- Action execution mode: live or simulated.
- Provider result and delivery status where available.
- Who reviewed, edited, approved or rejected something.

Use UTC internally and display Asia/Kolkata dates and times by default.

Start the 96-hour window from actual discharge time.
Late registration must not trigger a burst of all missed reminders.

## 7. Hospital UX

Navigation: Overview, Patients, Approvals, Settings

### Overview
Prioritise: Urgent concerns. Pending doctor reviews. Overdue check-ins. Failed actions. Unassigned work.
Use operational counts rather than decorative analytics.
Doctor default view: Reviews and approvals needing their attention.
Nurse default view: Discharges to prepare and patients needing follow-up.

### Intake
Collect: Patient name and age. Phone number. Preferred language. Optional caregiver and authorised
contact details. Actual discharge time. Assigned doctor and nurse. Relevant risk inputs from the
provided summary/form. Pasted discharge summary.

Provide fictional sample summaries: Heart failure. Appendectomy. Diabetes/DKA.
Use one scenario as the primary tested demo.

Show original summary alongside editable extracted information.
Mark missing fields as "not provided."
Never invent missing dosage or clinical instructions.

### Patient workspace
Header: Patient. Episode status. Assigned team. Time since discharge. Risk category with reasons. Urgent outstanding items.
Main content: 96-hour timeline. Current and upcoming steps. Proposed changes and reasons. Pending approval panel.
Supporting tabs: Check-ins and messages. Medicines and reviewed instructions. Follow-ups. Memory. Activity.
Timeline: Planned, blocked, executed, failed and skipped steps. Actual execution timestamps.
Current-time marker. Plan-version changes. Approval blockers. Text labels alongside status colours.

### Approval queue
Each card shows: Patient and priority. Proposed action. Recipient. Exact message or booking details.
Why it was proposed. Supporting facts. Plan version. Live/simulated mode.
Actions: Edit and approve. Approve. Reject with reason.
Show urgent items first.
Never silently move an item under a user's cursor during review.

## 8. Patient UX

Mobile-first navigation: Today, My Instructions, Messages, Follow-up

### Today
Show: The next useful action. Due check-in. Relevant approved reminders. Recovery-period progress.
Clear contact information and monitoring expectations.
Avoid exposing raw risk scores and internal agent reasoning.

### Guided check-in
Use guided questions plus optional free text.
Flow: 1. Open the due check-in. 2. Answer a small number of clinician-reviewed questions.
3. Add optional concerns. 4. Review and submit. 5. See an accurate receipt status.
Requirements: One question at a time. Large buttons. Visible progress. Back navigation.
Draft persistence. Prevent duplicate submissions. Preserve answers if processing fails.
Questions come from a reviewed template associated with the plan.
The agent may propose additional questions, but new clinical questions require review.
Distinguish: Received. Agent processing complete. Awaiting staff review. Reviewed by staff. Response available.
Do not use "reviewed" to mean merely processed by AI.

### Instructions
Show clinician-reviewed discharge information. Maintain traceability to the source and reviewed version.

### Messages
Show only patient-visible messages. Track queued, provider-accepted, delivered and failed states
where supported. Do not invent delivery confirmation.

### Follow-up
Clearly distinguish: Proposed follow-up. Calendar event created. Appointment confirmed by the
hospital, if explicitly recorded.

## 9. Agent workflow

### Intake understanding
Extract: Diagnosis. Allergies. Medicines and existing instructions. Follow-up instructions.
Discharge red flags. Missing or ambiguous information.
Use structured LLM output with validation. Provide a deterministic fallback for the demo.

### Risk prioritisation
Calculate a transparent demo prioritisation category using defined inputs. Show reasons.
Do not describe it as a validated clinical prediction.

### Planning
Generate a structured 0–96-hour plan. Each step contains: Timing. Purpose. Tool/action type.
Validated arguments. Rationale. Dependencies. Approval requirements.
High-risk demo rules may add more frequent check-ins and caregiver coordination.
Validate: Allowed tools. Argument schemas. Timing bounds. Recipients. Required approvals. Dependencies.
Reject invalid plans and use the rule-based fallback. Show fallback use in the activity log.

### Replanning
Replan from: Tool findings. Patient responses. Staff corrections. Rejected actions. Explicit manual request.
Create a new plan version. Explain what changed and why.
Preserve completed work. Supersede only affected pending work.
Invalidate approvals when their exact action or relevant context changes.
Do not cancel external bookings merely by changing an internal plan.

### Memory
Store facts with: Value. Source. Timestamp. Review state. Whether extracted, patient-reported or inferred.
Log which facts informed planning, triage and summaries.
Show useful decision summaries, not hidden chain-of-thought.

## 10. Approval policy

The doctor reviews the initial plan and may authorise a bounded set of routine actions.
Routine authorisation specifies: Exact templates or allowed content. Recipients. Schedule/frequency.
Episode and expiration. Permitted action types.
Actions outside that scope require new approval.
Always require explicit approval for: New or materially changed clinical messages. External bookings.
Escalation messages. Newly proposed caregiver disclosures. Actions no longer covered by the active authorisation.
Bind approval to the exact action payload and version.
Editing an approved payload invalidates that approval.
Revocation blocks future covered actions.
Rejecting one action and revoking the whole routine authorisation are separate operations.
The executor must check approval again immediately before execution.
Urgent detection should immediately create a visible hospital review item.
External actions remain subject to approval.
Track acknowledgement, ownership and waiting time.

## 11. Tools and integrations

Implement adapters with consistent results: Success/failure/unknown outcome. Live/simulated mode.
Provider reference. Timestamp. Safe error information.

Tools:
1. Medication information: openFDA label retrieval where applicable. Separate local interaction rules.
   Show sources and limitations. Missing lookup is not a safety clearance.
2. Messaging: Twilio SMS first. Optional WhatsApp sandbox later. DEMO_PHONE redirects outbound demo
   messages to the presenter. Display the effective recipient before approval.
3. Calendar: Google Calendar event creation. Respect configured timezone and scheduling rules.
   Clearly state when availability has not been checked.
4. Care-team alert: SMTP email if time permits.
5. Episode summary: Save outcomes, actions and unresolved concerns.

Demonstrate at least two successful external tool integrations for the stated hackathon requirement.
Simulated calls do not count as proof of real actions.
A failed live action must remain visibly failed or unknown.
Do not silently relabel simulated output as a successful live action.

## 12. Reliable execution

Use durable task states, for example: Scheduled, Awaiting approval, Ready, Running, Succeeded,
Failed, Outcome unknown, Cancelled/superseded.
Implement: Atomic task claiming. Idempotency keys. Bounded retries. Duplicate-click protection.
Restart recovery. Execution result persistence.
A provider timeout may mean the action already happened.
Reconcile uncertain outcomes before retrying side-effecting actions.
Do not perform external API calls inside long database transactions.
Persist check-ins before queueing triage. Persist events before notifying the frontend.
SSE is for updates, not the source of truth. On reconnect, refetch current state and recover missed events.

## 13. Demo mode

Provide a clearly labelled demo drawer: Role switching, only in demo mode. Load fictional samples.
Submit patient test responses. Advance time by 6 or 24 hours. Show current simulated date and time.
Show tool modes. Reset demo data.
All scheduling must use a shared clock abstraction. Avoid mixing simulated time with system time.
Fast-forward must respect dependencies and approval gates. It must not send a burst of outdated reminders.
Reset: Confirm before wiping local demo state. Reset database seed and simulated clock. Explain that
sent messages and external events are not undone.
The full demo must work without API keys.

## 14. Episode ending

At 96 hours: Generate a summary. List completed and missed tasks. Include responses, escalations and
unresolved issues. Mark the monitoring window ended.
Keep unresolved review items visible. Use "awaiting closure review" where appropriate.
Do not automatically imply clinical clearance.

## 15. Implementation order

Phase 1: Foundation and one working flow — Inspect repo. Set up data model and migrations. Add seeded
roles and backend permissions. Build sample intake. Generate a draft plan. Doctor reviews and approves.
Execute one simulated message. Patient sees instructions and submits a check-in.
Phase 2: Agent behaviour and execution — Durable scheduler. Shared simulated clock. Triage. Versioned
replanning. Approval invalidation. Memory and trace. Urgent review workflow.
Phase 3: Live integrations — Messaging. Medication information. Calendar if feasible. Explicit tool
status and failure handling. Verify two real integrations.
Phase 4: Complete UX — Doctor and nurse queues. Patient mobile screens. Timeline. SSE updates and
reconnection. Settings. Demo reset. Accessibility and responsive polish.
Phase 5: Delivery — End-to-end tests. Windows setup.ps1 and dev.ps1. .env.example without secrets.
README with setup, modes, limitations and demo script. Updated feature status.
Keep the app runnable after every phase.

## 16. Required verification

1. Intake → review → approval → execution.
2. Patient check-in → triage → revised plan.
3. Red flag → urgent review → approved response.
4. Rejection prevents execution.
5. Changed payload invalidates prior approval.
6. Revoked routine authorisation blocks future sends.
7. Repeated ticks and approval clicks do not duplicate actions.
8. Patient cannot access another patient's episode.
9. Nurse cannot perform doctor-only approval.
10. Restart preserves pending tasks.
11. Provider failure and uncertain outcome remain accurately represented.
12. SSE reconnect restores current state.
13. Fast-forward does not bypass approvals.
14. Reset does not claim to undo external actions.

Inspect desktop hospital screens and mobile patient screens.

## 17. Demo narrative

Use one fictional high-risk patient:
1. Nurse prepares discharge.
2. Agent proposes the 96-hour plan.
3. Medication information changes a proposed step.
4. Doctor reviews and authorises routine actions.
5. An approved external action executes.
6. Patient completes a website check-in.
7. A concerning reply changes the plan.
8. Hospital receives an urgent review item.
9. Doctor approves the proposed response.
10. Show memory, plan differences and execution history.
Make the agent's decisions and the human's control easy to see.

## 18. Working expectations

Before coding, give a concise repository assessment and implementation sequence. Then proceed.
Ask only when a missing decision materially blocks progress. Use reasonable defaults for routine details.
Do not claim: A feature is tested when only code exists. A simulated integration is live. A provider
accepted message is delivered. A generated plan is clinician-reviewed. A prototype is ready for real clinical use.
At each milestone, report: What works. What was verified. What remains. Any blockers.

---

# PART 2 — Team process brief (abridged to the parts that bind acceptance)

Team: Lead (architecture/integration), A backend & access control, B agent workflow & execution,
C hospital interface, D patient interface, E independent evaluator (separate from implementation;
must not accept an implementer's "done" as proof; reports defects, implementers fix; rechecks fixes;
produces the final verification report).

## Evaluator instructions (verbatim)

Evaluate behaviour, not just code style.

Create an acceptance matrix: Requirement. Test procedure. Expected result. Observed result. Evidence.
Pass, fail or blocked.

Use fictional data and sandbox/test destinations. Do not contact real patients or send messages to arbitrary recipients.

### Core journeys
Verify: Nurse prepares an episode. Doctor reviews the initial plan. Routine authorisation covers only
its defined scope. Patient signs in with a code. Patient sees only their own information. Patient
submits a guided check-in. Agent processes the saved response. A concern changes the plan. Doctor
approves a new action. The authorised action executes once.

### Permissions
Attempt: Patient accessing another patient's IDs. Patient calling staff endpoints directly. Nurse
calling doctor-only approval endpoints. Unassigned staff accessing restricted episodes. Expired and
reused verification codes. Demo authentication outside demo mode.

### Approval and execution
Verify: Rejected actions do not execute. Edited payloads invalidate old approvals. Superseded actions
cannot execute. Revocation blocks future routine actions. Duplicate approval requests do not duplicate
execution. Repeated scheduler ticks do not duplicate sends. Restart preserves pending work.
Fast-forward respects approval gates. Timeouts do not trigger unsafe blind retries. Failed live
actions are not presented as simulated successes.

### Patient experience
Verify: Mobile layout and keyboard navigation. Clear validation errors. Draft recovery.
Duplicate-submit protection. Processing failure does not lose the submitted reply. "Received" does not
imply clinician review. Follow-up status does not falsely imply confirmation. No internal notes or
agent traces leak into patient views.

### Agent integrity
Verify: Extracted facts retain sources. Missing information is not invented. Invalid plans are
rejected. Fallback use is visible. Replanning actually changes persisted steps. Memory claims
correspond to stored facts. Clinical content remains within the reviewed instructions and approval scope.

### Live updates
Verify: Doctor, nurse and patient views reflect the same backend state. SSE disconnect/reconnect
recovers correctly. Refreshing the page preserves state. Updates do not overwrite unsaved clinician edits.

### Tool evidence
Distinguish: Implemented. Tested in simulation. Tested against a sandbox/live API. Blocked by missing credentials.
A mock test is not proof of a working external integration. Provider acceptance is not proof of message delivery.

## Defect handling
For every defect report: Severity. Affected role/flow. Reproduction steps. Expected versus actual
behaviour. Relevant evidence. Owning agent. Retest result.
Severity: P0 unauthorised disclosure/action, approval bypass or serious data corruption. P1 broken
core journey, duplicate external action or materially false status. P2 recoverable functional or
accessibility problem. P3 cosmetic issue.
Fix all P0 and P1 defects before declaring the build ready for demonstration.
The evaluator must reproduce the original failure after the fix and confirm it no longer occurs.
Add a regression test where it meaningfully protects the behaviour.
Do not weaken tests or remove requirements merely to get a passing result.
If a requirement cannot be completed, mark it blocked or incomplete. Never fabricate verification.

## Final delivery gate
Build succeeds. Relevant automated tests pass. Core journeys tested in the running app. Backend
permissions tested directly. No unresolved P0/P1 defects. Simulation and live results clearly
separated. Missing credentials and untested integrations disclosed. Setup instructions work from a
clean environment. No secrets committed.
