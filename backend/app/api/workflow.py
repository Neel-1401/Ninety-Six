"""Approval queue, actions, routine authorisations, executions, review items, check-in review,
follow-up confirmation. Visibility (assignment, 404) is checked before role (403)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..auth.deps import assigned_episode_ids, ensure_doctor, episode_for_staff, get_db, is_assigned, require_staff
from ..auth.sessions import Actor
from ..clock import clock
from ..models import (
    ApprovalState,
    CheckIn,
    DischargeEpisode,
    ExecutionState,
    FollowUpEvent,
    FollowUpStatus,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
    Role,
    RoutineAuthorisation,
    StaffAssignment,
    StaffReviewState,
    ToolExecution,
    User,
)
from ..schemas.requests import (
    ApproveIn,
    AssignIn,
    EditApproveIn,
    NoteOnlyIn,
    OptionalNoteIn,
    ReasonIn,
    ReconcileIn,
    RejectIn,
)
from ..schemas.serializers import (
    Names,
    action_out,
    checkin_out,
    execution_out,
    followup_out,
    review_out,
    routine_out,
    sort_actions,
    sort_reviews,
)
from ..services import agent_ops
from .common import audit, kick_worker

router = APIRouter(prefix="/api", tags=["workflow"])


def _call(fn, *args, **kwargs):  # noqa: ANN001, ANN202
    try:
        return fn(*args, **kwargs)
    except NotImplementedError:
        raise errors.ApiError(501, "not_implemented", f"{fn.__name__} is not available yet.")


def _action(session: Session, actor: Actor, action_id: int) -> ProposedAction:
    a = session.get(ProposedAction, action_id)
    if a is None:
        raise errors.not_found("Action not found.")
    episode_for_staff(session, actor, a.episode_id)  # 404 if not assigned
    return a


# --------------------------------------------------------------------------- approvals
@router.get("/approvals")
def approvals(state: str = "pending", session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ids = assigned_episode_ids(session, actor)
    if not ids:
        return []
    names = Names(session)
    eps = {e.id: e for e in session.scalars(select(DischargeEpisode).where(DischargeEpisode.id.in_(ids))).all()}
    q = select(ProposedAction).where(ProposedAction.episode_id.in_(ids))
    rows = session.scalars(q).all()
    if state == "pending":
        rows = [
            a
            for a in rows
            if a.approval_state == ApprovalState.PENDING
            and a.execution_state == ExecutionState.NOT_STARTED
            and (eps[a.episode_id].current_plan_version_id in (None, a.plan_version_id))
        ]
    else:
        rows = sorted(rows, key=lambda a: a.id, reverse=True)[:300]
    return [action_out(session, a, names) for a in sort_actions(rows)]


@router.get("/actions/{action_id}")
def get_action(action_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    a = _action(session, actor, action_id)
    return action_out(session, a, Names(session))


@router.post("/actions/{action_id}/approve")
def approve(action_id: int, body: ApproveIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    a = _action(session, actor, action_id)
    ensure_doctor(actor, "approve actions")
    a = _call(agent_ops.approve_action, session, a.id, payload_hash=body.payload_hash, doctor=actor.user)
    session.commit()
    kick_worker()
    return action_out(session, a, Names(session))


@router.post("/actions/{action_id}/edit-approve")
def edit_approve(action_id: int, body: EditApproveIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    a = _action(session, actor, action_id)
    ensure_doctor(actor, "edit and approve actions")
    a = _call(agent_ops.edit_and_approve_action, session, a.id, base_payload_hash=body.base_payload_hash,
              payload=dict(body.payload), doctor=actor.user)
    session.commit()
    kick_worker()
    return action_out(session, a, Names(session))


@router.post("/actions/{action_id}/reject")
def reject(action_id: int, body: RejectIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    a = _action(session, actor, action_id)
    ensure_doctor(actor, "reject actions")
    a = _call(agent_ops.reject_action, session, a.id, base_payload_hash=body.base_payload_hash, reason=body.reason,
              doctor=actor.user)
    session.commit()
    kick_worker()
    return action_out(session, a, Names(session))


@router.post("/actions/{action_id}/retry")
def retry(action_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    a = _action(session, actor, action_id)
    ensure_doctor(actor, "retry actions")
    a = _call(agent_ops.retry_action, session, a.id, doctor=actor.user)
    session.commit()
    kick_worker()
    return action_out(session, a, Names(session))


@router.post("/executions/{execution_id}/reconcile")
def reconcile(execution_id: int, body: ReconcileIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    e = session.get(ToolExecution, execution_id)
    if e is None:
        raise errors.not_found("Execution not found.")
    episode_for_staff(session, actor, e.episode_id)
    e = _call(agent_ops.reconcile_execution, session, e.id, outcome=body.outcome, note=body.note, actor=actor.user)
    session.commit()
    kick_worker()
    return execution_out(e, Names(session))


@router.post("/routine-authorisations/{routine_id}/revoke")
def revoke_routine(routine_id: int, body: ReasonIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    r = session.get(RoutineAuthorisation, routine_id)
    if r is None:
        raise errors.not_found("Authorisation not found.")
    episode_for_staff(session, actor, r.episode_id)
    ensure_doctor(actor, "revoke routine authorisation")
    r = _call(agent_ops.revoke_routine, session, r.id, reason=body.reason, doctor=actor.user)
    session.commit()
    kick_worker()
    return routine_out(r, Names(session))


# --------------------------------------------------------------------------- review items
def _review(session: Session, actor: Actor, item_id: int) -> ReviewItem:
    r = session.get(ReviewItem, item_id)
    if r is None:
        raise errors.not_found("Review item not found.")
    episode_for_staff(session, actor, r.episode_id)
    return r


@router.get("/review-items")
def review_items(status: str = "active", episode_id: Optional[int] = None, session: Session = Depends(get_db),
                 actor: Actor = Depends(require_staff)):
    ids = assigned_episode_ids(session, actor)
    if episode_id is not None:
        ids = [i for i in ids if i == episode_id]
    if not ids:
        return []
    q = select(ReviewItem).where(ReviewItem.episode_id.in_(ids))
    statuses = {
        "active": (ReviewStatus.OPEN, ReviewStatus.ACKNOWLEDGED),
        "open": (ReviewStatus.OPEN,),
        "acknowledged": (ReviewStatus.ACKNOWLEDGED,),
        "resolved": (ReviewStatus.RESOLVED,),
    }.get(status)
    if statuses:
        q = q.where(ReviewItem.status.in_(statuses))
    names = Names(session)
    now = clock.now()
    return [review_out(session, r, names, now) for r in sort_reviews(session.scalars(q).all())]


@router.post("/review-items/{item_id}/acknowledge")
def acknowledge(item_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    r = _review(session, actor, item_id)
    if r.status == ReviewStatus.OPEN:
        r.status = ReviewStatus.ACKNOWLEDGED
        r.acknowledged_at = clock.now()
        r.acknowledged_by_id = actor.id
        if r.owner_id is None:
            r.owner_id = actor.id
        audit(session, actor, "review.acknowledged", f"Acknowledged: {r.title}", episode_id=r.episode_id,
              detail={"review_item_id": r.id}, scopes=["reviews", "overview", "episode"])
        session.commit()
    return review_out(session, r, Names(session))


@router.post("/review-items/{item_id}/assign")
def assign(item_id: int, body: AssignIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    r = _review(session, actor, item_id)
    if r.status == ReviewStatus.RESOLVED:
        raise errors.invalid_state("This item is already resolved.")
    target = session.get(User, body.user_id)
    if target is None or target.role not in Role.STAFF or not is_assigned(session, _ActorShim(target), r.episode_id):
        raise errors.validation("Route it to a doctor or nurse assigned to this patient.")
    if r.requires_role == Role.DOCTOR and target.role != Role.DOCTOR:
        raise errors.validation("This item needs a doctor.")
    r.owner_id = target.id
    audit(session, actor, "review.assigned", f"Routed to {target.display_name}: {r.title}", episode_id=r.episode_id,
          detail={"review_item_id": r.id, "owner_id": target.id}, scopes=["reviews", "overview", "episode"])
    session.commit()
    return review_out(session, r, Names(session))


class _ActorShim:
    def __init__(self, user: User) -> None:
        self.id = user.id


@router.post("/review-items/{item_id}/resolve")
def resolve(item_id: int, body: NoteOnlyIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    r = _review(session, actor, item_id)
    if r.requires_role == Role.DOCTOR:
        ensure_doctor(actor, "resolve this item")
    if r.status == ReviewStatus.RESOLVED:
        return review_out(session, r, Names(session))
    now = clock.now()
    if r.acknowledged_at is None:
        r.acknowledged_at = now
        r.acknowledged_by_id = actor.id
    if r.owner_id is None:
        r.owner_id = actor.id
    r.status = ReviewStatus.RESOLVED
    r.resolved_at = now
    r.resolved_by_id = actor.id
    r.resolution_note = body.note
    audit(session, actor, "review.resolved", f"Resolved: {r.title}", episode_id=r.episode_id,
          detail={"review_item_id": r.id, "note": body.note}, scopes=["reviews", "overview", "episode", "plan"])
    try:
        agent_ops.on_review_item_resolved(session, r, actor=actor.user)
    except NotImplementedError:
        pass
    session.commit()
    kick_worker()
    return review_out(session, r, Names(session))


# --------------------------------------------------------------------------- check-ins & follow-ups
@router.post("/checkins/{checkin_id}/mark-reviewed")
def mark_reviewed(checkin_id: int, body: NoteOnlyIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    c = session.get(CheckIn, checkin_id)
    if c is None:
        raise errors.not_found("Check-in not found.")
    episode_for_staff(session, actor, c.episode_id)
    if c.submitted_at is None:
        raise errors.invalid_state("This check-in has not been submitted.")
    if c.staff_review_state != StaffReviewState.REVIEWED:
        c.staff_review_state = StaffReviewState.REVIEWED
        c.reviewed_by_id = actor.id
        c.reviewed_at = clock.now()
        c.review_note = body.note
        audit(session, actor, "checkin.reviewed", f"Check-in reviewed by {actor.label}", episode_id=c.episode_id,
              detail={"checkin_id": c.id}, scopes=["checkins", "today", "overview", "episode"], patient_visible=True)
        session.commit()
    return checkin_out(session, c, Names(session))


@router.post("/followups/{followup_id}/confirm")
def confirm_followup(followup_id: int, body: NoteOnlyIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    f = session.get(FollowUpEvent, followup_id)
    if f is None:
        raise errors.not_found("Follow-up not found.")
    episode_for_staff(session, actor, f.episode_id)
    if f.status in (FollowUpStatus.CANCELLED,):
        raise errors.invalid_state("This follow-up was cancelled.")
    if f.status != FollowUpStatus.CONFIRMED_BY_HOSPITAL:
        f.status = FollowUpStatus.CONFIRMED_BY_HOSPITAL
        f.confirmed_by_id = actor.id
        f.confirmed_at = clock.now()
        f.notes = ((f.notes + "\n") if f.notes else "") + f"Confirmed by {actor.label}: {body.note}"
        audit(session, actor, "followup.confirmed", f"Appointment confirmed by the hospital: {f.title}",
              episode_id=f.episode_id, detail={"followup_id": f.id}, scopes=["followups", "today", "episode"],
              patient_visible=True)
        session.commit()
    return followup_out(f, Names(session))
