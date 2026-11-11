"""Shared helpers for Agent B's service-level tests (no HTTP; drives services + worker directly)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import db
from app.clock import clock
from app.models import (
    ApprovalState,
    CheckIn,
    CheckInResponse,
    CheckInStatus,
    DischargeDocument,
    DischargeEpisode,
    EpisodeStatus,
    Message,
    Patient,
    ProposedAction,
    Role,
    ScheduledTask,
    StaffAssignment,
    ToolExecution,
    User,
)
from app.samples import get_sample


def make_staff(s):
    doc = User(username="dr.test", display_name="Dr. Test", role=Role.DOCTOR)
    nurse = User(username="nurse.test", display_name="Nurse Test", role=Role.NURSE)
    s.add_all([doc, nurse])
    s.flush()
    return doc, nurse


def make_episode(s, nurse, doctor, key="heart-failure", hours_ago=2, **patient_overrides):
    sm = get_sample(key)
    p = Patient(**{**sm["patient"], **patient_overrides})
    s.add(p)
    s.flush()
    d = clock.now() - timedelta(hours=hours_ago)
    ep = DischargeEpisode(patient_id=p.id, status=EpisodeStatus.DRAFT, discharge_at=d, window_ends_at=d + timedelta(hours=96),
                          risk_inputs=dict(sm["risk_inputs"]), created_by_id=nurse.id, sample_key=key)
    s.add(ep)
    s.flush()
    s.add_all([StaffAssignment(episode_id=ep.id, user_id=doctor.id, role="doctor", assigned_by_id=nurse.id),
               StaffAssignment(episode_id=ep.id, user_id=nurse.id, role="nurse", assigned_by_id=nurse.id),
               DischargeDocument(episode_id=ep.id, text=sm["summary_text"], created_by_id=nurse.id)])
    s.flush()
    return ep


def count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


def run_worker():
    from app.execution import worker

    return worker.run_until_idle()


def setup_active(routine=True, key="heart-failure", hours_ago=2, resolve_med_review=True, **patient_overrides):
    """Intake -> extraction -> draft plan -> med review -> activation. Returns ids."""
    from app.services import agent_ops as ops
    from app.agent.memory import active_facts
    from app.services.actions import routine_offsets_in_plan

    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor, key=key, hours_ago=hours_ago, **patient_overrides)
        ops.run_extraction(s, ep, actor=nurse)
        ep.status = EpisodeStatus.AWAITING_REVIEW
        ops.generate_draft_plan(s, ep, actor=nurse)
        ids = {"episode": ep.id, "doctor": doctor.id, "nurse": nurse.id}
    run_worker()  # medication review (draft stage) -> possible plan v2 (tool_finding)
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, ids["episode"])
        doctor = s.get(User, ids["doctor"])
        facts = active_facts(s, ep.id)
        r = None
        if routine:
            r = {"template_ids": ["checkin_reminder_v1"], "recipient_roles": ["patient"],
                 "offsets_hours": routine_offsets_in_plan(s, ep, ["checkin_reminder_v1"]), "max_per_day": 2}
        ops.activate_plan(s, ep, plan_version_id=ep.current_plan_version_id, confirm_fact_ids=[f.id for f in facts],
                          routine=r, doctor=doctor)
    run_worker()
    if resolve_med_review:
        resolve_reviews("medication_review", ids)
    return ids


def resolve_reviews(kind, ids, note="Reviewed; continue as prescribed"):
    from app.models import ReviewItem, ReviewStatus
    from app.services import agent_ops as ops

    with db.session_scope() as s:
        doctor = s.get(User, ids["doctor"])
        for item in s.scalars(select(ReviewItem).where(ReviewItem.episode_id == ids["episode"], ReviewItem.kind == kind,
                                                       ReviewItem.status != ReviewStatus.RESOLVED)):
            item.status, item.resolved_by_id, item.resolved_at, item.resolution_note = ReviewStatus.RESOLVED, doctor.id, clock.now(), note
            ops.on_review_item_resolved(s, item, actor=doctor)
    run_worker()


def action_by_key(s, episode_id, prefix):
    return s.scalars(select(ProposedAction).where(ProposedAction.episode_id == episode_id,
                                                  ProposedAction.step_key.like(f"{prefix}%"),
                                                  ProposedAction.approval_state.notin_([ApprovalState.INVALIDATED]))
                     .order_by(ProposedAction.id.desc())).first()


def advance(hours):
    from app.services import agent_ops as ops

    with db.session_scope() as s:
        return ops.advance_clock(s, hours)


def submit_checkin(episode_id, preset, submission_id=None):
    """Mimics Agent A's submit route: persist answers, then enqueue processing (same transaction)."""
    from app.services import agent_ops as ops
    from app.models import ProcessingState

    with db.session_scope() as s:
        ci = s.scalars(select(CheckIn).where(CheckIn.episode_id == episode_id, CheckIn.status == CheckInStatus.DUE)
                       .order_by(CheckIn.due_at.desc())).first()
        assert ci is not None, "no due check-in"
        answers, free = ops.preset_checkin_answers(ci, preset)
        qmap = {q["id"]: q for q in ci.questions}
        for a in answers:
            label = next(o["label"] for o in qmap[a["question_id"]]["options"] if o["value"] == a["value"])
            s.add(CheckInResponse(checkin_id=ci.id, question_id=a["question_id"], answer={"value": a["value"], "label": label}))
        ci.free_text, ci.status, ci.submitted_at = free, CheckInStatus.SUBMITTED, clock.now()
        ci.submission_id = submission_id or f"sub-{ci.id}"
        ci.processing_state = ProcessingState.RECEIVED
        ops.enqueue_checkin_processing(s, ci)
        cid = ci.id
    run_worker()
    return cid


def approve(action_id, doctor_id):
    from app.services import agent_ops as ops

    with db.session_scope() as s:
        a = s.get(ProposedAction, action_id)
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, doctor_id))
    return run_worker()


__all__ = ["make_staff", "make_episode", "count", "run_worker", "setup_active", "resolve_reviews", "action_by_key",
           "advance", "submit_checkin", "approve", "Message", "ToolExecution", "ScheduledTask", "pytest"]
