"""Human decisions on proposed actions (Agent B). Called by Agent A's routes after permission checks.
Approvals are bound to the exact payload hash; edits invalidate earlier approvals; reject and
revoke are separate operations. Repeating a completed decision is idempotent."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..clock import clock
from ..events import record_event
from ..models import (
    ApprovalDecision,
    ApprovalState,
    DischargeEpisode,
    EpisodeStatus,
    ExecutionState,
    FollowUpEvent,
    FollowUpStatus,
    Message,
    MessageStatus,
    PlanStep,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
    RoutineAuthorisation,
    RoutineStatus,
    ScheduledTask,
    TaskStatus,
    ToolExecution,
    User,
)
from ..policy import approval as policy

SCOPES = ["actions", "approvals", "plan", "overview", "episode"]


def _label(u: User) -> str:
    return u.display_name


def _load(session: Session, action_id: int) -> ProposedAction:
    a = session.get(ProposedAction, action_id)
    if a is None:
        raise errors.not_found("Action not found.")
    return a


def _check_current(session: Session, a: ProposedAction) -> DischargeEpisode:
    ep = session.get(DischargeEpisode, a.episode_id)
    st = session.get(PlanStep, a.step_id) if a.step_id else None
    urgent_after_window = ep.status == EpisodeStatus.AWAITING_CLOSURE_REVIEW and a.priority == "urgent"
    if ep.status != EpisodeStatus.ACTIVE and not urgent_after_window:
        raise errors.invalid_state("This episode is not active, so actions can't be approved.")
    if st is None or st.plan_version_id != ep.current_plan_version_id:
        raise errors.invalid_state("This action belongs to a superseded plan version and can no longer be approved.")
    return ep


def _open_tasks(session: Session, a: ProposedAction) -> list[ScheduledTask]:
    return list(session.scalars(select(ScheduledTask).where(
        ScheduledTask.action_id == a.id, ScheduledTask.status.notin_(list(TaskStatus.TERMINAL)))))


def _release_tasks(session: Session, a: ProposedAction) -> None:
    from ..agent.plan_store import set_task_status

    for t in _open_tasks(session, a):
        if t.status == TaskStatus.AWAITING_APPROVAL:
            set_task_status(session, t, TaskStatus.SCHEDULED, "approved; waiting to run")


def approve_action(session: Session, action_id: int, *, payload_hash: str, doctor: User) -> ProposedAction:
    a = _load(session, action_id)
    if a.approval_state == ApprovalState.APPROVED:
        d = policy.latest_valid_decision(session, a.id)
        if d is not None and d.payload_hash == payload_hash == a.payload_hash:
            return a  # duplicate click: no new decision, no new execution
        raise errors.conflict("This action was already approved with different content.", code="stale_version",
                              details={"payload_hash": a.payload_hash})
    if a.approval_state != ApprovalState.PENDING:
        raise errors.invalid_state(f"This action is '{a.approval_state}' and can't be approved.",
                                   details={"approval_state": a.approval_state})
    if payload_hash != a.payload_hash:
        raise errors.conflict("The action changed since you opened it. Review the latest version.", code="stale_version",
                              details={"payload_hash": a.payload_hash})
    if policy.action_hash(a) != a.payload_hash:
        raise errors.conflict("Stored payload failed its integrity check; it can't be approved.", code="stale_version")
    ep = _check_current(session, a)
    session.add(ApprovalDecision(action_id=a.id, decision="approved", payload_hash=a.payload_hash,
                                 plan_version_id=ep.current_plan_version_id, decided_by_id=doctor.id))
    a.approval_state = ApprovalState.APPROVED
    _release_tasks(session, a)
    session.flush()
    record_event(session, "action.approved", f"{_label(doctor)} approved: {a.step_key}", episode_id=a.episode_id,
                 actor_type="user", actor_id=doctor.id, actor_label=_label(doctor),
                 detail={"action_id": a.id, "payload_hash": a.payload_hash, "payload_version": a.payload_version},
                 scopes=SCOPES)
    return a


def _validate_edit(a: ProposedAction, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(policy.editable_fields(a.action_type))
    bad = set(payload) - allowed
    if bad:
        raise errors.validation("Some fields can't be edited.", {"fields": [{"field": f, "message": "not editable"} for f in sorted(bad)]})
    new = {**dict(a.payload or {}), **{k: v for k, v in payload.items()}}
    fields = []
    for k in ("body", "subject", "title"):
        if k in payload and (not isinstance(payload[k], str) or not payload[k].strip()):
            fields.append({"field": k, "message": "must not be empty"})
    if "body" in payload and isinstance(payload["body"], str) and len(payload["body"]) > (4000 if a.channel == "email" else 1200):
        fields.append({"field": "body", "message": "too long"})
    if "start_at" in payload:
        try:
            start = datetime.fromisoformat(str(payload["start_at"]).replace("Z", "+00:00"))
            if start.tzinfo is None:
                raise ValueError
            if start <= clock.now():
                fields.append({"field": "start_at", "message": "must be in the future"})
            from datetime import timedelta, timezone

            end = start + timedelta(minutes=30)
            new["start_at"] = start.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            new["end_at"] = end.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            fields.append({"field": "start_at", "message": "must be an ISO date-time with timezone"})
    if fields:
        raise errors.validation("Some fields need attention.", {"fields": fields})
    if isinstance(new.get("body"), str):
        new["body"] = new["body"].strip()
    return new


def edit_and_approve_action(session: Session, action_id: int, *, base_payload_hash: str, payload: dict[str, Any],
                            doctor: User) -> ProposedAction:
    from ..agent.plan_store import _ensure_followup, _next_payload_version

    a = _load(session, action_id)
    if a.execution_state != ExecutionState.NOT_STARTED:
        raise errors.invalid_state("This action has already started or finished; it can't be edited.")
    new_payload = _validate_edit(a, payload)
    if a.approval_state == ApprovalState.APPROVED and new_payload == dict(a.payload or {}):
        d = policy.latest_valid_decision(session, a.id)
        if d is not None and d.payload_hash == a.payload_hash:
            return a  # duplicate submit of the same edit
    if a.approval_state not in (ApprovalState.PENDING, ApprovalState.APPROVED, ApprovalState.ROUTINE):
        raise errors.invalid_state(f"This action is '{a.approval_state}' and can't be edited.")
    if base_payload_hash != a.payload_hash:
        raise errors.conflict("The action changed since you opened it. Review the latest version.", code="stale_version",
                              details={"payload_hash": a.payload_hash})
    ep = _check_current(session, a)
    if new_payload == dict(a.payload or {}):
        if a.approval_state == ApprovalState.PENDING:
            return approve_action(session, action_id, payload_hash=a.payload_hash, doctor=doctor)
    old_hash = a.payload_hash
    for d in session.scalars(select(ApprovalDecision).where(ApprovalDecision.action_id == a.id, ApprovalDecision.valid.is_(True))):
        d.valid, d.invalidated_reason = False, "payload edited"
    a.payload = new_payload
    a.payload_version = _next_payload_version(session, a.episode_id, a.step_key)
    a.idempotency_key = f"ep{a.episode_id}-{a.step_key}-v{a.payload_version}"
    a.routine_authorisation_id = None
    a.payload_hash = policy.action_hash(a)
    session.add(ApprovalDecision(action_id=a.id, decision="edited_approved", payload_hash=a.payload_hash,
                                 previous_payload_hash=old_hash, plan_version_id=ep.current_plan_version_id,
                                 decided_by_id=doctor.id))
    a.approval_state = ApprovalState.APPROVED
    if a.action_type == "create_calendar_event":
        _ensure_followup(session, ep, a, a)
    _release_tasks(session, a)
    session.flush()
    record_event(session, "action.edited_approved", f"{_label(doctor)} edited and approved: {a.step_key}",
                 episode_id=a.episode_id, actor_type="user", actor_id=doctor.id, actor_label=_label(doctor),
                 detail={"action_id": a.id, "previous_payload_hash": old_hash, "payload_hash": a.payload_hash,
                         "payload_version": a.payload_version, "edited_fields": sorted(payload)},
                 scopes=SCOPES + ["followups"], patient_visible=a.action_type == "create_calendar_event")
    return a


def reject_action(session: Session, action_id: int, *, base_payload_hash: str, reason: str, doctor: User) -> ProposedAction:
    from ..agent.plan_store import set_task_status
    from ..agent.replanner import rejected_action_replan

    a = _load(session, action_id)
    if a.approval_state == ApprovalState.REJECTED:
        return a
    if not (reason or "").strip():
        raise errors.validation("A reason is required to reject.", {"fields": [{"field": "reason", "message": "required"}]})
    if a.execution_state != ExecutionState.NOT_STARTED:
        raise errors.invalid_state("This action has already started or finished; it can't be rejected.")
    if a.approval_state not in (ApprovalState.PENDING, ApprovalState.APPROVED, ApprovalState.ROUTINE):
        raise errors.invalid_state(f"This action is '{a.approval_state}' and can't be rejected.")
    if base_payload_hash != a.payload_hash:
        raise errors.conflict("The action changed since you opened it. Review the latest version.", code="stale_version",
                              details={"payload_hash": a.payload_hash})
    ep = session.get(DischargeEpisode, a.episode_id)
    for d in session.scalars(select(ApprovalDecision).where(ApprovalDecision.action_id == a.id, ApprovalDecision.valid.is_(True))):
        d.valid, d.invalidated_reason = False, "action rejected"
    session.add(ApprovalDecision(action_id=a.id, decision="rejected", payload_hash=a.payload_hash,
                                 plan_version_id=ep.current_plan_version_id or a.plan_version_id,
                                 decided_by_id=doctor.id, reason=reason.strip(), valid=True))
    a.approval_state = ApprovalState.REJECTED
    a.execution_state = ExecutionState.CANCELLED
    for t in _open_tasks(session, a):
        set_task_status(session, t, TaskStatus.CANCELLED, f"rejected by {_label(doctor)}: {reason.strip()}")
    fu = session.scalars(select(FollowUpEvent).where(FollowUpEvent.action_id == a.id)).first()
    if fu is not None and fu.status == FollowUpStatus.PROPOSED:
        fu.status = FollowUpStatus.CANCELLED
    session.flush()
    record_event(session, "action.rejected", f"{_label(doctor)} rejected: {a.step_key} ({reason.strip()})",
                 episode_id=a.episode_id, actor_type="user", actor_id=doctor.id, actor_label=_label(doctor),
                 detail={"action_id": a.id, "reason": reason.strip()}, scopes=SCOPES + ["followups"],
                 patient_visible=a.action_type == "create_calendar_event")
    if ep.status == EpisodeStatus.ACTIVE:
        rejected_action_replan(session, ep, a, reason=reason.strip(), doctor_id=doctor.id, doctor_label=_label(doctor))
    return a


def retry_action(session: Session, action_id: int, *, doctor: User) -> ProposedAction:
    a = _load(session, action_id)
    if a.execution_state != ExecutionState.FAILED:
        raise errors.invalid_state("Only a failed action can be retried. Reconcile unknown outcomes first.")
    _check_current(session, a)
    if a.approval_state == ApprovalState.APPROVED:
        ok, why = policy.explicit_approval_valid(session, a)
        if not ok:
            raise errors.invalid_state(f"Approval is no longer valid ({why}).")
    elif a.approval_state != ApprovalState.ROUTINE:
        raise errors.invalid_state(f"This action is '{a.approval_state}'.")
    n = len(session.scalars(select(ScheduledTask.id).where(ScheduledTask.action_id == a.id)).all())
    a.execution_state = ExecutionState.NOT_STARTED
    session.add(ScheduledTask(episode_id=a.episode_id, step_id=a.step_id, action_id=a.id, kind="execute_action",
                              due_at=clock.now(), status=TaskStatus.SCHEDULED,
                              idempotency_key=f"{a.idempotency_key}-retry{n}"))
    st = session.get(PlanStep, a.step_id)
    if st is not None:
        st.status, st.result_summary = "planned", f"retry requested by {_label(doctor)}"
    session.flush()
    record_event(session, "action.approved", f"{_label(doctor)} requested a retry: {a.step_key}", episode_id=a.episode_id,
                 actor_type="user", actor_id=doctor.id, actor_label=_label(doctor), detail={"action_id": a.id, "retry": True},
                 scopes=SCOPES + ["executions"])
    return a


def reconcile_execution(session: Session, execution_id: int, *, outcome: str, note: str, actor: User) -> ToolExecution:
    from ..agent.plan_store import set_task_status

    ex = session.get(ToolExecution, execution_id)
    if ex is None:
        raise errors.not_found("Execution not found.")
    if outcome not in ("success", "failure"):
        raise errors.validation("Outcome must be success or failure.")
    if ex.reconciled_at is not None:
        return ex
    if ex.outcome != "unknown":
        raise errors.invalid_state("Only executions with an unknown outcome can be reconciled.")
    ex.outcome = outcome
    ex.reconciled_by_id, ex.reconciled_at, ex.reconcile_note = actor.id, clock.now(), (note or "").strip()
    ex.data = {**dict(ex.data or {}), "reconciled_from": "unknown"}
    a = session.get(ProposedAction, ex.action_id) if ex.action_id else None
    t = session.get(ScheduledTask, ex.task_id) if ex.task_id else None
    if a is not None:
        a.execution_state = ExecutionState.SUCCEEDED if outcome == "success" else ExecutionState.FAILED
        for m in session.scalars(select(Message).where(Message.action_id == a.id)):
            if m.status == MessageStatus.UNKNOWN:
                m.status = MessageStatus.PROVIDER_ACCEPTED if outcome == "success" else MessageStatus.FAILED
                m.error = f"reconciled by {_label(actor)}: {note}"
                m.status_updated_at = clock.now()
    if t is not None and t.status == TaskStatus.UNKNOWN:
        set_task_status(session, t, TaskStatus.SUCCEEDED if outcome == "success" else TaskStatus.FAILED,
                        f"reconciled by {_label(actor)} as {outcome}")
    for item in session.scalars(select(ReviewItem).where(ReviewItem.source_type == "execution", ReviewItem.source_id == ex.id,
                                                         ReviewItem.status != ReviewStatus.RESOLVED)):
        item.status, item.resolved_at, item.resolved_by_id = ReviewStatus.RESOLVED, clock.now(), actor.id
        item.resolution_note = f"Reconciled as {outcome}: {note}"
    session.flush()
    record_event(session, "tool.executed", f"{_label(actor)} reconciled execution #{ex.id} as {outcome}",
                 episode_id=ex.episode_id, actor_type="user", actor_id=actor.id, actor_label=_label(actor),
                 detail={"execution_id": ex.id, "outcome": outcome, "note": note},
                 scopes=["executions", "actions", "plan", "reviews", "messages", "overview"], patient_visible=True)
    return ex


def revoke_routine(session: Session, routine_id: int, *, reason: str, doctor: User) -> RoutineAuthorisation:
    from ..agent.plan_store import set_task_status

    ra = session.get(RoutineAuthorisation, routine_id)
    if ra is None:
        raise errors.not_found("Routine authorisation not found.")
    if ra.status == RoutineStatus.REVOKED:
        return ra
    if ra.status != RoutineStatus.ACTIVE:
        raise errors.invalid_state(f"This routine authorisation is {ra.status}.")
    if not (reason or "").strip():
        raise errors.validation("A reason is required to revoke.", {"fields": [{"field": "reason", "message": "required"}]})
    ra.status, ra.revoked_by_id, ra.revoked_at, ra.revoke_reason = RoutineStatus.REVOKED, doctor.id, clock.now(), reason.strip()
    moved = 0
    for a in session.scalars(select(ProposedAction).where(ProposedAction.routine_authorisation_id == ra.id,
                                                          ProposedAction.approval_state == ApprovalState.ROUTINE,
                                                          ProposedAction.execution_state == ExecutionState.NOT_STARTED)):
        a.approval_state = ApprovalState.PENDING
        moved += 1
        for t in _open_tasks(session, a):
            if t.status == TaskStatus.READY:
                set_task_status(session, t, TaskStatus.AWAITING_APPROVAL, "routine authorisation revoked; needs approval")
    session.flush()
    record_event(session, "routine.revoked", f"{_label(doctor)} revoked routine authorisation #{ra.id} ({moved} future actions now need approval)",
                 episode_id=ra.episode_id, actor_type="user", actor_id=doctor.id, actor_label=_label(doctor),
                 detail={"routine_id": ra.id, "reason": reason.strip(), "actions_moved_to_pending": moved},
                 scopes=SCOPES + ["routine"])
    return ra


def routine_offsets_in_plan(session: Session, episode: DischargeEpisode, template_ids: list[str]) -> list[float]:
    from ..agent.plan_store import current_plan, steps_of

    pv = current_plan(session, episode)
    if pv is None:
        return []
    return sorted({s.offset_hours for s in steps_of(session, pv)
                   if s.action_type == "send_message" and (s.arguments or {}).get("template_id") in template_ids})


__all__ = ["approve_action", "edit_and_approve_action", "reject_action", "retry_action", "reconcile_execution",
           "revoke_routine", "routine_offsets_in_plan", "Optional"]
