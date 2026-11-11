// Mirror of docs/CONTRACTS.md §4 (lead-owned). Times are ISO-8601 UTC strings ("...Z").
// If the backend disagrees with this file, report it to the lead instead of patching locally.

export type ISO = string;
export type Role = "doctor" | "nurse" | "patient";
export type Mode = "live" | "simulated";

export interface UserOut {
  id: number;
  role: Role;
  display_name: string;
  username: string | null;
  specialty: string | null;
  patient_id: number | null;
}

export interface MeOut {
  user: UserOut;
  session_via: "password" | "otp" | "demo";
  demo_mode: boolean;
  sim_now: ISO;
  display_timezone: string;
}

export interface TeamMember {
  user_id: number;
  display_name: string;
  role: "doctor" | "nurse";
  specialty: string | null;
}

export interface PatientOut {
  id: number;
  full_name: string;
  age: number | null;
  sex: string | null;
  phone_e164: string;
  preferred_language: string;
  caregiver_name: string | null;
  caregiver_relation: string | null;
  caregiver_phone_e164: string | null;
  caregiver_authorised: boolean;
  hospital_number: string | null;
}

export type EpisodeStatus =
  | "draft"
  | "awaiting_review"
  | "active"
  | "awaiting_closure_review"
  | "closed"
  | "cancelled";

export type RiskCategory = "low" | "medium" | "high";

export interface EpisodeCounts {
  urgent_reviews: number;
  open_reviews: number;
  pending_approvals: number;
  overdue_checkins: number;
  failed_actions: number;
}

export interface EpisodeRow {
  id: number;
  patient: { id: number; full_name: string; age: number | null; sex: string | null };
  status: EpisodeStatus;
  discharge_at: ISO;
  window_ends_at: ISO;
  hours_since_discharge: number;
  risk_category: RiskCategory | null;
  risk_reasons: string[];
  team: { doctor: TeamMember | null; nurse: TeamMember | null };
  counts: EpisodeCounts;
  next_step: { title: string; due_at: ISO } | null;
  current_plan_version: number | null;
  updated_at: ISO;
}

export interface EpisodePermissions {
  can_edit_draft: boolean;
  can_submit: boolean;
  can_activate: boolean;
  can_decide_actions: boolean;
  can_revoke_routine: boolean;
  can_close: boolean;
  can_assign_team: boolean;
  can_add_note: boolean;
  can_ack_reviews: boolean;
}

export type FactSourceKind =
  | "extracted"
  | "staff_entered"
  | "staff_corrected"
  | "patient_reported"
  | "inferred"
  | "tool";

export type FactCategory =
  | "diagnosis"
  | "allergy"
  | "medication"
  | "follow_up"
  | "red_flag"
  | "social"
  | "risk_input"
  | "instruction"
  | "medication_info"
  | "symptom"
  | "adherence"
  | "note"
  | "missing";

export interface MedicationValue {
  name: string;
  dose?: string | null;
  frequency?: string | null;
  instructions?: string | null;
}
export interface FollowUpValue {
  title: string;
  provider?: string | null;
  within_days?: number | null;
  location?: string | null;
}

export interface FactOut {
  id: number;
  category: FactCategory | string;
  label: string;
  value: Record<string, unknown> & { text?: string };
  source_kind: FactSourceKind;
  source_ref: string | null;
  source_excerpt: string | null;
  extraction_method: string | null;
  is_missing: boolean;
  review_state: "unreviewed" | "confirmed" | "rejected";
  reviewed_by: string | null;
  reviewed_at: ISO | null;
  patient_visible: boolean;
  supersedes_id: number | null;
  active: boolean;
  created_by: string;
  created_at: ISO;
}

export interface RoutineOut {
  id: number;
  status: "active" | "revoked" | "expired";
  action_types: string[];
  template_ids: string[];
  recipient_roles: string[];
  offsets_hours: number[];
  max_per_day: number;
  expires_at: ISO;
  scope_summary: string;
  plan_version_id: number;
  granted_by: string;
  granted_at: ISO;
  revoked_by: string | null;
  revoked_at: ISO | null;
  revoke_reason: string | null;
}

export interface EpisodeDetail extends EpisodeRow {
  patient: PatientOut & { age: number | null; sex: string | null };
  document: { id: number; text: string; created_at: ISO } | null;
  facts: FactOut[];
  extraction_method: string | null;
  extraction_notes: string[];
  risk_inputs: Record<string, unknown>;
  routine_authorisations: RoutineOut[];
  activated_at: ISO | null;
  activated_by: string | null;
  monitoring_ended_at: ISO | null;
  closed_at: ISO | null;
  closure_note: string | null;
  invitation: { status: string; created_at: ISO; expires_at: ISO } | null;
  row_version: number;
  permissions: EpisodePermissions;
}

export type StepStatus =
  | "planned"
  | "blocked"
  | "awaiting_approval"
  | "ready"
  | "running"
  | "executed"
  | "failed"
  | "unknown"
  | "skipped"
  | "superseded"
  | "cancelled";

export type ApprovalState =
  | "not_required"
  | "pending"
  | "approved"
  | "routine"
  | "rejected"
  | "invalidated";

export type ExecutionState = "not_started" | "running" | "succeeded" | "failed" | "unknown" | "cancelled";

export type Priority = "urgent" | "high" | "normal";

export interface PlanStepOut {
  id: number;
  step_key: string;
  seq: number;
  title: string;
  purpose: string;
  action_type: string;
  arguments: Record<string, unknown>;
  offset_hours: number;
  due_at: ISO;
  freshness_hours: number | null;
  depends_on: string[];
  approval_requirement: "none" | "routine_eligible" | "explicit";
  rationale: string;
  origin: string;
  status: StepStatus;
  status_reason: string | null;
  executed_at: ISO | null;
  result_summary: string | null;
  action: { id: number; approval_state: ApprovalState; execution_state: ExecutionState; priority: Priority } | null;
  task: { id: number; status: string; attempts: number; status_reason: string | null } | null;
}

export interface PlanChange {
  change: "added" | "removed" | "changed" | "kept";
  step_key: string;
  description: string;
}

export interface PlanVersionOut {
  id: number;
  version: number;
  status: "draft" | "active" | "superseded" | "discarded";
  source: string;
  trigger: string;
  trigger_ref: string | null;
  rationale: string | null;
  change_summary: PlanChange[];
  validation: { llm_attempted: boolean; llm_valid: boolean | null; fallback_used: boolean; errors: string[] };
  facts_used: number[];
  based_on_version_id: number | null;
  created_by: string;
  created_at: ISO;
  activated_at: ISO | null;
  activated_by: string | null;
  steps: PlanStepOut[];
}

export interface DecisionOut {
  id: number;
  decision: "approved" | "edited_approved" | "rejected";
  payload_hash: string;
  decided_by: string;
  decided_at: ISO;
  reason: string | null;
  valid: boolean;
  invalidated_reason: string | null;
}

export interface ExecutionOut {
  id: number;
  task_id: number | null;
  action_id: number | null;
  tool: string;
  provider: string;
  mode: Mode;
  attempt: number;
  outcome: "pending" | "success" | "failure" | "unknown";
  provider_ref: string | null;
  provider_status: string | null;
  delivery_status: string | null;
  safe_error: string | null;
  started_at: ISO;
  finished_at: ISO | null;
  reconciled_by: string | null;
  reconciled_at: ISO | null;
  reconcile_note: string | null;
}

export interface ActionOut {
  id: number;
  episode_id: number;
  patient: { id: number; full_name: string };
  plan_version: number;
  plan_version_id: number;
  step_key: string;
  step_title: string;
  action_type: "send_message" | "create_calendar_event" | "care_team_alert" | string;
  recipient_role: "patient" | "caregiver" | "care_team" | "calendar" | string;
  intended_recipient: string;
  effective_recipient: string;
  redirected: boolean;
  channel: string;
  template_id: string | null;
  payload: Record<string, unknown>;
  payload_hash: string;
  payload_version: number;
  editable_fields: string[];
  expected_mode: Mode;
  priority: Priority;
  reason: string;
  supporting_facts: { id: number; label: string; text: string; source_ref: string | null }[];
  approval_requirement: "none" | "routine_eligible" | "explicit";
  approval_state: ApprovalState;
  execution_state: ExecutionState;
  routine_authorisation_id: number | null;
  invalidated_reason: string | null;
  due_at: ISO;
  created_at: ISO;
  decisions: DecisionOut[];
  last_execution: ExecutionOut | null;
}

export interface CheckInAnswer {
  question_id: string;
  question_text: string;
  value: string;
  label: string;
}

export interface CheckInOut {
  id: number;
  kind: "scheduled" | "concern";
  title: string;
  template_id: string | null;
  due_at: ISO;
  available_until: ISO | null;
  status: "upcoming" | "due" | "submitted" | "missed" | "cancelled";
  submitted_at: ISO | null;
  processing_state: "not_started" | "received" | "processing" | "processed" | "failed";
  triage_level: "ok" | "concern" | "urgent" | null;
  triage_reasons: string[];
  triage_source: string | null;
  staff_review_state: "not_requested" | "awaiting" | "reviewed";
  reviewed_by: string | null;
  reviewed_at: ISO | null;
  review_note: string | null;
  answers: CheckInAnswer[];
  free_text: string | null;
  response_message_id: number | null;
}

export type MessageStatus =
  | "draft"
  | "queued"
  | "provider_accepted"
  | "delivered"
  | "failed"
  | "unknown"
  | "simulated";

export interface MessageOut {
  id: number;
  direction: "outbound" | "inbound";
  kind: string;
  audience: "patient" | "caregiver" | "care_team";
  channel: string;
  subject: string | null;
  body: string;
  patient_visible: boolean;
  intended_recipient: string | null;
  effective_recipient: string | null;
  mode: Mode;
  status: MessageStatus;
  provider_ref: string | null;
  error: string | null;
  created_at: ISO;
  sent_at: ISO | null;
  status_updated_at: ISO | null;
  action_id: number | null;
}

export type FollowUpStatus = "proposed" | "calendar_event_created" | "confirmed_by_hospital" | "failed" | "cancelled";

export interface FollowUpOut {
  id: number;
  title: string;
  provider_name: string | null;
  location: string | null;
  start_at: ISO | null;
  end_at: ISO | null;
  status: FollowUpStatus;
  availability_checked: boolean;
  calendar_mode: Mode | null;
  external_ref: string | null;
  external_link: string | null;
  confirmed_by: string | null;
  confirmed_at: ISO | null;
  notes: string | null;
}

export interface ReviewItemOut {
  id: number;
  episode_id: number;
  patient: { id: number; full_name: string };
  kind: string;
  priority: Priority;
  title: string;
  summary: string;
  detail: Record<string, unknown>;
  source_type: string | null;
  source_id: number | null;
  requires_role: "any" | "doctor";
  status: "open" | "acknowledged" | "resolved";
  owner: { id: number; display_name: string } | null;
  created_at: ISO;
  acknowledged_at: ISO | null;
  acknowledged_by: string | null;
  resolved_at: ISO | null;
  resolved_by: string | null;
  resolution_note: string | null;
  waiting_minutes: number;
}

export interface AuditEventOut {
  id: number;
  kind: string;
  title: string;
  actor_type: "user" | "patient" | "agent" | "worker" | "system";
  actor_label: string;
  detail: Record<string, unknown>;
  created_at: ISO;
}

export interface NoteOut {
  id: number;
  author: string;
  body: string;
  created_at: ISO;
  review_item_id: number | null;
}

export interface FactUsageOut {
  fact_id: number;
  used_for: string;
  ref_type: string;
  ref_id: number | null;
  note: string | null;
  created_at: ISO;
}

export interface MemoryOut {
  facts: FactOut[];
  usage: FactUsageOut[];
}

export interface ToolStatusOut {
  name: string;
  label: string;
  mode: Mode;
  configured: boolean;
  detail: string;
  last_live_success_at: ISO | null;
  last_failure_at: ISO | null;
}

export interface OverviewOut {
  role: Role;
  counts: {
    urgent_reviews: number;
    open_reviews: number;
    pending_approvals: number;
    overdue_checkins: number;
    failed_actions: number;
    unknown_outcomes: number;
    unassigned: number;
    drafts: number;
    awaiting_review: number;
    active: number;
  };
  urgent_reviews: ReviewItemOut[];
  pending_approvals: ActionOut[];
  awaiting_review: EpisodeRow[];
  drafts: EpisodeRow[];
  needs_follow_up: EpisodeRow[];
  failed_actions: ActionOut[];
  unassigned_reviews: ReviewItemOut[];
}

export interface SettingsOut {
  app_mode: "demo" | "production";
  demo_mode: boolean;
  hospital_name: string;
  emergency_number: string;
  display_timezone: string;
  sim_now: ISO;
  sim_offset_hours: number;
  llm: { provider: string; mode: Mode; model: string | null; configured: boolean };
  tools: ToolStatusOut[];
  demo_phone_redirect: { active: boolean; masked: string | null };
  worker: { enabled: boolean; last_tick_at: ISO | null };
}

export interface ClockOut {
  now: ISO;
  sim_offset_hours: number;
  demo_mode: boolean;
  display_timezone: string;
}

export interface SampleOut {
  key: string;
  label: string;
  primary: boolean;
  patient: Partial<PatientOut> & { full_name: string };
  risk_inputs: Record<string, unknown>;
  discharge_hours_ago: number;
  summary_text: string;
  suggested_doctor_username: string | null;
}

// ---------------------------------------------------------------- patient-facing
export interface PatientEpisodeOut {
  id: number;
  status: EpisodeStatus;
  discharge_at: ISO;
  window_ends_at: ISO;
  hours_since_discharge: number;
  monitoring_active: boolean;
  hospital_name: string;
  hospital_phone: string;
  emergency_number: string;
  care_team: { display_name: string; role_label: string }[];
}

export interface Question {
  id: string;
  text: string;
  type: "single_choice" | "yes_no" | "text";
  options: { value: string; label: string }[];
  required: boolean;
  help: string | null;
}

export interface CheckInReceipt {
  received: boolean;
  received_at: ISO | null;
  agent_processing: "not_started" | "processing" | "complete" | "failed";
  staff_review: "not_requested" | "awaiting" | "reviewed";
  staff_reviewed_at: ISO | null;
  response_available: boolean;
  response_message_id: number | null;
}

export interface PatientCheckInOut {
  id: number;
  kind: "scheduled" | "concern";
  title: string;
  due_at: ISO;
  available_until: ISO | null;
  status: "upcoming" | "due" | "submitted" | "missed" | "cancelled";
  questions: Question[] | null;
  draft: { answers: Record<string, string>; free_text: string | null } | null;
  submitted_at: ISO | null;
  answers: { question_id: string; question_text: string; label: string }[];
  receipt: CheckInReceipt;
}

export interface TodayOut {
  episode: PatientEpisodeOut;
  next_action: {
    kind: "checkin" | "instructions" | "followup" | "none";
    title: string;
    description: string;
    checkin_id: number | null;
  };
  due_checkin: PatientCheckInOut | null;
  upcoming_checkin: { title: string; due_at: ISO } | null;
  reminders: { id: number; body: string; sent_at: ISO | null; status: MessageStatus }[];
  progress: { hours_elapsed: number; window_hours: number; ended: boolean };
  monitoring_expectations: string;
  contact: { hospital_phone: string; emergency_number: string };
}

export interface InstructionItem {
  text: string;
  source_ref: string | null;
}

export interface InstructionsOut {
  episode_id: number;
  reviewed: boolean;
  reviewed_by: string | null;
  reviewed_at: ISO | null;
  plan_version: number | null;
  diagnosis: InstructionItem[];
  medicines: { name: string; dose: string | null; frequency: string | null; instructions: string | null; source_ref: string | null }[];
  allergies: InstructionItem[];
  warning_signs: InstructionItem[];
  follow_up: InstructionItem[];
  other: InstructionItem[];
  source_note: string;
}

export interface PatientMessageOut {
  id: number;
  kind: string;
  body: string;
  channel: string;
  created_at: ISO;
  sent_at: ISO | null;
  status: MessageStatus;
}

export interface PatientFollowUpOut {
  id: number;
  title: string;
  provider_name: string | null;
  location: string | null;
  start_at: ISO | null;
  status: FollowUpStatus;
  availability_checked: boolean;
}

// ---------------------------------------------------------------- demo
export interface DemoState {
  sim_now: ISO;
  sim_offset_hours: number;
  tools: ToolStatusOut[];
  accounts: { user_id: number; role: Role; display_name: string; episode_id: number | null }[];
}

export interface DemoAdvanceOut {
  sim_now: ISO;
  ran: number;
  skipped_stale: number;
  awaiting_approval: number;
  notes: string[];
}

export interface DemoOutboxItem {
  id: number;
  kind: string;
  to_masked: string;
  body: string;
  mode: Mode;
  status: MessageStatus;
  created_at: ISO;
}

// SSE notice (CONTRACTS §7)
export interface LiveNotice {
  id: number;
  kind: string;
  episode_id: number | null;
  scopes: string[];
  at: ISO;
}
