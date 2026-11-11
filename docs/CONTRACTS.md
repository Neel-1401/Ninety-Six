# Ninety-Six — Shared Contracts (lead-owned)

Source of truth for every agent. **Change requests go through the lead.** Code contracts live in
`backend/app/models.py`, `clock.py`, `db.py`, `events.py`, `errors.py`, `tools/base.py`,
`frontend/src/lib/types.ts` and `frontend/src/ui/*`. If this file and code disagree, report it.

Product boundary (never violate): coordinate clinician-provided instructions only. No diagnosis, no
dose changes or new medicines, no "safe" claims from interaction checks, never call agent processing
"reviewed", never call a calendar event a confirmed appointment, never imply 96 h = medical clearance.

---

## 1. File ownership

| Owner | Files |
|---|---|
| **Lead** | `backend/app/{models,config,clock,db,events,errors}.py`, `backend/app/tools/base.py`, `backend/requirements.txt`, `backend/.env.example`, `docs/CONTRACTS.md`, `docs/DECISIONS.md`, `FEATURES.md`, `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig*.json`, `frontend/index.html`, `frontend/src/{main.tsx,App.tsx,index.css}`, `frontend/src/lib/**`, `frontend/src/ui/**`, `scripts/**`, `README.md` |
| **A — backend & access** | `backend/app/main.py`, `backend/app/auth/**`, `backend/app/api/**`, `backend/app/schemas/**`, `backend/app/seed_accounts.py`, `backend/alembic.ini`, `backend/alembic/**`, `backend/tests/test_auth*.py`, `backend/tests/test_permissions*.py`, `backend/tests/test_api*.py`, `backend/tests/conftest.py` |
| **B — agent & execution** | `backend/app/agent/**`, `backend/app/llm/**`, `backend/app/policy/**`, `backend/app/execution/**`, `backend/app/tools/*` (except `base.py`), `backend/app/services/**`, `backend/app/samples.py`, `backend/tests/test_agent*.py`, `backend/tests/test_execution*.py`, `backend/tests/test_policy*.py` |
| **C — hospital UI** | `frontend/src/hospital/**`, `frontend/src/demo/**` |
| **D — patient UI** | `frontend/src/patient/**` |
| **E — evaluator** | `docs/ACCEPTANCE.md`, `frontend/e2e/**`, `backend/tests/acceptance/**` (tests only, no feature code) |

`backend/legacy/` holds the original unrun scaffold — **read-only reference** for porting logic.

## 2. Roles and permission matrix

Staff access to an episode requires an **active `StaffAssignment`** (doctor or nurse) on that episode.
Patients access only episodes where `episode.patient_id == user.patient_id`. All checks are on the
server; `permissions` blocks in responses are UI hints only.

| Capability | Doctor (assigned) | Nurse (assigned) | Patient (own) | Endpoint(s) |
|---|---|---|---|---|
| Create draft episode | – | ✅ (any nurse; creator auto-assigned) | – | `POST /api/episodes` |
| Edit draft info / correct facts | ✅ | ✅ (draft, awaiting_review) | – | `PATCH /api/episodes/{id}`, `/facts` |
| Edit contact details on active episode | ✅ | ✅ | – | `PATCH /api/episodes/{id}` (`patient` contact fields only) |
| Assign care team | – | ✅ | – | `PUT /api/episodes/{id}/team` |
| Submit for doctor review | – | ✅ | – | `POST /api/episodes/{id}/submit` |
| Regenerate draft plan | ✅ | ✅ | – | `POST /api/episodes/{id}/plan/regenerate` |
| Review extraction + activate plan (+ routine authorisation) | ✅ | ❌ 403 | – | `POST /api/episodes/{id}/activate` |
| Return to draft | ✅ | – | – | `POST /api/episodes/{id}/return-to-draft` |
| Approve / edit-approve / reject action | ✅ | ❌ 403 (read-only view) | – | `POST /api/actions/{id}/...` |
| Revoke routine authorisation | ✅ | ❌ 403 | – | `POST /api/routine-authorisations/{id}/revoke` |
| Retry failed action | ✅ | ❌ 403 | – | `POST /api/actions/{id}/retry` |
| Reconcile unknown outcome | ✅ | ✅ | – | `POST /api/executions/{id}/reconcile` |
| Request replan | ✅ | ✅ | – | `POST /api/episodes/{id}/replan` |
| Acknowledge / assign / resolve review item | ✅ | ✅ (except `requires_role=doctor` resolve) | – | `/api/review-items/{id}/...` |
| Mark check-in reviewed | ✅ | ✅ | – | `POST /api/checkins/{id}/mark-reviewed` |
| Confirm follow-up as hospital appointment | ✅ | ✅ | – | `POST /api/followups/{id}/confirm` |
| Add coordination note | ✅ | ✅ | – | `POST /api/episodes/{id}/notes` |
| Send patient invitation | ✅ | ✅ | – | `POST /api/episodes/{id}/invite` |
| Close episode after closure review | ✅ | ❌ 403 | – | `POST /api/episodes/{id}/close` |
| View reviewed instructions, check-ins, approved messages, follow-ups | – | – | ✅ | `/api/me/**` |
| Submit check-in / concern | – | – | ✅ | `POST /api/me/checkins/{id}/submit`, `POST /api/me/episodes/{id}/concerns` |
| Agent traces, staff notes, triage levels, other patients | ✅/✅ | ✅/✅ | ❌ never | – |

Denials: unassigned staff or another patient → **404 `not_found`**; wrong role on a visible
resource → **403 `forbidden`**; patient calling any staff endpoint → **403 `forbidden`**;
unauthenticated → **401**. Demo endpoints outside demo mode → **404 `demo_disabled`**.

## 3. Authentication

* **Staff**: provisioned accounts only (seeded). `POST /api/auth/staff/login {username,password}`.
  Password hash: `hashlib.scrypt` (stdlib). Generic failure message.
* **Patient**: invitation + phone + one-time code. No passwords, no self-registration.
  1. Nurse records phone at intake. 2. `POST /api/episodes/{id}/invite` (staff) creates an
  `Invitation` and sends an invitation SMS (no medical info; link `/p/invite/{token}`; the token
  alone grants nothing). 3. `POST /api/auth/patient/request-code {phone, invitation_token?}` →
  always `202 {"message": "If this number is registered with us, we've sent a code.", "resend_after_seconds": 60}`.
  4. `POST /api/auth/patient/verify {phone, code}` → session.
* OTP: 6 digits, scrypt-hashed, TTL 10 min (**real time**), single use, max 5 attempts then
  invalidated, 60 s resend cooldown per phone, rate limits per phone and per IP (`RateLimitHit`).
  Verify failure is always `400 {"error":{"code":"invalid_code","message":"That code didn't work. Check it, or request a new code."}}`.
* Sessions: random 32-byte token in **HttpOnly, SameSite=Lax** cookie `ns_session` (Secure when
  `COOKIE_SECURE=true`); only the sha256 is stored (`AuthSession`). Staff 12 h, patient 72 h.
  `POST /api/auth/logout` revokes. `GET /api/auth/me` → `MeOut` or 401.
* CSRF: every non-GET `/api/**` request must send header `X-Requested-With: ninety-six`
  (frontend client does this); otherwise 403. CORS allows only `FRONTEND_ORIGIN` with credentials.
* **Demo mode** (`APP_MODE=demo`, default for local): `/api/demo/**` enabled — role switching
  (`login-as`), simulated outbox showing OTP codes, clock advance, reset. In `APP_MODE=production`
  every `/api/demo/**` route returns 404 and OTP bodies are redacted in storage.

## 4. API (all JSON, snake_case, times ISO-8601 UTC `...Z`)

### 4.1 Common objects (TypeScript mirror: `frontend/src/lib/types.ts`)

```
UserOut        {id, role, display_name, username|null, specialty|null, patient_id|null}
MeOut          {user: UserOut, session_via: "password"|"otp"|"demo", demo_mode, sim_now, display_timezone}
TeamMember     {user_id, display_name, role, specialty|null}
PatientOut     {id, full_name, age|null, sex|null, phone_e164, preferred_language, caregiver_name|null,
                caregiver_relation|null, caregiver_phone_e164|null, caregiver_authorised, hospital_number|null}
EpisodeRow     {id, patient:{id, full_name, age, sex}, status, discharge_at, window_ends_at,
                hours_since_discharge, risk_category|null, risk_reasons:[str],
                team:{doctor:TeamMember|null, nurse:TeamMember|null},
                counts:{urgent_reviews, open_reviews, pending_approvals, overdue_checkins, failed_actions},
                next_step:{title, due_at}|null, current_plan_version:int|null, updated_at}
EpisodeDetail  EpisodeRow + {patient: PatientOut, document:{id,text,created_at}|null, facts:[FactOut],
                extraction_method, extraction_notes:[str], risk_inputs:{...}, routine_authorisations:[RoutineOut],
                activated_at, activated_by:str|null, monitoring_ended_at, closed_at, closure_note,
                invitation:{status, created_at, expires_at}|null, row_version,
                permissions:{can_edit_draft, can_submit, can_activate, can_decide_actions,
                             can_revoke_routine, can_close, can_assign_team, can_add_note, can_ack_reviews}}
FactOut        {id, category, label, value:{...}, source_kind, source_ref|null, source_excerpt|null,
                extraction_method|null, is_missing, review_state, reviewed_by:str|null, reviewed_at|null,
                patient_visible, supersedes_id|null, active, created_by, created_at}
PlanVersionOut {id, version, status, source, trigger, trigger_ref|null, rationale|null,
                change_summary:[{change:"added"|"removed"|"changed"|"kept", step_key, description}],
                validation:{llm_attempted:bool, llm_valid:bool|null, fallback_used:bool, errors:[str]},
                facts_used:[int], based_on_version_id|null, created_by, created_at, activated_at|null,
                activated_by:str|null, steps:[PlanStepOut]}
PlanStepOut    {id, step_key, seq, title, purpose, action_type, arguments, offset_hours, due_at,
                freshness_hours|null, depends_on:[str], approval_requirement, rationale, origin, status,
                status_reason|null, executed_at|null, result_summary|null,
                action:{id, approval_state, execution_state, priority}|null,
                task:{id, status, attempts, status_reason|null}|null}
ActionOut      {id, episode_id, patient:{id, full_name}, plan_version:int, plan_version_id, step_key,
                step_title, action_type, recipient_role, intended_recipient, effective_recipient,
                redirected:bool, channel, template_id|null, payload:{...}, payload_hash, payload_version,
                editable_fields:[str], expected_mode, priority, reason,
                supporting_facts:[{id,label,text,source_ref}], approval_requirement, approval_state,
                execution_state, routine_authorisation_id|null, invalidated_reason|null, due_at,
                created_at, decisions:[DecisionOut], last_execution:ExecutionOut|null}
DecisionOut    {id, decision:"approved"|"edited_approved"|"rejected", payload_hash, decided_by, decided_at,
                reason|null, valid, invalidated_reason|null}
RoutineOut     {id, status, action_types, template_ids, recipient_roles, offsets_hours, max_per_day,
                expires_at, scope_summary, plan_version_id, granted_by, granted_at, revoked_by|null,
                revoked_at|null, revoke_reason|null}
ExecutionOut   {id, task_id|null, action_id|null, tool, provider, mode, attempt, outcome, provider_ref|null,
                provider_status|null, delivery_status|null, safe_error|null, started_at, finished_at|null,
                reconciled_by|null, reconciled_at|null, reconcile_note|null}
CheckInOut     {id, kind, title, template_id|null, due_at, available_until|null, status, submitted_at|null,
                processing_state, triage_level|null, triage_reasons:[str], triage_source|null,
                staff_review_state, reviewed_by|null, reviewed_at|null, review_note|null,
                answers:[{question_id, question_text, value, label}], free_text|null, response_message_id|null}
MessageOut     {id, direction, kind, audience, channel, subject|null, body, patient_visible,
                intended_recipient|null, effective_recipient|null, mode, status, provider_ref|null,
                error|null, created_at, sent_at|null, status_updated_at|null, action_id|null}
FollowUpOut    {id, title, provider_name|null, location|null, start_at|null, end_at|null, status,
                availability_checked, calendar_mode|null, external_ref|null, external_link|null,
                confirmed_by|null, confirmed_at|null, notes|null}
ReviewItemOut  {id, episode_id, patient:{id, full_name}, kind, priority, title, summary, detail,
                source_type|null, source_id|null, requires_role, status, owner:{id,display_name}|null,
                created_at, acknowledged_at|null, acknowledged_by|null, resolved_at|null,
                resolved_by|null, resolution_note|null, waiting_minutes}
AuditEventOut  {id, kind, title, actor_type, actor_label, detail, created_at}
NoteOut        {id, author:str, body, created_at, review_item_id|null}
ToolStatusOut  {name, label, mode, configured, detail, last_live_success_at|null, last_failure_at|null}
```

### 4.2 Staff endpoints (role doctor|nurse; episode-scoped ones need assignment)

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/auth/staff/login` | `{username, password}` | `MeOut` + cookie |
| `POST /api/auth/logout` | – | `204` |
| `GET /api/auth/me` | – | `MeOut` |
| `GET /api/staff?role=doctor\|nurse` | – | `[UserOut]` |
| `GET /api/samples` | – | `[{key,label,primary,patient:{...},risk_inputs:{...},discharge_hours_ago,summary_text,suggested_doctor_username}]` |
| `GET /api/overview` | – | `OverviewOut` (§4.5) |
| `GET /api/episodes?status=&scope=mine` | – | `[EpisodeRow]` (assigned only) |
| `POST /api/episodes` | `{patient:{full_name, age, sex, phone_e164, preferred_language, caregiver_name?, caregiver_relation?, caregiver_phone_e164?, caregiver_authorised, hospital_number?}, discharge_at, doctor_id, nurse_id?, risk_inputs:{lives_alone?:bool, prior_admission_6m?:bool, caregiver_available?:bool}, summary_text, sample_key?}` | `201 EpisodeDetail` (extraction already run) |
| `GET /api/episodes/{id}` | – | `EpisodeDetail` |
| `PATCH /api/episodes/{id}` | `{row_version, patient?:{...partial}, discharge_at?, risk_inputs?}` | `EpisodeDetail` (409 `stale_version` if row_version differs) |
| `PUT /api/episodes/{id}/team` | `{doctor_id, nurse_id}` | `EpisodeDetail` |
| `POST /api/episodes/{id}/facts` | `{category, label, value}` | `FactOut` (source_kind `staff_entered`) |
| `PATCH /api/episodes/{id}/facts/{fact_id}` | `{value, label?, reason?}` | `FactOut` (new fact superseding old, `staff_corrected`) |
| `POST /api/episodes/{id}/facts/{fact_id}/reject` | `{reason}` | `FactOut` |
| `POST /api/episodes/{id}/submit` | – | `EpisodeDetail` (draft plan generated; status `awaiting_review`) |
| `POST /api/episodes/{id}/plan/regenerate` | `{reason?}` | `PlanVersionOut` |
| `POST /api/episodes/{id}/return-to-draft` | `{reason}` | `EpisodeDetail` |
| `POST /api/episodes/{id}/activate` | `{plan_version_id, confirm_fact_ids:[int], routine:{template_ids, recipient_roles, offsets_hours, max_per_day}\|null}` | `EpisodeDetail` (409 `stale_version` if plan changed) |
| `POST /api/episodes/{id}/replan` | `{reason}` | `PlanVersionOut` |
| `GET /api/episodes/{id}/plans` | – | `[PlanVersionOut]` newest first |
| `GET /api/episodes/{id}/plans/current` | – | `PlanVersionOut` (latest draft before activation, active after) |
| `GET /api/episodes/{id}/actions` | – | `[ActionOut]` |
| `GET /api/episodes/{id}/checkins` | – | `[CheckInOut]` |
| `GET /api/episodes/{id}/messages` | – | `[MessageOut]` (all audiences) |
| `GET /api/episodes/{id}/followups` | – | `[FollowUpOut]` |
| `GET /api/episodes/{id}/memory` | – | `{facts:[FactOut], usage:[{fact_id, used_for, ref_type, ref_id, note, created_at}]}` |
| `GET /api/episodes/{id}/activity?after_id=` | – | `[AuditEventOut]` |
| `GET /api/episodes/{id}/executions` | – | `[ExecutionOut]` |
| `GET /api/episodes/{id}/review-items` | – | `[ReviewItemOut]` |
| `GET/POST /api/episodes/{id}/notes` | `{body, review_item_id?}` | `[NoteOut]` / `NoteOut` |
| `GET /api/episodes/{id}/summary` | – | episode summary JSON (§5.6) or 404 if not generated |
| `POST /api/episodes/{id}/invite` | – | `{invitation:{status, expires_at}, link, message:MessageOut}` |
| `POST /api/episodes/{id}/close` | `{note}` | `EpisodeDetail` (409 if open urgent review items) |
| `GET /api/approvals?state=pending\|all` | – | `[ActionOut]` urgent first, then due_at |
| `GET /api/actions/{id}` | – | `ActionOut` |
| `POST /api/actions/{id}/approve` | `{payload_hash}` | `ActionOut` (409 `stale_version` if hash ≠ current) |
| `POST /api/actions/{id}/edit-approve` | `{base_payload_hash, payload:{...editable fields}}` | `ActionOut` |
| `POST /api/actions/{id}/reject` | `{base_payload_hash, reason}` (reason required) | `ActionOut` |
| `POST /api/actions/{id}/retry` | – | `ActionOut` |
| `POST /api/executions/{id}/reconcile` | `{outcome:"success"\|"failure", note}` | `ExecutionOut` |
| `POST /api/routine-authorisations/{id}/revoke` | `{reason}` | `RoutineOut` |
| `GET /api/review-items?status=open\|acknowledged\|resolved\|active` | – | `[ReviewItemOut]` (active = open+acknowledged) |
| `POST /api/review-items/{id}/acknowledge` | – | `ReviewItemOut` |
| `POST /api/review-items/{id}/assign` | `{user_id}` | `ReviewItemOut` |
| `POST /api/review-items/{id}/resolve` | `{note}` | `ReviewItemOut` |
| `POST /api/checkins/{id}/mark-reviewed` | `{note}` | `CheckInOut` |
| `POST /api/followups/{id}/confirm` | `{note}` | `FollowUpOut` |
| `GET /api/settings` | – | `SettingsOut` (§4.5) |
| `GET /api/clock` | – | `{now, sim_offset_hours, demo_mode, display_timezone}` (any signed-in user) |
| `GET /api/stream` | – | SSE (§7) (any signed-in user) |
| `GET /api/health` | – | `{ok:true, mode}` (public) |

Idempotency: repeating approve/edit-approve/reject with the same inputs after success returns the
current `ActionOut` with `200` and creates **no** additional decision or execution. Approve on an
action that is no longer pending → `409 invalid_state`.

### 4.3 Patient endpoints (role patient; own episodes only)

| Method & path | Returns |
|---|---|
| `POST /api/auth/patient/request-code {phone, invitation_token?}` | `202 {message, resend_after_seconds}` |
| `POST /api/auth/patient/verify {phone, code}` | `MeOut` + cookie |
| `GET /api/me/episodes` | `[PatientEpisodeOut]` |
| `GET /api/me/episodes/{id}/today` | `TodayOut` |
| `GET /api/me/episodes/{id}/instructions` | `InstructionsOut` (only doctor-confirmed facts of the activated plan; 200 with `reviewed:false` and empty lists before activation) |
| `GET /api/me/episodes/{id}/checkins` | `[PatientCheckInOut]` |
| `GET /api/me/checkins/{id}` | `PatientCheckInOut` (questions included while due) |
| `PUT /api/me/checkins/{id}/draft {answers:{qid:value}, free_text}` | `PatientCheckInOut` |
| `POST /api/me/checkins/{id}/submit {submission_id, answers:[{question_id, value}], free_text}` | `202 PatientCheckInOut`; same `submission_id` again → `200` same receipt; already submitted with another id → `409 already_submitted` |
| `POST /api/me/episodes/{id}/concerns {submission_id, text}` | `202 PatientCheckInOut` (kind `concern`) |
| `GET /api/me/episodes/{id}/messages` | `[PatientMessageOut]` (patient-visible only) |
| `GET /api/me/episodes/{id}/followups` | `[PatientFollowUpOut]` |

```
PatientEpisodeOut {id, status, discharge_at, window_ends_at, hours_since_discharge, monitoring_active,
                   hospital_name, hospital_phone, emergency_number, care_team:[{display_name, role_label}]}
TodayOut          {episode: PatientEpisodeOut,
                   next_action:{kind:"checkin"|"instructions"|"followup"|"none", title, description, checkin_id|null},
                   due_checkin: PatientCheckInOut|null, upcoming_checkin:{title, due_at}|null,
                   reminders:[{id, body, sent_at|null, status}],
                   progress:{hours_elapsed, window_hours:96, ended:bool},
                   monitoring_expectations: str, contact:{hospital_phone, emergency_number}}
InstructionsOut   {episode_id, reviewed:bool, reviewed_by|null, reviewed_at|null, plan_version|null,
                   diagnosis:[Item], medicines:[{name, dose, frequency, instructions, source_ref}],
                   allergies:[Item], warning_signs:[Item], follow_up:[Item], other:[Item], source_note}
                   Item = {text, source_ref|null}   ("not provided" items are shown as such)
PatientCheckInOut {id, kind, title, due_at, available_until|null, status,
                   questions:[Question]|null, draft:{answers, free_text}|null, submitted_at|null,
                   answers:[{question_id, question_text, label}],
                   receipt:{received:bool, received_at|null,
                            agent_processing:"not_started"|"processing"|"complete"|"failed",
                            staff_review:"not_requested"|"awaiting"|"reviewed", staff_reviewed_at|null,
                            response_available:bool, response_message_id|null}}
Question          {id, text, type:"single_choice"|"yes_no"|"text", options:[{value,label}], required, help|null}
PatientMessageOut {id, kind, body, channel, created_at, sent_at|null,
                   status:"queued"|"provider_accepted"|"delivered"|"failed"|"unknown"|"simulated"}
PatientFollowUpOut{id, title, provider_name|null, location|null, start_at|null,
                   status:"proposed"|"calendar_event_created"|"confirmed_by_hospital"|"cancelled"|"failed",
                   availability_checked:bool}
```
Patients never receive: triage level/reasons, risk category/score, agent rationale, audit events,
staff notes, review items, other audiences' messages, internal ids of other patients.

### 4.4 Demo endpoints (`APP_MODE=demo` only; otherwise 404)

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/demo/state` | – | `{sim_now, sim_offset_hours, tools:[ToolStatusOut], accounts:[{user_id, role, display_name, episode_id\|null}]}` |
| `POST /api/demo/login-as` | `{user_id}` | `MeOut` + cookie (session `via=demo`) |
| `POST /api/demo/advance` | `{hours: 6\|24}` | `{sim_now, ran:int, skipped_stale:int, awaiting_approval:int, notes:[str]}` |
| `GET /api/demo/outbox` | – | `[{id, kind, to_masked, body, mode, status, created_at}]` simulated messages incl. OTP codes |
| `POST /api/demo/episodes/{id}/patient-response` | `{preset:"all_good"\|"concern"\|"red_flag"}` | `PatientCheckInOut` (submits the due check-in via the normal service path) |
| `POST /api/demo/reset` | `{confirm:"RESET"}` | `{ok, notice:"Local demo data and the simulated clock were reset. Messages already sent and calendar events already created by live integrations were NOT undone."}` |

### 4.5 Aggregates
```
OverviewOut {role, counts:{urgent_reviews, open_reviews, pending_approvals, overdue_checkins,
             failed_actions, unknown_outcomes, unassigned, drafts, awaiting_review, active},
             urgent_reviews:[ReviewItemOut], pending_approvals:[ActionOut], awaiting_review:[EpisodeRow],
             drafts:[EpisodeRow], needs_follow_up:[EpisodeRow], failed_actions:[ActionOut],
             unassigned_reviews:[ReviewItemOut]}
SettingsOut {app_mode, demo_mode, hospital_name, emergency_number, display_timezone, sim_now,
             sim_offset_hours, llm:{provider, mode, model|null, configured},
             tools:[ToolStatusOut], demo_phone_redirect:{active, masked|null},
             worker:{enabled, last_tick_at|null}}
```

## 5. State machines

### 5.1 Episode
`draft → awaiting_review` (nurse submit; draft plan generated) · `awaiting_review → draft` (doctor
return, or nurse edit) · `awaiting_review → active` (doctor activate) · `active →
awaiting_closure_review` (worker at discharge_at+96 h: summary generated, `monitoring_ended_at` set,
closure ReviewItem for doctor) · `awaiting_closure_review → closed` (doctor; blocked while urgent
review items are open) · any non-closed → `cancelled`.

### 5.2 Plan version & step
Plan: `draft → active → superseded`; `draft → discarded`. Exactly one `active` per episode after
activation; before activation, the newest `draft` is current. Step status mirrors its task:
`planned → (blocked|awaiting_approval) → ready → running → executed|failed|unknown`;
non-terminal → `skipped|superseded|cancelled`.

### 5.3 Proposed action
`approval_state`: `pending → approved|rejected|routine|invalidated`; `approved|routine → invalidated`
(payload edit, recipient/context change, superseded step, routine revoked → back to `pending`).
`execution_state`: `not_started → running → succeeded|failed|unknown`; `unknown → succeeded|failed`
(reconcile); `failed → not_started` (doctor retry). `cancelled` when rejected/superseded.

### 5.4 Scheduled task
`scheduled → awaiting_approval|ready`; `ready → running` (**atomic claim**:
`UPDATE ... SET status='running', claimed_by, lease_expires_at WHERE id=? AND status='ready'`,
rowcount must be 1); `running → succeeded|failed|unknown`; `failed → ready` (only if result was
`retryable` and attempts < max_attempts); non-terminal → `cancelled|superseded|skipped`.
Task is `skipped` (reason recorded) when `clock.now() > expires_at` (missed freshness window).

### 5.5 Check-in (patient-facing receipt is derived, see §4.3)
`upcoming → due → submitted|missed|cancelled`. On submit: `processing_state received →
processing → processed|failed`; `staff_review_state not_requested|awaiting → reviewed`.

### 5.6 Episode summary (stored in `episode.summary`)
`{generated_at, window:{start,end}, completed_steps:[...], missed_or_skipped:[...], checkins:[...],
escalations:[...], unresolved_review_items:[...], messages:{by_status}, followups:[...],
facts_used:[ids], statement:"The 96-hour monitoring window has ended. This is not a medical clearance."}`

## 6. Approval policy and execution guarantees (Agent B implements, A calls)

**Requirement by action** (deterministic, `app/policy/approval.py`):
* `send_message` with template `checkin_reminder_v1` or `closing_v1` to **patient** → `routine_eligible`.
* Every other `send_message` (welcome/medication info, replies, safety advice), anything to a
  **caregiver**, `create_calendar_event`, `care_team_alert` → `explicit`.
* `open_checkin`, `close_checkin`, `medication_review`, `staff_review`, `generate_summary` → `none` (internal).

**Routine coverage** (fail-closed: any missing/unknown field ⇒ not covered; checked when proposed AND again immediately before execution): RA active,
`now < expires_at`, same episode, action_type ∈ action_types, template_id ∈ template_ids,
recipient_role ∈ recipient_roles, step offset ∈ offsets_hours, payload equals the rendered template
for this episode (exact allowed content), recipient equals the episode's current registered number,
and covered sends in the trailing 24 h < max_per_day. Otherwise → needs explicit approval.

**Binding**: `payload_hash = sha256(canonical_json({episode_id, action_type, recipient_role,
intended_recipient, effective_recipient, expected_mode, channel, template_id, payload, step_key, due_at}))`.
The worker re-derives recipient, mode, channel and caregiver consent before execution; any
difference invalidates the approval and re-proposes the action as pending (fail closed). A decision is valid only while its
`payload_hash` equals the action's current hash. Edit-approve: new payload → `payload_version+1`, old
decisions `valid=False`, new decision bound to the new hash.

**Invalidation**: replan keeps an approval only if the action is byte-identical (same hash) and its
step survives; otherwise old action `invalidated` (reason recorded) and a new pending action is
proposed. Contact changes re-render recipients → hash changes → invalidated. Revoking a routine
authorisation moves uncovered future actions back to `pending` (explicit approval needed).
Rejecting one action ≠ revoking routine authorisation (separate endpoints, separate audit events).
Changing the internal plan never cancels an already-created external calendar event.

**Executor pre-flight** (immediately before calling a provider, inside the claim transaction):
episode `active`; step belongs to the episode's current plan version and is not superseded/cancelled;
dependencies executed; approval valid (hash match) or routine coverage re-evaluated true;
execution_state `not_started`; not stale. Then: mark running + insert `ToolExecution(outcome=pending)`
→ **commit** → call adapter outside any transaction → new transaction persists result + domain rows
+ audit event.

**Outcomes**: `success` → succeeded. `failure` + `retryable` → bounded retry with backoff (max 3).
`failure` non-retryable → failed + `ReviewItem(failed_action)`. `unknown` (timeout etc.) → task &
execution `unknown`, `ReviewItem(unknown_outcome)`, **no automatic retry**; staff reconcile.
Idempotency keys: action `ep{episode}-{step_key}-v{payload_version}`; Google Calendar uses a
deterministic event id so a retry cannot double-book.

**Restart recovery** (worker start): `running` tasks with expired lease → if a `pending`
ToolExecution exists for a side-effecting action → mark `unknown` + review item; internal tasks →
back to `ready`.

**Fast-forward**: `clock.advance` then `worker.run_until_idle()`. Approval gates still apply; stale
reminders are skipped (`freshness_hours`), so jumping 24 h sends at most the current reminder, never
a burst. Late registration (discharge in the past) uses the same freshness rule.

**Urgent**: triage `urgent` creates `ReviewItem(urgent_concern, priority=urgent, requires_role=doctor)`
in the same transaction as the triage result (visible immediately), then replans: proposed patient
safety message, care-team alert, optional caregiver message — all `explicit`, priority `urgent`.
Review items track owner, acknowledgement and waiting time.

## 7. Events and SSE

`app.events.record_event(session, kind, title, episode_id=, actor_type=, actor_id=, actor_label=,
detail=, scopes=[...], patient_visible=)` — persisted in the caller's transaction, published after
commit. Kinds (dotted): `episode.created|updated|submitted|activated|returned|closed`,
`extraction.completed`, `fact.corrected|added|rejected|used`, `plan.created|activated|replanned|fallback_used`,
`action.proposed|approved|edited_approved|rejected|invalidated|carried_over`, `routine.granted|revoked`,
`task.scheduled|claimed|succeeded|failed|unknown|skipped|superseded|recovered`, `tool.executed`,
`message.sent|status`, `checkin.opened|submitted|processed|processing_failed|missed|reviewed`,
`review.created|acknowledged|assigned|resolved`, `followup.created|confirmed`, `note.added`,
`invite.sent`, `auth.login|logout|otp_requested|otp_failed`, `clock.advanced`, `demo.reset`.

Wire format from `GET /api/stream`:
```
id: 42
event: update
data: {"id":42,"kind":"action.approved","episode_id":3,"scopes":["actions","approvals","plan"],"at":"2026-11-11T09:30:00Z"}
```
Heartbeat comment `: ping` every 15 s. On reconnect the browser sends `Last-Event-ID`; the server
replays events with `id > Last-Event-ID` that the user may see (max 500), then streams live. Staff
receive notices for assigned episodes (and episode-less system events); patients receive only
`patient_visible` notices for their own episodes, with `kind` replaced by `"update"` and scopes
intersected with `{today, checkins, messages, followups, instructions, clock}`. Clients treat every
notice as "invalidate these query scopes and refetch"; after any reconnect they refetch everything.

## 8. Tools (adapters return `ToolResult`, see `backend/app/tools/base.py`)

| Tool name | Provider live / fallback | Side effect | Notes |
|---|---|---|---|
| `messaging` | Twilio SMS (WhatsApp optional) / simulated | yes | `DEMO_PHONE` redirect; live sends refused to non-DEMO_PHONE unless `ALLOW_LIVE_TO_REAL_RECIPIENTS=true`; Twilio 201 → `provider_accepted` (never "delivered") |
| `calendar` | Google Calendar events.insert / simulated | yes | deterministic event id; result states `availability_checked=false` |
| `medication_info` | openFDA drug label / local table | no | separate local interaction rules; output always carries `limitations`; a missing lookup is "no information found", never "safe" |
| `care_team_email` | SMTP / simulated | yes | |
| `llm` | Anthropic / Gemini / none | no | structured output validated by Pydantic; rule fallback always |

## 9. Clock

`from app.clock import clock` → `clock.now()` (aware UTC, simulated), `clock.real_now()` (security
timers), `clock.advance(session, hours)` / `clock.reset(session)` (demo only; caller commits),
`clock.load(session)` (startup and each worker tick). UI displays Asia/Kolkata, 12-hour times.

## 10. Frontend shared system (lead-owned, `frontend/src/ui`, `frontend/src/lib`)

* Stack: React 19, TypeScript, Vite, Tailwind CSS v4 (`@tailwindcss/vite`), React Router,
  TanStack Query, Framer Motion (subtle, reduced-motion aware), lucide-react icons.
* API: `lib/api.ts` → `api.get/post/put/patch` (credentials included, `X-Requested-With`
  header, throws `ApiError {status, code, message, details}`). Never fetch outside it.
* Types: `lib/types.ts` mirrors §4. Query keys: `lib/queryKeys.ts` (`qk.*`) aligned to SSE scopes.
* Live: `lib/live.tsx` `<LiveProvider>` single EventSource; invalidates by scope; exposes
  `useLiveStatus()` (`connecting|open|reconnecting`).
* Auth: `lib/auth.tsx` `useMe()`, `<RequireRole roles=[...]>`.
* Format: `lib/format.ts` → `fmtDateTime`, `fmtTime`, `fmtRelative`, `fmtTPlus(hours)` ("T+24 h"),
  all Asia/Kolkata 12-hour.
* Components (`ui/`): `Button`, `IconButton`, `Card`, `Badge`, `StatusBadge` (text + icon, never
  colour alone), `PriorityBadge`, `ModeBadge` (LIVE/SIMULATED), `Field`, `TextInput`, `TextArea`,
  `Select`, `Checkbox`, `Dialog`, `Tabs`, `EmptyState`, `ErrorState`, `Spinner`, `Skeleton`,
  `Toast` (`useToast`), `PageHeader`, `KeyValue`, `Banner` (info/warning/urgent).
* Tokens: teal primary actions; amber = attention, red = urgent only; Atkinson Hyperlegible;
  focus ring `2px` teal with offset; touch targets ≥ 44 px on patient screens.

## 11. Backend module interfaces between A and B

* **Services** — `backend/app/services/agent_ops.py` (signatures fixed by lead, bodies by B). A's
  routers authenticate, check permissions, call these, then `session.commit()` and
  `worker.kick()`. Transaction rule: services don't commit unless the docstring says so.
* **Worker** — `backend/app/execution/worker.py` (B): `start_background_worker()`,
  `stop_background_worker()` (A's `main.py` lifespan calls these when `WORKER_ENABLED`),
  `recover_on_startup()`, `run_once() -> dict`, `run_until_idle(max_iterations=200) -> dict`,
  `kick()` (wake the loop now), `last_tick_at() -> datetime|None`.
* **Tools registry** — `backend/app/tools/registry.py` (B): `tool_statuses() -> list[ToolStatus]`,
  `llm_status() -> dict`, `get_messaging()` returning the messaging adapter with
  `effective_recipient(phone) -> str` and `execute({"to","body","channel"}, idempotency_key=) -> ToolResult`.
  A uses the messaging adapter directly ONLY for OTP and invitation SMS (auth/operational, not plan
  actions) and records the `Message` row itself (OTP body redacted unless demo mode).
* **Samples** — `backend/app/samples.py` (B): `SAMPLES: list[dict]` in the `GET /api/samples` shape;
  heart failure is `primary=True`.
* **Seeding** — `backend/app/seed_accounts.py` (A): `seed_staff(session)` creates the provisioned demo
  staff (idempotent). Demo reset (A's route) = wipe all rows, `clock.reset`, `seed_staff`,
  `agent_ops.seed_background_episodes`. Startup: run Alembic `upgrade head`, then seed if empty.
* **Seeded staff** (demo password `demo1234` in demo mode only; production requires
  `SEED_STAFF_PASSWORD` or no seeding): `dr.mehta` Dr. Arjun Mehta (Cardiology; primary demo doctor),
  `dr.iyer` Dr. Kavitha Iyer (General Surgery; NOT assigned to the primary scenario),
  `nurse.asha` Asha Nair (Discharge coordinator; primary demo nurse), `nurse.ravi` Ravi Kumar (Ward nurse).
* **Check-in question template** (`questions` JSON snapshot on CheckIn, B defines the library in
  `backend/app/agent/checkin_templates.py`): `[{"id","text","type":"single_choice"|"yes_no"|"text",
  "options":[{"value","label"}],"required":bool,"help":str|null}]`. Submitted answers are validated
  by A against this snapshot (required present, value ∈ options).
* **Patient-visible facts**: categories `diagnosis, medication, allergy, follow_up, red_flag,
  instruction` with `review_state=confirmed` and `active=True` (set `patient_visible=True` at activation).

## 12. Changes after the evaluator's architecture review (11 Nov 2026)

* `APP_MODE` defaults to **production**; demo must be explicitly enabled (`.env.example` sets it).
* `audit_event` uses SQLite AUTOINCREMENT (ids never reused after a demo reset). SSE clients recreate
  the EventSource with backoff and `?last_event_id=` after HTTP errors, and refetch on recovery.
* Changing a patient's phone revokes invitations, unused codes and patient sessions for the old number.
* `activate_plan` → `409 medication_review_pending` until the draft-stage medication review finishes,
  so the doctor always reviews the post-finding plan version.
* Urgent review items are created from deterministic rules before any LLM call.
* Demo-only fault switch: `GET/POST /api/demo/faults` `{messaging|calendar|care_team_email|medication_info|triage: none|fail|timeout}`.
* `PatientFollowUpOut` adds `calendar_mode`; proposed slot details are hidden from patients until approved.
* Unauthenticated demo outbox shows only sign-in codes and invitations.
