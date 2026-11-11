"""Model -> JSON serializers producing exactly the shapes in docs/CONTRACTS.md §4.

Staff serializers may include internal detail; patient serializers (prefix `p_`) must never
expose triage levels, risk, agent rationale, audit events, staff notes or other audiences.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth.security import mask_phone
from ..clock import clock, hours_between, iso
from ..config import settings
from ..models import (
    ApprovalDecision,
    ApprovalState,
    AuditEvent,
    CheckIn,
    CheckInResponse,
    CheckInStatus,
    DischargeDocument,
    DischargeEpisode,
    EpisodeStatus,
    ExecutionState,
    FactUsage,
    FollowUpEvent,
    Invitation,
    Message,
    MessageStatus,
    Patient,
    PatientFact,
    PlanStatus,
    PlanStep,
    PlanVersion,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
    Role,
    RoutineAuthorisation,
    ScheduledTask,
    StaffAssignment,
    StaffNote,
    StaffReviewState,
    StepStatus,
    ToolExecution,
    User,
)

PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2}
EDITABLE_FIELDS = {
    "send_message": ["body"],
    "care_team_alert": ["subject", "body"],
    "create_calendar_event": ["title", "start_at", "location", "notes"],
}
PATIENT_FACT_CATEGORIES = ("diagnosis", "medication", "allergy", "follow_up", "red_flag", "instruction")


# --------------------------------------------------------------------------- helpers
class Names:
    """Per-request cache of user display names."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._cache: dict[int, Optional[User]] = {}

    def user(self, user_id: Optional[int]) -> Optional[User]:
        if user_id is None:
            return None
        if user_id not in self._cache:
            self._cache[user_id] = self.session.get(User, user_id)
        return self._cache[user_id]

    def name(self, user_id: Optional[int]) -> Optional[str]:
        u = self.user(user_id)
        return u.display_name if u else None


def fact_text(value: dict[str, Any] | None) -> str:
    v = value or {}
    if "text" in v and isinstance(v.get("text"), str):
        return v["text"]
    if "name" in v:  # medication
        parts = [v.get("name") or "", v.get("dose") or "", v.get("frequency") or ""]
        s = " ".join(p for p in parts if p).strip()
        if v.get("instructions"):
            s += f" — {v['instructions']}"
        return s
    if "title" in v:  # follow-up
        s = v.get("title") or ""
        if v.get("provider"):
            s += f" ({v['provider']})"
        if v.get("within_days"):
            s += f" within {v['within_days']} days"
        return s
    return ", ".join(f"{k}: {val}" for k, val in v.items())


def hours_since(ep: DischargeEpisode, now: Optional[datetime] = None) -> float:
    return round(hours_between(ep.discharge_at, now or clock.now()), 2)


# --------------------------------------------------------------------------- users
def user_out(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "role": u.role,
        "display_name": u.display_name,
        "username": u.username,
        "specialty": u.specialty,
        "patient_id": u.patient_id,
    }


def me_out(u: User, via: str) -> dict[str, Any]:
    return {
        "user": user_out(u),
        "session_via": via,
        "demo_mode": settings.demo_mode,
        "sim_now": iso(clock.now()),
        "display_timezone": settings.display_timezone,
    }


def team_member(u: Optional[User]) -> Optional[dict[str, Any]]:
    if u is None:
        return None
    return {"user_id": u.id, "display_name": u.display_name, "role": u.role, "specialty": u.specialty}


def team_of(session: Session, episode_id: int, names: Names) -> dict[str, Any]:
    rows = session.scalars(
        select(StaffAssignment).where(StaffAssignment.episode_id == episode_id, StaffAssignment.active.is_(True))
    ).all()
    out: dict[str, Any] = {"doctor": None, "nurse": None}
    for r in rows:
        if r.role in out and out[r.role] is None:
            out[r.role] = team_member(names.user(r.user_id))
    return out


def patient_out(p: Patient) -> dict[str, Any]:
    return {
        "id": p.id,
        "full_name": p.full_name,
        "age": p.age,
        "sex": p.sex,
        "phone_e164": p.phone_e164,
        "preferred_language": p.preferred_language,
        "caregiver_name": p.caregiver_name,
        "caregiver_relation": p.caregiver_relation,
        "caregiver_phone_e164": p.caregiver_phone_e164,
        "caregiver_authorised": bool(p.caregiver_authorised),
        "hospital_number": p.hospital_number,
    }


# --------------------------------------------------------------------------- episodes
def current_plan(session: Session, ep: DischargeEpisode) -> Optional[PlanVersion]:
    if ep.current_plan_version_id:
        pv = session.get(PlanVersion, ep.current_plan_version_id)
        if pv is not None:
            return pv
    return session.scalars(
        select(PlanVersion)
        .where(PlanVersion.episode_id == ep.id, PlanVersion.status.in_((PlanStatus.DRAFT, PlanStatus.ACTIVE)))
        .order_by(PlanVersion.version.desc())
    ).first()


def _pending_actions_q(ep: DischargeEpisode):  # noqa: ANN202
    q = select(ProposedAction).where(
        ProposedAction.episode_id == ep.id,
        ProposedAction.approval_state == ApprovalState.PENDING,
        ProposedAction.execution_state == ExecutionState.NOT_STARTED,
    )
    if ep.current_plan_version_id:
        q = q.where(ProposedAction.plan_version_id == ep.current_plan_version_id)
    return q


def episode_counts(session: Session, ep: DischargeEpisode, now: datetime) -> dict[str, int]:
    active_reviews = (ReviewStatus.OPEN, ReviewStatus.ACKNOWLEDGED)
    urgent = session.scalar(
        select(func.count(ReviewItem.id)).where(
            ReviewItem.episode_id == ep.id, ReviewItem.status.in_(active_reviews), ReviewItem.priority == "urgent"
        )
    ) or 0
    open_reviews = session.scalar(
        select(func.count(ReviewItem.id)).where(ReviewItem.episode_id == ep.id, ReviewItem.status.in_(active_reviews))
    ) or 0
    pending = session.scalar(select(func.count()).select_from(_pending_actions_q(ep).subquery())) or 0
    overdue = 0
    for c in session.scalars(select(CheckIn).where(CheckIn.episode_id == ep.id, CheckIn.kind == "scheduled")).all():
        if c.status == CheckInStatus.MISSED or (
            c.status == CheckInStatus.DUE and c.available_until is not None and c.available_until < now
        ):
            overdue += 1
    failed = session.scalar(
        select(func.count(ProposedAction.id)).where(
            ProposedAction.episode_id == ep.id,
            ProposedAction.execution_state.in_((ExecutionState.FAILED, ExecutionState.UNKNOWN)),
        )
    ) or 0
    return {
        "urgent_reviews": urgent,
        "open_reviews": open_reviews,
        "pending_approvals": pending,
        "overdue_checkins": overdue,
        "failed_actions": failed,
    }


def next_step(session: Session, ep: DischargeEpisode) -> Optional[dict[str, Any]]:
    pv = current_plan(session, ep)
    if pv is None:
        return None
    st = session.scalars(
        select(PlanStep)
        .where(
            PlanStep.plan_version_id == pv.id,
            PlanStep.status.in_(
                (StepStatus.PLANNED, StepStatus.BLOCKED, StepStatus.AWAITING_APPROVAL, StepStatus.READY, StepStatus.RUNNING)
            ),
        )
        .order_by(PlanStep.due_at, PlanStep.seq)
    ).first()
    return {"title": st.title, "due_at": iso(st.due_at)} if st else None


def episode_row(session: Session, ep: DischargeEpisode, names: Names, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or clock.now()
    p = session.get(Patient, ep.patient_id)
    pv = current_plan(session, ep)
    return {
        "id": ep.id,
        "patient": {"id": p.id, "full_name": p.full_name, "age": p.age, "sex": p.sex},
        "status": ep.status,
        "discharge_at": iso(ep.discharge_at),
        "window_ends_at": iso(ep.window_ends_at),
        "hours_since_discharge": hours_since(ep, now),
        "risk_category": ep.risk_category,
        "risk_reasons": list(ep.risk_reasons or []),
        "team": team_of(session, ep.id, names),
        "counts": episode_counts(session, ep, now),
        "next_step": next_step(session, ep),
        "current_plan_version": pv.version if pv else None,
        "updated_at": iso(ep.updated_at),
    }


def episode_permissions(session: Session, ep: DischargeEpisode, actor) -> dict[str, bool]:  # noqa: ANN001
    doctor = actor.role == Role.DOCTOR
    nurse = actor.role == Role.NURSE
    pre = ep.status in (EpisodeStatus.DRAFT, EpisodeStatus.AWAITING_REVIEW)
    live = ep.status not in (EpisodeStatus.CLOSED, EpisodeStatus.CANCELLED)
    return {
        "can_edit_draft": pre and (doctor or nurse),
        "can_submit": nurse and ep.status == EpisodeStatus.DRAFT,
        "can_activate": doctor and ep.status == EpisodeStatus.AWAITING_REVIEW,
        "can_decide_actions": doctor and ep.status == EpisodeStatus.ACTIVE,
        "can_revoke_routine": doctor and ep.status == EpisodeStatus.ACTIVE,
        "can_close": doctor and ep.status == EpisodeStatus.AWAITING_CLOSURE_REVIEW,
        "can_assign_team": nurse and live,
        "can_add_note": (doctor or nurse) and live,
        "can_ack_reviews": doctor or nurse,
    }


def episode_detail(session: Session, ep: DischargeEpisode, actor, names: Optional[Names] = None) -> dict[str, Any]:  # noqa: ANN001
    names = names or Names(session)
    row = episode_row(session, ep, names)
    p = session.get(Patient, ep.patient_id)
    doc = session.scalars(
        select(DischargeDocument).where(DischargeDocument.episode_id == ep.id).order_by(DischargeDocument.id.desc())
    ).first()
    facts = session.scalars(
        select(PatientFact).where(PatientFact.episode_id == ep.id, PatientFact.active.is_(True)).order_by(PatientFact.id)
    ).all()
    ras = session.scalars(
        select(RoutineAuthorisation).where(RoutineAuthorisation.episode_id == ep.id).order_by(RoutineAuthorisation.id.desc())
    ).all()
    inv = session.scalars(
        select(Invitation).where(Invitation.episode_id == ep.id).order_by(Invitation.id.desc())
    ).first()
    row.update(
        {
            "patient": patient_out(p),
            "document": {"id": doc.id, "text": doc.text, "created_at": iso(doc.created_at)} if doc else None,
            "facts": [fact_out(f, names) for f in facts],
            "extraction_method": ep.extraction_method,
            "extraction_notes": list(ep.extraction_notes or []),
            "risk_inputs": dict(ep.risk_inputs or {}),
            "routine_authorisations": [routine_out(r, names) for r in ras],
            "activated_at": iso(ep.activated_at),
            "activated_by": names.name(ep.activated_by_id),
            "monitoring_ended_at": iso(ep.monitoring_ended_at),
            "closed_at": iso(ep.closed_at),
            "closure_note": ep.closure_note,
            "invitation": (
                {"status": inv.status, "created_at": iso(inv.created_at), "expires_at": iso(inv.expires_at)} if inv else None
            ),
            "row_version": ep.row_version,
            "permissions": episode_permissions(session, ep, actor),
        }
    )
    return row


def fact_out(f: PatientFact, names: Names) -> dict[str, Any]:
    return {
        "id": f.id,
        "category": f.category,
        "label": f.label,
        "value": dict(f.value or {}),
        "source_kind": f.source_kind,
        "source_ref": f.source_ref,
        "source_excerpt": f.source_excerpt,
        "extraction_method": f.extraction_method,
        "is_missing": bool(f.is_missing),
        "review_state": f.review_state,
        "reviewed_by": names.name(f.reviewed_by_id),
        "reviewed_at": iso(f.reviewed_at),
        "patient_visible": bool(f.patient_visible),
        "supersedes_id": f.supersedes_id,
        "active": bool(f.active),
        "created_by": f.created_by,
        "created_at": iso(f.created_at),
    }


# --------------------------------------------------------------------------- plans
def plan_version_out(session: Session, pv: PlanVersion, names: Names, include_steps: bool = True) -> dict[str, Any]:
    validation = dict(pv.validation or {})
    validation.setdefault("llm_attempted", False)
    validation.setdefault("llm_valid", None)
    validation.setdefault("fallback_used", pv.source == "rules_fallback")
    validation.setdefault("errors", [])
    out = {
        "id": pv.id,
        "version": pv.version,
        "status": pv.status,
        "source": pv.source,
        "trigger": pv.trigger,
        "trigger_ref": pv.trigger_ref,
        "rationale": pv.rationale,
        "change_summary": list(pv.change_summary or []),
        "validation": validation,
        "facts_used": list(pv.facts_used or []),
        "based_on_version_id": pv.based_on_version_id,
        "created_by": pv.created_by,
        "created_at": iso(pv.created_at),
        "activated_at": iso(pv.activated_at),
        "activated_by": names.name(pv.activated_by_id),
        "steps": [],
    }
    if include_steps:
        steps = session.scalars(
            select(PlanStep).where(PlanStep.plan_version_id == pv.id).order_by(PlanStep.offset_hours, PlanStep.seq)
        ).all()
        out["steps"] = [step_out(session, s) for s in steps]
    return out


def step_out(session: Session, s: PlanStep) -> dict[str, Any]:
    action = session.scalars(
        select(ProposedAction).where(ProposedAction.step_id == s.id).order_by(ProposedAction.id.desc())
    ).first()
    task = session.scalars(
        select(ScheduledTask).where(ScheduledTask.step_id == s.id).order_by(ScheduledTask.id.desc())
    ).first()
    return {
        "id": s.id,
        "step_key": s.step_key,
        "seq": s.seq,
        "title": s.title,
        "purpose": s.purpose,
        "action_type": s.action_type,
        "arguments": dict(s.arguments or {}),
        "offset_hours": s.offset_hours,
        "due_at": iso(s.due_at),
        "freshness_hours": s.freshness_hours,
        "depends_on": list(s.depends_on or []),
        "approval_requirement": s.approval_requirement,
        "rationale": s.rationale,
        "origin": s.origin,
        "status": s.status,
        "status_reason": task.status_reason if task else None,
        "executed_at": iso(s.executed_at),
        "result_summary": s.result_summary,
        "action": (
            {"id": action.id, "approval_state": action.approval_state, "execution_state": action.execution_state,
             "priority": action.priority}
            if action
            else None
        ),
        "task": (
            {"id": task.id, "status": task.status, "attempts": task.attempts, "status_reason": task.status_reason}
            if task
            else None
        ),
    }


# --------------------------------------------------------------------------- actions
def decision_out(d: ApprovalDecision, names: Names) -> dict[str, Any]:
    return {
        "id": d.id,
        "decision": d.decision,
        "payload_hash": d.payload_hash,
        "decided_by": names.name(d.decided_by_id),
        "decided_at": iso(d.decided_at),
        "reason": d.reason,
        "valid": bool(d.valid),
        "invalidated_reason": d.invalidated_reason,
    }


def execution_out(e: ToolExecution, names: Names) -> dict[str, Any]:
    return {
        "id": e.id,
        "task_id": e.task_id,
        "action_id": e.action_id,
        "tool": e.tool,
        "provider": e.provider,
        "mode": e.mode,
        "attempt": e.attempt,
        "outcome": e.outcome,
        "provider_ref": e.provider_ref,
        "provider_status": e.provider_status,
        "delivery_status": e.delivery_status,
        "safe_error": e.safe_error,
        "started_at": iso(e.started_at),
        "finished_at": iso(e.finished_at),
        "reconciled_by": names.name(e.reconciled_by_id),
        "reconciled_at": iso(e.reconciled_at),
        "reconcile_note": e.reconcile_note,
    }


def action_out(session: Session, a: ProposedAction, names: Names) -> dict[str, Any]:
    ep = session.get(DischargeEpisode, a.episode_id)
    p = session.get(Patient, ep.patient_id)
    pv = session.get(PlanVersion, a.plan_version_id)
    step = session.get(PlanStep, a.step_id) if a.step_id else None
    facts = []
    for fid in a.supporting_fact_ids or []:
        f = session.get(PatientFact, fid)
        if f is not None:
            facts.append({"id": f.id, "label": f.label, "text": fact_text(f.value), "source_ref": f.source_ref})
    decisions = session.scalars(
        select(ApprovalDecision).where(ApprovalDecision.action_id == a.id).order_by(ApprovalDecision.id)
    ).all()
    last_exec = session.scalars(
        select(ToolExecution).where(ToolExecution.action_id == a.id).order_by(ToolExecution.id.desc())
    ).first()
    return {
        "id": a.id,
        "episode_id": a.episode_id,
        "patient": {"id": p.id, "full_name": p.full_name},
        "plan_version": pv.version if pv else None,
        "plan_version_id": a.plan_version_id,
        "step_key": a.step_key,
        "step_title": step.title if step else a.step_key,
        "action_type": a.action_type,
        "recipient_role": a.recipient_role,
        "intended_recipient": a.intended_recipient,
        "effective_recipient": a.effective_recipient,
        "redirected": (a.effective_recipient or "") != (a.intended_recipient or ""),
        "channel": a.channel,
        "template_id": a.template_id,
        "payload": dict(a.payload or {}),
        "payload_hash": a.payload_hash,
        "payload_version": a.payload_version,
        "editable_fields": EDITABLE_FIELDS.get(a.action_type, []),
        "expected_mode": a.expected_mode,
        "priority": a.priority,
        "reason": a.reason,
        "supporting_facts": facts,
        "approval_requirement": a.approval_requirement,
        "approval_state": a.approval_state,
        "execution_state": a.execution_state,
        "routine_authorisation_id": a.routine_authorisation_id,
        "invalidated_reason": a.invalidated_reason,
        "due_at": iso(a.due_at),
        "created_at": iso(a.created_at),
        "decisions": [decision_out(d, names) for d in decisions],
        "last_execution": execution_out(last_exec, names) if last_exec else None,
    }


def sort_actions(actions: Iterable[ProposedAction]) -> list[ProposedAction]:
    """Urgent first, then due time, then id — a stable order so cards don't jump around."""
    return sorted(actions, key=lambda a: (PRIORITY_RANK.get(a.priority, 3), a.due_at, a.id))


def routine_out(r: RoutineAuthorisation, names: Names) -> dict[str, Any]:
    return {
        "id": r.id,
        "status": r.status,
        "action_types": list(r.action_types or []),
        "template_ids": list(r.template_ids or []),
        "recipient_roles": list(r.recipient_roles or []),
        "offsets_hours": list(r.offsets_hours or []),
        "max_per_day": r.max_per_day,
        "expires_at": iso(r.expires_at),
        "scope_summary": r.scope_summary,
        "plan_version_id": r.plan_version_id,
        "granted_by": names.name(r.granted_by_id),
        "granted_at": iso(r.granted_at),
        "revoked_by": names.name(r.revoked_by_id),
        "revoked_at": iso(r.revoked_at),
        "revoke_reason": r.revoke_reason,
    }


# --------------------------------------------------------------------------- check-ins, messages, follow-ups
def _question_map(c: CheckIn) -> dict[str, dict[str, Any]]:
    return {q.get("id"): q for q in (c.questions or []) if isinstance(q, dict)}


def checkin_answers(session: Session, c: CheckIn) -> list[dict[str, Any]]:
    qmap = _question_map(c)
    rows = session.scalars(select(CheckInResponse).where(CheckInResponse.checkin_id == c.id).order_by(CheckInResponse.id)).all()
    out = []
    for r in rows:
        q = qmap.get(r.question_id, {})
        out.append(
            {
                "question_id": r.question_id,
                "question_text": q.get("text", r.question_id),
                "value": (r.answer or {}).get("value"),
                "label": (r.answer or {}).get("label"),
            }
        )
    return out


def checkin_out(session: Session, c: CheckIn, names: Names) -> dict[str, Any]:
    return {
        "id": c.id,
        "kind": c.kind,
        "title": c.title,
        "template_id": c.template_id,
        "due_at": iso(c.due_at),
        "available_until": iso(c.available_until),
        "status": c.status,
        "submitted_at": iso(c.submitted_at),
        "processing_state": c.processing_state,
        "triage_level": c.triage_level,
        "triage_reasons": list(c.triage_reasons or []),
        "triage_source": c.triage_source,
        "staff_review_state": c.staff_review_state,
        "reviewed_by": names.name(c.reviewed_by_id),
        "reviewed_at": iso(c.reviewed_at),
        "review_note": c.review_note,
        "answers": checkin_answers(session, c),
        "free_text": c.free_text,
        "response_message_id": c.response_message_id,
    }


def message_out(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "direction": m.direction,
        "kind": m.kind,
        "audience": m.audience,
        "channel": m.channel,
        "subject": m.subject,
        "body": m.body,
        "patient_visible": bool(m.patient_visible),
        "intended_recipient": m.intended_recipient,
        "effective_recipient": m.effective_recipient,
        "mode": m.mode,
        "status": m.status,
        "provider_ref": m.provider_ref,
        "error": m.error,
        "created_at": iso(m.created_at),
        "sent_at": iso(m.sent_at),
        "status_updated_at": iso(m.status_updated_at),
        "action_id": m.action_id,
    }


def followup_out(f: FollowUpEvent, names: Names) -> dict[str, Any]:
    return {
        "id": f.id,
        "title": f.title,
        "provider_name": f.provider_name,
        "location": f.location,
        "start_at": iso(f.start_at),
        "end_at": iso(f.end_at),
        "status": f.status,
        "availability_checked": bool(f.availability_checked),
        "calendar_mode": f.calendar_mode,
        "external_ref": f.external_ref,
        "external_link": f.external_link,
        "confirmed_by": names.name(f.confirmed_by_id),
        "confirmed_at": iso(f.confirmed_at),
        "notes": f.notes,
    }


def review_out(session: Session, r: ReviewItem, names: Names, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or clock.now()
    ep = session.get(DischargeEpisode, r.episode_id)
    p = session.get(Patient, ep.patient_id)
    end = r.acknowledged_at or r.resolved_at or now
    owner = names.user(r.owner_id)
    return {
        "id": r.id,
        "episode_id": r.episode_id,
        "patient": {"id": p.id, "full_name": p.full_name},
        "kind": r.kind,
        "priority": r.priority,
        "title": r.title,
        "summary": r.summary,
        "detail": dict(r.detail or {}),
        "source_type": r.source_type,
        "source_id": r.source_id,
        "requires_role": r.requires_role,
        "status": r.status,
        "owner": {"id": owner.id, "display_name": owner.display_name} if owner else None,
        "created_at": iso(r.created_at),
        "acknowledged_at": iso(r.acknowledged_at),
        "acknowledged_by": names.name(r.acknowledged_by_id),
        "resolved_at": iso(r.resolved_at),
        "resolved_by": names.name(r.resolved_by_id),
        "resolution_note": r.resolution_note,
        "waiting_minutes": max(0, int((end - r.created_at).total_seconds() // 60)),
    }


def sort_reviews(items: Iterable[ReviewItem]) -> list[ReviewItem]:
    return sorted(items, key=lambda r: (PRIORITY_RANK.get(r.priority, 3), r.created_at, r.id))


def audit_out(e: AuditEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "kind": e.kind,
        "title": e.title,
        "actor_type": e.actor_type,
        "actor_label": e.actor_label,
        "detail": dict(e.detail or {}),
        "created_at": iso(e.created_at),
    }


def note_out(n: StaffNote, names: Names) -> dict[str, Any]:
    return {
        "id": n.id,
        "author": names.name(n.author_id),
        "body": n.body,
        "created_at": iso(n.created_at),
        "review_item_id": n.review_item_id,
    }


def usage_out(u: FactUsage) -> dict[str, Any]:
    return {
        "fact_id": u.fact_id,
        "used_for": u.used_for,
        "ref_type": u.ref_type,
        "ref_id": u.ref_id,
        "note": u.note,
        "created_at": iso(u.created_at),
    }


# =========================================================================== patient-facing (p_*)
def p_episode_out(session: Session, ep: DischargeEpisode, names: Names) -> dict[str, Any]:
    now = clock.now()
    team = team_of(session, ep.id, names)
    care_team = []
    if team["doctor"]:
        care_team.append({"display_name": team["doctor"]["display_name"], "role_label": "Doctor"})
    if team["nurse"]:
        care_team.append({"display_name": team["nurse"]["display_name"], "role_label": "Nurse"})
    return {
        "id": ep.id,
        "status": ep.status,
        "discharge_at": iso(ep.discharge_at),
        "window_ends_at": iso(ep.window_ends_at),
        "hours_since_discharge": max(0.0, hours_since(ep, now)),
        "monitoring_active": ep.status == EpisodeStatus.ACTIVE and now < ep.window_ends_at,
        "hospital_name": settings.hospital_name,
        "hospital_phone": settings.hospital_phone,
        "emergency_number": settings.emergency_number,
        "care_team": care_team,
    }


def _p_staff_review(c: CheckIn) -> str:
    return {
        StaffReviewState.AWAITING: "awaiting",
        StaffReviewState.REVIEWED: "reviewed",
    }.get(c.staff_review_state, "not_requested")


def _p_processing(c: CheckIn) -> str:
    return {
        "processing": "processing",
        "processed": "complete",
        "failed": "failed",
    }.get(c.processing_state, "not_started")


def p_checkin_out(session: Session, c: CheckIn) -> dict[str, Any]:
    response_available = False
    if c.response_message_id:
        m = session.get(Message, c.response_message_id)
        response_available = bool(
            m and m.patient_visible and m.status not in (MessageStatus.DRAFT, MessageStatus.QUEUED, MessageStatus.FAILED)
        )
    is_open = c.status == CheckInStatus.DUE
    answers = [{"question_id": a["question_id"], "question_text": a["question_text"], "label": a["label"]}
               for a in checkin_answers(session, c)]
    draft = dict(c.draft or {}) if is_open else None
    return {
        "id": c.id,
        "kind": c.kind,
        "title": c.title,
        "due_at": iso(c.due_at),
        "available_until": iso(c.available_until),
        "status": c.status,
        "questions": [
            {
                "id": q.get("id"),
                "text": q.get("text"),
                "type": q.get("type"),
                "options": list(q.get("options") or []),
                "required": bool(q.get("required", True)),
                "help": q.get("help"),
            }
            for q in (c.questions or [])
        ]
        if is_open
        else None,
        "draft": ({"answers": dict(draft.get("answers") or {}), "free_text": draft.get("free_text")} if draft else None),
        "submitted_at": iso(c.submitted_at),
        "answers": answers,
        "free_text": c.free_text if c.submitted_at else None,
        "receipt": {
            "received": c.submitted_at is not None,
            "received_at": iso(c.submitted_at),
            "agent_processing": _p_processing(c) if c.submitted_at else "not_started",
            "staff_review": _p_staff_review(c),
            "staff_reviewed_at": iso(c.reviewed_at) if c.staff_review_state == StaffReviewState.REVIEWED else None,
            "response_available": response_available,
            "response_message_id": c.response_message_id if response_available else None,
        },
    }


def p_message_out(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "kind": m.kind,
        "body": m.body,
        "channel": m.channel,
        "created_at": iso(m.created_at),
        "sent_at": iso(m.sent_at),
        "status": m.status,
    }


def p_followup_out(f: FollowUpEvent) -> dict[str, Any]:
    return {
        "id": f.id,
        "title": f.title,
        "provider_name": f.provider_name,
        "location": f.location,
        "start_at": iso(f.start_at),
        "status": f.status,
        "availability_checked": bool(f.availability_checked),
    }


def p_visible_messages_q(ep_id: int):  # noqa: ANN202
    return (
        select(Message)
        .where(
            Message.episode_id == ep_id,
            Message.patient_visible.is_(True),
            Message.audience == "patient",
            Message.status != MessageStatus.DRAFT,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
    )


def p_instructions_out(session: Session, ep: DischargeEpisode, names: Names) -> dict[str, Any]:
    reviewed = ep.activated_at is not None and ep.status not in (EpisodeStatus.DRAFT, EpisodeStatus.AWAITING_REVIEW)
    out: dict[str, Any] = {
        "episode_id": ep.id,
        "reviewed": reviewed,
        "reviewed_by": None,
        "reviewed_at": None,
        "plan_version": None,
        "diagnosis": [],
        "medicines": [],
        "allergies": [],
        "warning_signs": [],
        "follow_up": [],
        "other": [],
        "source_note": "Your care team has not finished reviewing your discharge instructions yet. "
        "Until they do, follow the paper instructions you were given at discharge.",
    }
    if not reviewed:
        return out
    pv = current_plan(session, ep)
    facts = session.scalars(
        select(PatientFact)
        .where(
            PatientFact.episode_id == ep.id,
            PatientFact.active.is_(True),
            PatientFact.patient_visible.is_(True),
            PatientFact.review_state == "confirmed",
            PatientFact.category.in_(PATIENT_FACT_CATEGORIES),
        )
        .order_by(PatientFact.id)
    ).all()
    reviewer = names.name(ep.activated_by_id)
    for f in facts:
        v = dict(f.value or {})
        if f.category == "medication" and not f.is_missing:
            out["medicines"].append(
                {
                    "name": v.get("name") or f.label,
                    "dose": v.get("dose") or None,
                    "frequency": v.get("frequency") or None,
                    "instructions": v.get("instructions") or None,
                    "source_ref": f.source_ref,
                }
            )
            continue
        text = "Not provided in your discharge summary" if f.is_missing else fact_text(v)
        item = {"text": text, "source_ref": f.source_ref}
        key = {
            "diagnosis": "diagnosis",
            "allergy": "allergies",
            "red_flag": "warning_signs",
            "follow_up": "follow_up",
        }.get(f.category, "other")
        out[key].append(item)
    out.update(
        {
            "reviewed_by": reviewer,
            "reviewed_at": iso(ep.activated_at),
            "plan_version": pv.version if pv else None,
            "source_note": f"From your discharge summary, reviewed by {reviewer or 'your doctor'}. "
            "If anything here differs from what your doctor told you, call the hospital.",
        }
    )
    return out
