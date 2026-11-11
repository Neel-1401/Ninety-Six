"""Persistence model (SQLAlchemy 2.0). LEAD-OWNED CONTRACT — request changes via the lead.

Conventions
- All datetimes are timezone-aware UTC in Python (UTCDateTime stores naive UTC in SQLite).
- Domain timestamps come from `app.clock.clock.now()` (shared simulated clock), never datetime.now().
- JSON columns: always REASSIGN a new dict/list, never mutate in place (no MutableDict tracking).
- Status values are the string constants in the *Status / *State classes below. docs/CONTRACTS.md
  holds the allowed transitions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .clock import clock


class UTCDateTime(TypeDecorator):
    """Aware-UTC in Python, naive-UTC in SQLite. Rejects naive datetimes on write."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime passed to UTCDateTime; use clock.now() / aware UTC")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Optional[datetime], dialect):  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return clock.now()


def _real_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


# --------------------------------------------------------------------------- enums
class Role:
    DOCTOR = "doctor"
    NURSE = "nurse"
    PATIENT = "patient"
    STAFF = (DOCTOR, NURSE)


class EpisodeStatus:
    DRAFT = "draft"  # nurse preparing; extraction editable
    AWAITING_REVIEW = "awaiting_review"  # submitted; draft plan waiting for doctor
    ACTIVE = "active"  # doctor activated a plan; monitoring window running
    AWAITING_CLOSURE_REVIEW = "awaiting_closure_review"  # 96 h elapsed; summary waiting for doctor
    CLOSED = "closed"  # doctor completed closure review (NOT medical clearance)
    CANCELLED = "cancelled"


class FactSourceKind:
    EXTRACTED = "extracted"  # from the discharge document
    STAFF_ENTERED = "staff_entered"
    STAFF_CORRECTED = "staff_corrected"
    PATIENT_REPORTED = "patient_reported"
    INFERRED = "inferred"  # derived by agent logic (e.g. risk category)
    TOOL = "tool"  # e.g. medication information lookup


class FactReviewState:
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"  # doctor confirmed at activation (or later review)
    REJECTED = "rejected"


class PlanStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"  # replaced before it was ever activated


class StepStatus:
    PLANNED = "planned"  # in the plan, not yet due
    BLOCKED = "blocked"  # dependency not met
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    RUNNING = "running"
    EXECUTED = "executed"
    FAILED = "failed"
    UNKNOWN = "unknown"  # provider outcome uncertain; needs reconciliation
    SKIPPED = "skipped"  # missed freshness window / not applicable
    SUPERSEDED = "superseded"  # replaced by a later plan version
    CANCELLED = "cancelled"


class ApprovalRequirement:
    NONE = "none"  # internal, no external side effect
    ROUTINE_ELIGIBLE = "routine_eligible"  # may be covered by a RoutineAuthorisation
    EXPLICIT = "explicit"  # always needs an individual doctor approval


class ApprovalState:
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"  # bound to ApprovalDecision.payload_hash
    ROUTINE = "routine"  # covered by RoutineAuthorisation (checked again at execution)
    REJECTED = "rejected"
    INVALIDATED = "invalidated"  # payload/context changed or step superseded


class ExecutionState:
    NOT_STARTED = "not_started"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class TaskStatus:
    SCHEDULED = "scheduled"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    SKIPPED = "skipped"
    TERMINAL = (SUCCEEDED, FAILED, UNKNOWN, CANCELLED, SUPERSEDED, SKIPPED)


class RoutineStatus:
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CheckInStatus:
    UPCOMING = "upcoming"
    DUE = "due"
    SUBMITTED = "submitted"
    MISSED = "missed"
    CANCELLED = "cancelled"


class ProcessingState:
    NOT_STARTED = "not_started"
    RECEIVED = "received"  # persisted; triage queued
    PROCESSING = "processing"
    PROCESSED = "processed"  # agent processing complete (NOT staff review)
    FAILED = "failed"  # answers preserved; will retry / staff can see raw answers


class StaffReviewState:
    NOT_REQUESTED = "not_requested"
    AWAITING = "awaiting"
    REVIEWED = "reviewed"  # a named staff member reviewed it


class MessageStatus:
    DRAFT = "draft"
    QUEUED = "queued"
    PROVIDER_ACCEPTED = "provider_accepted"  # provider took it; NOT proof of delivery
    DELIVERED = "delivered"  # only when the provider reports delivery
    FAILED = "failed"
    UNKNOWN = "unknown"
    SIMULATED = "simulated"  # recorded only; nothing left the server


class FollowUpStatus:
    PROPOSED = "proposed"
    CALENDAR_EVENT_CREATED = "calendar_event_created"  # NOT a confirmed hospital appointment
    CONFIRMED_BY_HOSPITAL = "confirmed_by_hospital"  # explicitly recorded by staff
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus:
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class Mode:
    LIVE = "live"
    SIMULATED = "simulated"


# --------------------------------------------------------------------------- accounts
class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(80), unique=True)  # staff only
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))  # staff only
    phone_e164: Mapped[Optional[str]] = mapped_column(String(20), index=True)  # patients
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient.id"), index=True)
    specialty: Mapped[Optional[str]] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo_account: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_login_at: Mapped[Optional[datetime]] = mapped_column()


class AuthSession(Base):
    __tablename__ = "auth_session"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the cookie token
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    via: Mapped[str] = mapped_column(String(16))  # password | otp | demo
    created_at: Mapped[datetime] = mapped_column(default=_real_now)  # REAL time (security)
    expires_at: Mapped[datetime] = mapped_column()
    last_seen_at: Mapped[Optional[datetime]] = mapped_column()
    revoked_at: Mapped[Optional[datetime]] = mapped_column()
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))


class Invitation(Base):
    """Hospital-initiated patient invitation. The link token alone grants nothing."""

    __tablename__ = "invitation"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|accepted|revoked|expired
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=_real_now)
    expires_at: Mapped[datetime] = mapped_column()
    accepted_at: Mapped[Optional[datetime]] = mapped_column()


class OtpChallenge(Base):
    """One-time sign-in code. Security timers use REAL time, not the simulated clock."""

    __tablename__ = "otp_challenge"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))  # None => unregistered phone
    invitation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invitation.id"))
    code_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(default=_real_now)
    expires_at: Mapped[datetime] = mapped_column()
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    consumed_at: Mapped[Optional[datetime]] = mapped_column()
    invalidated_at: Mapped[Optional[datetime]] = mapped_column()
    delivery_mode: Mapped[Optional[str]] = mapped_column(String(16))  # live|simulated|none


class RateLimitHit(Base):
    __tablename__ = "rate_limit_hit"
    id: Mapped[int] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(String(40), index=True)  # e.g. otp_request_phone, otp_verify_ip
    key: Mapped[str] = mapped_column(String(128), index=True)
    at: Mapped[datetime] = mapped_column(default=_real_now, index=True)


# --------------------------------------------------------------------------- clinical episode
class Patient(Base):
    __tablename__ = "patient"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    age: Mapped[Optional[int]] = mapped_column(Integer)
    sex: Mapped[Optional[str]] = mapped_column(String(16))
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    preferred_language: Mapped[str] = mapped_column(String(32), default="English")
    caregiver_name: Mapped[Optional[str]] = mapped_column(String(120))
    caregiver_relation: Mapped[Optional[str]] = mapped_column(String(60))
    caregiver_phone_e164: Mapped[Optional[str]] = mapped_column(String(20))
    caregiver_authorised: Mapped[bool] = mapped_column(Boolean, default=False)  # authorised contact
    hospital_number: Mapped[Optional[str]] = mapped_column(String(40))  # fictional MRN
    created_at: Mapped[datetime] = mapped_column(default=_now)


class DischargeEpisode(Base):
    __tablename__ = "discharge_episode"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=EpisodeStatus.DRAFT, index=True)
    discharge_at: Mapped[datetime] = mapped_column()  # ACTUAL discharge time; 96 h window starts here
    window_ends_at: Mapped[datetime] = mapped_column()  # discharge_at + 96 h
    current_plan_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plan_version.id", use_alter=True, name="fk_episode_current_plan")
    )
    risk_category: Mapped[Optional[str]] = mapped_column(String(16))  # low|medium|high (demo prioritisation)
    risk_reasons: Mapped[list[Any]] = mapped_column(default=list)
    risk_inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    extraction_method: Mapped[Optional[str]] = mapped_column(String(32))  # llm:<name>|rules|rules_fallback
    extraction_notes: Mapped[list[Any]] = mapped_column(default=list)  # e.g. ambiguities, fallback reason
    sample_key: Mapped[Optional[str]] = mapped_column(String(40))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
    submitted_at: Mapped[Optional[datetime]] = mapped_column()
    activated_at: Mapped[Optional[datetime]] = mapped_column()
    activated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    monitoring_ended_at: Mapped[Optional[datetime]] = mapped_column()
    closed_at: Mapped[Optional[datetime]] = mapped_column()
    closed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    closure_note: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)  # 96-hour episode summary
    row_version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic concurrency for edits


class StaffAssignment(Base):
    __tablename__ = "staff_assignment"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # doctor | nurse
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    assigned_at: Mapped[datetime] = mapped_column(default=_now)
    ended_at: Mapped[Optional[datetime]] = mapped_column()


class DischargeDocument(Base):
    __tablename__ = "discharge_document"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="discharge_summary")
    text: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=_now)


class PatientFact(Base):
    """Memory. Every fact keeps value, source, timestamp, review state and provenance.

    category: diagnosis | allergy | medication | follow_up | red_flag | social | risk_input |
              instruction | medication_info | symptom | adherence | note | missing
    value: JSON; medication => {"name","dose","frequency","instructions"}; follow_up =>
           {"title","provider","within_days","location"}; others => {"text": ...}.
    A "not provided" field is a fact with is_missing=True and value {"text": "not provided"}.
    Corrections create a NEW fact with supersedes_id set and mark the old one active=False.
    """

    __tablename__ = "patient_fact"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(160))
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source_kind: Mapped[str] = mapped_column(String(24))
    source_ref: Mapped[Optional[str]] = mapped_column(String(80))  # "document:3#L12", "checkin:7", "tool:openfda"
    source_excerpt: Mapped[Optional[str]] = mapped_column(Text)  # exact text the fact came from
    extraction_method: Mapped[Optional[str]] = mapped_column(String(32))  # llm:<name> | rules
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    review_state: Mapped[str] = mapped_column(String(16), default=FactReviewState.UNREVIEWED)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column()
    patient_visible: Mapped[bool] = mapped_column(Boolean, default=False)  # only after review
    supersedes_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_fact.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str] = mapped_column(String(40), default="agent")  # "agent" | "user:<id>" | "patient"
    created_at: Mapped[datetime] = mapped_column(default=_now)


class FactUsage(Base):
    """Which facts informed which decision (planning, triage, summary, message)."""

    __tablename__ = "fact_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("patient_fact.id"), index=True)
    used_for: Mapped[str] = mapped_column(String(24))  # planning|replanning|triage|summary|message
    ref_type: Mapped[str] = mapped_column(String(24))  # plan_version|checkin|action|summary
    ref_id: Mapped[Optional[int]] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=_now)


# --------------------------------------------------------------------------- planning
class PlanVersion(Base):
    __tablename__ = "plan_version"
    __table_args__ = (UniqueConstraint("episode_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=PlanStatus.DRAFT)
    source: Mapped[str] = mapped_column(String(32))  # llm:<name> | rules | rules_fallback
    trigger: Mapped[str] = mapped_column(String(32))  # initial|tool_finding|patient_response|staff_correction|rejected_action|manual
    trigger_ref: Mapped[Optional[str]] = mapped_column(String(80))
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    change_summary: Mapped[list[Any]] = mapped_column(default=list)  # [{"change": added|removed|changed|kept, "step_key", "description"}]
    validation: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"llm_attempted", "llm_valid", "errors": [...]}
    facts_used: Mapped[list[Any]] = mapped_column(default=list)  # fact ids
    based_on_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plan_version.id"))
    created_by: Mapped[str] = mapped_column(String(40), default="agent")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    activated_at: Mapped[Optional[datetime]] = mapped_column()
    activated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))


class PlanStep(Base):
    """action_type: send_message | create_calendar_event | care_team_alert | open_checkin |
    medication_review | staff_review | generate_summary"""

    __tablename__ = "plan_step"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("plan_version.id"), index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(64))  # stable identity across versions, e.g. "checkin_24h"
    seq: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    purpose: Mapped[str] = mapped_column(Text, default="")
    action_type: Mapped[str] = mapped_column(String(32))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)  # validated against the tool schema
    offset_hours: Mapped[float] = mapped_column(Float)  # hours after discharge_at (0-96)
    due_at: Mapped[datetime] = mapped_column()
    freshness_hours: Mapped[Optional[float]] = mapped_column(Float)  # skip if not run within due+freshness
    depends_on: Mapped[list[Any]] = mapped_column(default=list)  # step_keys
    approval_requirement: Mapped[str] = mapped_column(String(20))
    rationale: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(24), default="initial")
    status: Mapped[str] = mapped_column(String(24), default=StepStatus.PLANNED)
    carried_from_step_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plan_step.id"))
    executed_at: Mapped[Optional[datetime]] = mapped_column()
    result_summary: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ProposedAction(Base):
    """An external side-effecting action with its EXACT payload and recipient."""

    __tablename__ = "proposed_action"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("plan_version.id"), index=True)
    step_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plan_step.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(32))  # send_message|create_calendar_event|care_team_alert
    recipient_role: Mapped[str] = mapped_column(String(16))  # patient|caregiver|care_team|calendar
    intended_recipient: Mapped[str] = mapped_column(String(160))
    effective_recipient: Mapped[str] = mapped_column(String(160))  # after DEMO_PHONE redirect
    channel: Mapped[str] = mapped_column(String(16))  # sms|whatsapp|email|calendar
    template_id: Mapped[Optional[str]] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"body"} | {"subject","body"} | {"title","start_at",...}
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_version: Mapped[int] = mapped_column(Integer, default=1)
    expected_mode: Mapped[str] = mapped_column(String(16))  # live|simulated at proposal time
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # urgent|high|normal
    reason: Mapped[str] = mapped_column(Text, default="")  # why the agent proposed it
    supporting_fact_ids: Mapped[list[Any]] = mapped_column(default=list)
    approval_requirement: Mapped[str] = mapped_column(String(20))
    approval_state: Mapped[str] = mapped_column(String(16), default=ApprovalState.PENDING, index=True)
    execution_state: Mapped[str] = mapped_column(String(16), default=ExecutionState.NOT_STARTED)
    routine_authorisation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("routine_authorisation.id"))
    invalidated_reason: Mapped[Optional[str]] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ApprovalDecision(Base):
    __tablename__ = "approval_decision"
    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("proposed_action.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # approved | edited_approved | rejected
    payload_hash: Mapped[str] = mapped_column(String(64))  # exact payload the decision is bound to
    previous_payload_hash: Mapped[Optional[str]] = mapped_column(String(64))  # for edits
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("plan_version.id"))
    decided_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    decided_at: Mapped[datetime] = mapped_column(default=_now)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)  # False once invalidated
    invalidated_reason: Mapped[Optional[str]] = mapped_column(Text)


class RoutineAuthorisation(Base):
    """Doctor-granted, bounded permission for routine actions (exact templates, recipients,
    schedule, episode, expiry). Anything outside the scope needs an individual approval."""

    __tablename__ = "routine_authorisation"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("plan_version.id"))
    action_types: Mapped[list[Any]] = mapped_column(default=list)  # ["send_message"]
    template_ids: Mapped[list[Any]] = mapped_column(default=list)  # ["checkin_reminder_v1"]
    recipient_roles: Mapped[list[Any]] = mapped_column(default=list)  # ["patient"]
    offsets_hours: Mapped[list[Any]] = mapped_column(default=list)  # allowed schedule [12, 24, 48, 72]
    max_per_day: Mapped[int] = mapped_column(Integer, default=3)
    expires_at: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default=RoutineStatus.ACTIVE)
    scope_summary: Mapped[str] = mapped_column(Text, default="")
    granted_by_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    granted_at: Mapped[datetime] = mapped_column(default=_now)
    revoked_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    revoked_at: Mapped[Optional[datetime]] = mapped_column()
    revoke_reason: Mapped[Optional[str]] = mapped_column(Text)


# --------------------------------------------------------------------------- execution
class ScheduledTask(Base):
    """Durable unit of work for the worker.

    kind: execute_action | open_checkin | close_checkin | process_checkin | medication_review |
          generate_summary | end_window
    """

    __tablename__ = "scheduled_task"
    __table_args__ = (Index("ix_task_due", "status", "due_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    step_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plan_step.id"), index=True)
    action_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposed_action.id"), index=True)
    checkin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("check_in.id"))
    kind: Mapped[str] = mapped_column(String(32))
    due_at: Mapped[datetime] = mapped_column()
    expires_at: Mapped[Optional[datetime]] = mapped_column()  # stale after this => skipped
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.SCHEDULED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(64))
    claimed_at: Mapped[Optional[datetime]] = mapped_column()
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column()  # REAL time
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column()
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status_reason: Mapped[Optional[str]] = mapped_column(Text)  # e.g. "skipped: missed freshness window"
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ToolExecution(Base):
    """One attempt to call a tool adapter. Simulated and live attempts are both recorded."""

    __tablename__ = "tool_execution"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scheduled_task.id"), index=True)
    action_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposed_action.id"), index=True)
    tool: Mapped[str] = mapped_column(String(40))  # messaging|calendar|medication_info|care_team_email|summary
    provider: Mapped[str] = mapped_column(String(40))  # twilio|google_calendar|openfda|smtp|local
    mode: Mapped[str] = mapped_column(String(16))  # live|simulated
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(100), index=True)
    request_summary: Mapped[dict[str, Any]] = mapped_column(default=dict)  # redacted
    outcome: Mapped[str] = mapped_column(String(16), default="pending")  # pending|success|failure|unknown
    provider_ref: Mapped[Optional[str]] = mapped_column(String(128))
    provider_status: Mapped[Optional[str]] = mapped_column(String(40))
    delivery_status: Mapped[Optional[str]] = mapped_column(String(32))
    safe_error: Mapped[Optional[str]] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column()
    reconciled_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    reconciled_at: Mapped[Optional[datetime]] = mapped_column()
    reconcile_note: Mapped[Optional[str]] = mapped_column(Text)


# --------------------------------------------------------------------------- patient contact
class CheckIn(Base):
    __tablename__ = "check_in"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    step_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plan_step.id"))
    kind: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled | concern
    title: Mapped[str] = mapped_column(String(120))
    template_id: Mapped[Optional[str]] = mapped_column(String(64))
    template_version: Mapped[Optional[int]] = mapped_column(Integer)
    questions: Mapped[list[Any]] = mapped_column(default=list)  # reviewed snapshot (see CONTRACTS.md)
    due_at: Mapped[datetime] = mapped_column()
    available_until: Mapped[Optional[datetime]] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default=CheckInStatus.UPCOMING, index=True)
    draft: Mapped[dict[str, Any]] = mapped_column(default=dict)
    draft_updated_at: Mapped[Optional[datetime]] = mapped_column()
    submission_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)  # client UUID (dedupe)
    submitted_at: Mapped[Optional[datetime]] = mapped_column()
    free_text: Mapped[Optional[str]] = mapped_column(Text)
    processing_state: Mapped[str] = mapped_column(String(16), default=ProcessingState.NOT_STARTED)
    processing_error: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[Optional[datetime]] = mapped_column()
    triage_level: Mapped[Optional[str]] = mapped_column(String(16))  # ok|concern|urgent (staff-only)
    triage_reasons: Mapped[list[Any]] = mapped_column(default=list)  # staff-only
    triage_source: Mapped[Optional[str]] = mapped_column(String(40))  # rules | llm:<name>+rules
    staff_review_state: Mapped[str] = mapped_column(String(16), default=StaffReviewState.NOT_REQUESTED)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column()
    review_note: Mapped[Optional[str]] = mapped_column(Text)  # staff-only
    response_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("message.id"))
    created_at: Mapped[datetime] = mapped_column(default=_now)


class CheckInResponse(Base):
    __tablename__ = "check_in_response"
    __table_args__ = (UniqueConstraint("checkin_id", "question_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    checkin_id: Mapped[int] = mapped_column(ForeignKey("check_in.id"), index=True)
    question_id: Mapped[str] = mapped_column(String(64))
    answer: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"value": "...", "label": "..."}
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Message(Base):
    """kind: welcome | reminder | clinical | reply | safety | caregiver | care_team_alert | otp | invitation"""

    __tablename__ = "message"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[Optional[int]] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    action_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposed_action.id"))
    direction: Mapped[str] = mapped_column(String(16), default="outbound")  # outbound | inbound
    kind: Mapped[str] = mapped_column(String(24))
    audience: Mapped[str] = mapped_column(String(16))  # patient | caregiver | care_team
    channel: Mapped[str] = mapped_column(String(16))  # sms | whatsapp | email | in_app
    subject: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)  # OTP bodies are redacted unless demo mode
    patient_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    intended_recipient: Mapped[Optional[str]] = mapped_column(String(160))
    effective_recipient: Mapped[Optional[str]] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(16))  # live | simulated
    status: Mapped[str] = mapped_column(String(24), default=MessageStatus.QUEUED)
    provider_ref: Mapped[Optional[str]] = mapped_column(String(128))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    sent_at: Mapped[Optional[datetime]] = mapped_column()
    status_updated_at: Mapped[Optional[datetime]] = mapped_column()


class FollowUpEvent(Base):
    __tablename__ = "follow_up_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    action_id: Mapped[Optional[int]] = mapped_column(ForeignKey("proposed_action.id"))
    source_fact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_fact.id"))
    title: Mapped[str] = mapped_column(String(160))
    provider_name: Mapped[Optional[str]] = mapped_column(String(120))
    location: Mapped[Optional[str]] = mapped_column(String(160))
    start_at: Mapped[Optional[datetime]] = mapped_column()
    end_at: Mapped[Optional[datetime]] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), default=FollowUpStatus.PROPOSED)
    availability_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    calendar_mode: Mapped[Optional[str]] = mapped_column(String(16))  # live | simulated
    external_ref: Mapped[Optional[str]] = mapped_column(String(160))
    external_link: Mapped[Optional[str]] = mapped_column(String(500))
    confirmed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column()
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


# --------------------------------------------------------------------------- staff workflow
class ReviewItem(Base):
    """kind: urgent_concern | concern | failed_action | unknown_outcome | missed_checkin |
    medication_review | processing_failure | closure_review"""

    __tablename__ = "review_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # urgent | high | normal
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source_type: Mapped[Optional[str]] = mapped_column(String(24))  # checkin|action|task|execution|episode
    source_id: Mapped[Optional[int]] = mapped_column(Integer)
    requires_role: Mapped[str] = mapped_column(String(16), default="any")  # any | doctor
    status: Mapped[str] = mapped_column(String(16), default=ReviewStatus.OPEN, index=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column()
    acknowledged_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column()
    resolved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id"))
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)


class StaffNote(Base):
    __tablename__ = "staff_note"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    review_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("review_item.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class AuditEvent(Base):
    """Append-only trace. `id` doubles as the SSE event id (Last-Event-ID recovery)."""

    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[Optional[int]] = mapped_column(ForeignKey("discharge_episode.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user | patient | agent | worker | system
    actor_id: Mapped[Optional[int]] = mapped_column(Integer)
    actor_label: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(48), index=True)  # e.g. "action.approved"
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)  # staff-only
    scopes: Mapped[list[Any]] = mapped_column(default=list)  # query scopes to invalidate
    patient_visible: Mapped[bool] = mapped_column(Boolean, default=False)  # patients get a scrubbed ping
    created_at: Mapped[datetime] = mapped_column(default=_now)  # simulated clock
    real_created_at: Mapped[datetime] = mapped_column(default=_real_now)


class SystemState(Base):
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=_real_now, onupdate=_real_now)
