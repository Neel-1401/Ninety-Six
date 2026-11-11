"""Agent B: approval binding, execution guarantees, clock behaviour (service level, simulated tools)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import db, errors
from app.clock import clock
from app.execution import worker
from app.models import (
    ApprovalDecision,
    ApprovalState,
    DischargeEpisode,
    ExecutionState,
    Message,
    MessageStatus,
    Patient,
    PlanStep,
    PlanVersion,
    ProposedAction,
    ReviewItem,
    RoutineAuthorisation,
    ScheduledTask,
    TaskStatus,
    ToolExecution,
    User,
)
from app.services import agent_ops as ops
from app.tools import registry
from app.tools.base import ToolResult
from tests.test_agent_helpers import action_by_key, advance, approve, count, run_worker, setup_active


@pytest.fixture(autouse=True)
def _reset_adapters():
    yield
    for n in ("messaging", "calendar", "care_team_email", "medication_info"):
        registry.set_adapter(n, None)


def _msgs(s, action_id):
    return s.scalars(select(Message).where(Message.action_id == action_id)).all()


def test_intake_review_approval_executes_once(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "welcome_message")
        assert a.approval_state == ApprovalState.PENDING and a.execution_state == ExecutionState.NOT_STARTED
        assert "Furosemide 40 mg" in a.payload["body"]  # confirmed facts only
        aid = a.id
    approve(aid, ids["doctor"])
    approve(aid, ids["doctor"])  # duplicate click: idempotent
    for _ in range(5):
        worker.run_once()  # repeated ticks
    with db.session_scope() as s:
        a = s.get(ProposedAction, aid)
        assert a.execution_state == ExecutionState.SUCCEEDED
        assert count(s, ApprovalDecision, ApprovalDecision.action_id == aid) == 1
        assert count(s, ToolExecution, ToolExecution.action_id == aid) == 1
        msgs = _msgs(s, aid)
        assert len(msgs) == 1 and msgs[0].status == MessageStatus.SIMULATED and msgs[0].mode == "simulated"
        assert msgs[0].patient_visible is True


def test_welcome_blocked_until_medication_combination_reviewed(session):
    ids = setup_active(resolve_med_review=False)
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, ids["episode"])
        pv = s.get(PlanVersion, ep.current_plan_version_id)
        assert pv.trigger == "tool_finding"
        assert any(c["step_key"] == "welcome_message" and c["change"] == "changed" for c in pv.change_summary)
        aid = action_by_key(s, ep.id, "welcome_message").id
    approve(aid, ids["doctor"])
    with db.session_scope() as s:
        st = s.scalars(select(PlanStep).where(PlanStep.plan_version_id == s.get(DischargeEpisode, ids["episode"]).current_plan_version_id,
                                              PlanStep.step_key == "welcome_message")).one()
        assert st.status == "blocked" and "Doctor review" in (st.result_summary or "")
        assert count(s, ToolExecution, ToolExecution.action_id == aid) == 0
    from tests.test_agent_helpers import resolve_reviews

    resolve_reviews("medication_review", ids)
    with db.session_scope() as s:
        assert s.get(ProposedAction, aid).execution_state == ExecutionState.SUCCEEDED


def test_rejection_prevents_execution_and_replans(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "book_cardiology")
        ops.reject_action(s, a.id, base_payload_hash=a.payload_hash, reason="Patient already booked privately",
                          doctor=s.get(User, ids["doctor"]))
        aid = a.id
    run_worker()
    with db.session_scope() as s:
        a = s.get(ProposedAction, aid)
        assert a.approval_state == ApprovalState.REJECTED
        assert count(s, ToolExecution, ToolExecution.action_id == aid) == 0
        ep = s.get(DischargeEpisode, ids["episode"])
        pv = s.get(PlanVersion, ep.current_plan_version_id)
        assert pv.trigger == "rejected_action" and pv.change_summary[0]["change"] == "removed"
        with pytest.raises(errors.ApiError):
            ops.approve_action(s, aid, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))


def test_edit_invalidates_prior_approval_and_stale_hash_rejected(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "caregiver_brief")
        doctor = s.get(User, ids["doctor"])
        old_hash = a.payload_hash
        ops.approve_action(s, a.id, payload_hash=old_hash, doctor=doctor)
        ops.edit_and_approve_action(s, a.id, base_payload_hash=old_hash, payload={"body": "Edited by doctor: please call us."},
                                    doctor=doctor)
        assert a.payload_hash != old_hash and a.payload["body"].startswith("Edited by doctor")
        decisions = s.scalars(select(ApprovalDecision).where(ApprovalDecision.action_id == a.id).order_by(ApprovalDecision.id)).all()
        assert [d.valid for d in decisions] == [False, True] and decisions[1].payload_hash == a.payload_hash
        with pytest.raises(errors.ApiError) as e:  # approving with the hash the doctor saw before the edit
            ops.edit_and_approve_action(s, a.id, base_payload_hash=old_hash, payload={"body": "x"}, doctor=doctor)
        assert e.value.code == "stale_version"
        aid = a.id
    run_worker()
    with db.session_scope() as s:
        msgs = _msgs(s, aid)
        assert len(msgs) == 1 and msgs[0].body == "Edited by doctor: please call us."


def test_tampered_payload_is_not_executed(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "caregiver_brief")
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))
        a.payload = {"body": "changed after approval without a decision"}  # bypassing the API
        aid = a.id
    run_worker()
    with db.session_scope() as s:
        assert count(s, ToolExecution, ToolExecution.action_id == aid) == 0
        t = s.scalars(select(ScheduledTask).where(ScheduledTask.action_id == aid)).first()
        assert t.status == TaskStatus.AWAITING_APPROVAL


def test_contact_change_invalidates_approval_and_reproposes(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "caregiver_brief")
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))
        old_id = a.id
        ep = s.get(DischargeEpisode, ids["episode"])
        p = s.get(Patient, ep.patient_id)
        p.caregiver_phone_e164 = "+919000000099"
        ops.on_episode_context_changed(s, ep, changed_fields=["caregiver_phone_e164"], actor=s.get(User, ids["nurse"]))
    run_worker()
    with db.session_scope() as s:
        old = s.get(ProposedAction, old_id)
        assert old.approval_state == ApprovalState.INVALIDATED
        assert count(s, ToolExecution, ToolExecution.action_id == old_id) == 0
        new = action_by_key(s, ids["episode"], "caregiver_brief")
        assert new.id != old_id and new.approval_state == ApprovalState.PENDING
        assert new.intended_recipient == "+919000000099"
        with pytest.raises(errors.ApiError):
            ops.approve_action(s, old_id, payload_hash=old.payload_hash, doctor=s.get(User, ids["doctor"]))


def test_superseded_action_cannot_execute(session):
    from app.agent.replanner import replan

    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "book_blood")
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))
        aid = a.id
        ep = s.get(DischargeEpisode, ids["episode"])
        replan(s, ep, trigger="manual", reason="test removal",
               modify=lambda specs: [x for x in specs if not x.step_key.startswith("book_blood")])
    run_worker()
    with db.session_scope() as s:
        a = s.get(ProposedAction, aid)
        assert a.approval_state == ApprovalState.INVALIDATED
        assert count(s, ToolExecution, ToolExecution.action_id == aid) == 0


def test_fast_forward_skips_stale_reminders_and_respects_approval(session):
    ids = setup_active()  # discharged 2 h ago; reminders at 12/24/48/72 h are routine-covered
    r = advance(24)  # -> T+26 h
    assert r["skipped_stale"] >= 1
    with db.session_scope() as s:
        r12 = action_by_key(s, ids["episode"], "reminder_12h")
        r24 = action_by_key(s, ids["episode"], "reminder_24h")
        assert r12.execution_state == ExecutionState.CANCELLED  # stale -> skipped, never sent late
        assert r24.execution_state == ExecutionState.SUCCEEDED
        welcome = action_by_key(s, ids["episode"], "welcome_message")
        assert welcome.execution_state == ExecutionState.NOT_STARTED  # approval gate not bypassed
        sent = s.scalars(select(Message).where(Message.episode_id == ids["episode"])).all()
        assert [m.kind for m in sent] == ["reminder"]


def test_late_registration_does_not_burst(session):
    ids = setup_active(hours_ago=30)
    with db.session_scope() as s:
        sent = s.scalars(select(Message).where(Message.episode_id == ids["episode"])).all()
        assert sent == []  # 12 h and 24 h reminders are stale
        skipped = s.scalars(select(ScheduledTask).where(ScheduledTask.episode_id == ids["episode"],
                                                        ScheduledTask.status == TaskStatus.SKIPPED)).all()
        assert len(skipped) >= 2


def test_routine_scope_only_covers_listed_offsets(session):
    from app.agent.memory import active_facts
    from tests.test_agent_helpers import make_episode, make_staff

    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor)
        ops.run_extraction(s, ep, actor=nurse)
        ep.status = "awaiting_review"
        ops.generate_draft_plan(s, ep, actor=nurse)
        eid, did = ep.id, doctor.id
    run_worker()
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, eid)
        with pytest.raises(errors.ApiError):  # caregiver can never be in routine scope
            ops.activate_plan(s, ep, plan_version_id=ep.current_plan_version_id,
                              confirm_fact_ids=[f.id for f in active_facts(s, eid)], doctor=s.get(User, did),
                              routine={"template_ids": ["checkin_reminder_v1"], "recipient_roles": ["caregiver"],
                                       "offsets_hours": [24], "max_per_day": 2})
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, eid)
        ops.activate_plan(s, ep, plan_version_id=ep.current_plan_version_id,
                          confirm_fact_ids=[f.id for f in active_facts(s, eid)], doctor=s.get(User, did),
                          routine={"template_ids": ["checkin_reminder_v1"], "recipient_roles": ["patient"],
                                   "offsets_hours": [24], "max_per_day": 2})
    with db.session_scope() as s:
        assert action_by_key(s, eid, "reminder_24h").approval_state == ApprovalState.ROUTINE
        assert action_by_key(s, eid, "reminder_12h").approval_state == ApprovalState.PENDING
        assert action_by_key(s, eid, "closing_message").approval_state == ApprovalState.PENDING
        assert action_by_key(s, eid, "welcome_message").approval_state == ApprovalState.PENDING


def test_activation_requires_current_plan_and_all_facts(session):
    from app.agent.memory import active_facts
    from tests.test_agent_helpers import make_episode, make_staff

    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor)
        ops.run_extraction(s, ep, actor=nurse)
        ep.status = "awaiting_review"
        v1 = ops.generate_draft_plan(s, ep, actor=nurse)
        eid, did, v1_id = ep.id, doctor.id, v1.id
    run_worker()  # medication finding -> draft v2
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, eid)
        facts = active_facts(s, eid)
        with pytest.raises(errors.ApiError) as e:
            ops.activate_plan(s, ep, plan_version_id=v1_id, confirm_fact_ids=[f.id for f in facts], routine=None,
                              doctor=s.get(User, did))
        assert e.value.code == "stale_version"
        with pytest.raises(errors.ApiError) as e:
            ops.activate_plan(s, ep, plan_version_id=ep.current_plan_version_id, confirm_fact_ids=[facts[0].id],
                              routine=None, doctor=s.get(User, did))
        assert e.value.code == "validation_error"


def test_revoked_routine_blocks_future_sends(session):
    ids = setup_active()
    advance(6)
    advance(6)  # -> T+14: 12 h reminder sent under routine authorisation
    with db.session_scope() as s:
        assert action_by_key(s, ids["episode"], "reminder_12h").execution_state == ExecutionState.SUCCEEDED
        ra = s.scalars(select(RoutineAuthorisation).where(RoutineAuthorisation.episode_id == ids["episode"])).one()
        ops.revoke_routine(s, ra.id, reason="Patient asked for no texts", doctor=s.get(User, ids["doctor"]))
    advance(24)  # -> T+38: 24 h reminder would have been due
    with db.session_scope() as s:
        r24 = action_by_key(s, ids["episode"], "reminder_24h")
        assert r24.approval_state == ApprovalState.PENDING
        assert count(s, ToolExecution, ToolExecution.action_id == r24.id) == 0


class _FakeMessaging:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def effective_recipient(self, phone):
        return phone

    def mode_for(self, effective):
        return "live"

    def status(self):
        from app.tools.base import ToolStatus

        return ToolStatus("messaging", "fake", "live", True, "fake")

    def execute(self, request, *, idempotency_key):
        self.calls += 1
        return self.result


def test_provider_failure_stays_failed_and_live(session):
    fake = _FakeMessaging(ToolResult("failure", "live", "twilio", safe_error="Twilio rejected the message (HTTP 400)", retryable=False))
    registry.set_adapter("messaging", fake)
    ids = setup_active()
    with db.session_scope() as s:
        aid = action_by_key(s, ids["episode"], "welcome_message").id
    approve(aid, ids["doctor"])
    for _ in range(3):
        worker.run_once()
    with db.session_scope() as s:
        a = s.get(ProposedAction, aid)
        assert a.execution_state == ExecutionState.FAILED
        ex = s.scalars(select(ToolExecution).where(ToolExecution.action_id == aid)).all()
        assert len(ex) == 1 and ex[0].mode == "live" and ex[0].outcome == "failure"
        assert _msgs(s, aid)[0].status == MessageStatus.FAILED
        assert s.scalars(select(ReviewItem).where(ReviewItem.kind == "failed_action")).first() is not None
    assert fake.calls == 1


def test_unknown_outcome_is_not_retried_blindly(session):
    fake = _FakeMessaging(ToolResult("unknown", "live", "twilio", delivery_status="unknown", safe_error="timeout"))
    registry.set_adapter("messaging", fake)
    ids = setup_active()
    with db.session_scope() as s:
        aid = action_by_key(s, ids["episode"], "welcome_message").id
    approve(aid, ids["doctor"])
    for _ in range(4):
        worker.run_once()
    assert fake.calls == 1
    with db.session_scope() as s:
        a = s.get(ProposedAction, aid)
        assert a.execution_state == ExecutionState.UNKNOWN
        assert _msgs(s, aid)[0].status == MessageStatus.UNKNOWN
        item = s.scalars(select(ReviewItem).where(ReviewItem.kind == "unknown_outcome")).one()
        with pytest.raises(errors.ApiError):
            ops.retry_action(s, aid, doctor=s.get(User, ids["doctor"]))  # must reconcile first
        ex = s.scalars(select(ToolExecution).where(ToolExecution.action_id == aid)).one()
        ops.reconcile_execution(s, ex.id, outcome="failure", note="Not in Twilio logs", actor=s.get(User, ids["nurse"]))
        assert item.status == "resolved"
    registry.set_adapter("messaging", _FakeMessaging(ToolResult("success", "live", "twilio", provider_ref="SM1",
                                                                delivery_status="provider_accepted")))
    with db.session_scope() as s:
        ops.retry_action(s, aid, doctor=s.get(User, ids["doctor"]))
    run_worker()
    with db.session_scope() as s:
        assert s.get(ProposedAction, aid).execution_state == ExecutionState.SUCCEEDED
        assert _msgs(s, aid)[0].status == MessageStatus.PROVIDER_ACCEPTED  # accepted, NOT "delivered"


def test_transient_failure_is_retried_with_bound(session):
    fake = _FakeMessaging(ToolResult("failure", "live", "twilio", safe_error="connect error", retryable=True))
    registry.set_adapter("messaging", fake)
    ids = setup_active()
    with db.session_scope() as s:
        aid = action_by_key(s, ids["episode"], "welcome_message").id
    approve(aid, ids["doctor"])
    for _ in range(6):
        with db.session_scope() as s:  # skip the real-time backoff
            for t in s.scalars(select(ScheduledTask).where(ScheduledTask.action_id == aid)):
                t.next_attempt_at = None
        worker.run_once()
    assert fake.calls == 3  # max_attempts
    with db.session_scope() as s:
        assert s.get(ProposedAction, aid).execution_state == ExecutionState.FAILED


def test_restart_recovery(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "caregiver_brief")
        t_action = s.scalars(select(ScheduledTask).where(ScheduledTask.action_id == a.id)).first()
        t_action.status, t_action.claimed_by = TaskStatus.RUNNING, "old-process"
        t_action.lease_expires_at = clock.real_now() - timedelta(minutes=5)
        a.execution_state = ExecutionState.RUNNING
        s.add(ToolExecution(episode_id=a.episode_id, task_id=t_action.id, action_id=a.id, tool="messaging", provider="twilio",
                            mode="live", idempotency_key=a.idempotency_key, outcome="pending"))
        t_internal = s.scalars(select(ScheduledTask).where(ScheduledTask.episode_id == ids["episode"],
                                                           ScheduledTask.kind == "open_checkin")).first()
        t_internal.status, t_internal.claimed_by = TaskStatus.RUNNING, "old-process"
        ids_t = (t_action.id, t_internal.id, a.id)
    n = worker.recover_on_startup()
    assert n == 2
    with db.session_scope() as s:
        assert s.get(ScheduledTask, ids_t[0]).status == TaskStatus.UNKNOWN
        assert s.get(ProposedAction, ids_t[2]).execution_state == ExecutionState.UNKNOWN
        assert s.get(ScheduledTask, ids_t[1]).status == TaskStatus.READY
        assert s.scalars(select(ReviewItem).where(ReviewItem.kind == "unknown_outcome")).first() is not None
        # pending work survives: all other tasks still present and open
        open_tasks = s.scalars(select(ScheduledTask).where(ScheduledTask.episode_id == ids["episode"],
                                                           ScheduledTask.status.in_([TaskStatus.SCHEDULED, TaskStatus.AWAITING_APPROVAL]))).all()
        assert len(open_tasks) > 5


def test_96h_summary_and_closure(session):
    ids = setup_active()
    for _ in range(4):
        advance(24)
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, ids["episode"])
        assert ep.status == "awaiting_closure_review" and ep.monitoring_ended_at is not None
        assert "not a medical clearance" in ep.summary["statement"]
        assert ep.summary["missed_or_skipped"]  # e.g. the unapproved welcome message
        assert s.scalars(select(ReviewItem).where(ReviewItem.kind == "closure_review")).first() is not None
        ops.close_episode(s, ep, note="Reviewed summary", doctor=s.get(User, ids["doctor"]))
        assert ep.status == "closed"
