"""Agent B regression tests for evaluator findings A-05..A-11 and the pre-activation medication review."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app import db, errors, llm
from app.agent.memory import active_facts
from app.clock import clock
from app.config import settings
from app.execution import worker
from app.models import (
    ApprovalState,
    DischargeEpisode,
    ExecutionState,
    Patient,
    PlanVersion,
    ProposedAction,
    ReviewItem,
    RoutineAuthorisation,
    ScheduledTask,
    SystemState,
    TaskStatus,
    ToolExecution,
    User,
)
from app.policy import approval as policy
from app.services import agent_ops as ops
from app.tools import registry
from tests.test_agent_helpers import (
    action_by_key, advance, approve, count, make_episode, make_staff, run_worker, setup_active, submit_checkin,
)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for n in ("messaging", "calendar", "care_team_email", "medication_info"):
        registry.set_adapter(n, None)
    llm.set_override(None)


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-not-real")

    def install(fn):
        llm.set_override(fn)

    return install


class _LiveModeMessaging:
    """Same recipients as the real adapter but reports LIVE mode (as if credentials were added)."""

    def effective_recipient(self, phone):
        return settings.demo_phone or phone

    def mode_for(self, effective):
        return "live"

    def status(self):
        from app.tools.messaging import MessagingAdapter

        return MessagingAdapter().status()

    def execute(self, request, *, idempotency_key):
        raise AssertionError("must not be called: approval was given for SIMULATED mode")


@pytest.mark.parametrize("decision", ["reject", "revoke"])
def test_decision_between_dispatch_and_prepare_prevents_send(session, monkeypatch, decision):
    from app.execution import handlers

    ids = setup_active()
    with db.session_scope() as s:
        action = action_by_key(s, ids["episode"], "welcome_message")
        aid = action.id
        if decision == "reject":
            ops.approve_action(s, aid, payload_hash=action.payload_hash, doctor=s.get(User, ids["doctor"]))
        else:
            action = action_by_key(s, ids["episode"], "reminder_24")
            assert action is not None and action.approval_state == ApprovalState.ROUTINE
            aid = action.id
            clock.advance(s, 24)

    prepare = handlers.HANDLERS["execute_action"]
    intercepted = []

    def reject_before_prepare(task_id):
        with db.session_scope() as s:
            task = s.get(ScheduledTask, task_id)
            if task.action_id == aid and not intercepted:
                action = s.get(ProposedAction, aid)
                if decision == "reject":
                    ops.reject_action(s, aid, base_payload_hash=action.payload_hash,
                                      reason="Withdrawn before provider call", doctor=s.get(User, ids["doctor"]))
                else:
                    ops.revoke_routine(s, action.routine_authorisation_id,
                                       reason="Withdrawn before provider call", doctor=s.get(User, ids["doctor"]))
                intercepted.append(aid)
        prepare(task_id)

    monkeypatch.setitem(handlers.HANDLERS, "execute_action", reject_before_prepare)
    run_worker()
    assert intercepted == [aid]
    with db.session_scope() as s:
        assert s.get(ProposedAction, aid).approval_state == (ApprovalState.REJECTED if decision == "reject" else ApprovalState.PENDING)
        assert count(s, ToolExecution, ToolExecution.action_id == aid) == 0


def test_reconciled_reply_updates_patient_receipt(session):
    from app.models import CheckIn, Message
    from app.tools.base import ToolResult
    from tests.test_execution_flow import _FakeMessaging

    registry.set_adapter("messaging", _FakeMessaging(ToolResult("unknown", "live", "twilio", safe_error="timeout")))
    ids = setup_active()
    advance(24)
    cid = submit_checkin(ids["episode"], "red_flag")
    with db.session_scope() as s:
        aid = action_by_key(s, ids["episode"], "urgent_safety_").id
    approve(aid, ids["doctor"])
    with db.session_scope() as s:
        assert s.get(CheckIn, cid).response_message_id is None
        execution = s.scalars(select(ToolExecution).where(ToolExecution.action_id == aid)).one()
        ops.reconcile_execution(s, execution.id, outcome="success", note="Provider confirmed acceptance", actor=s.get(User, ids["doctor"]))
        response = s.scalars(select(Message).where(Message.action_id == aid)).one()
        assert s.get(CheckIn, cid).response_message_id == response.id
        assert response.status == "provider_accepted"


# --------------------------------------------------------------------------- A-05
def test_a05_mode_change_invalidates_prior_approval(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "caregiver_brief")
        assert a.expected_mode == "simulated"
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))
        old_id = a.id
    registry.set_adapter("messaging", _LiveModeMessaging())  # credentials added after approval
    run_worker()
    with db.session_scope() as s:
        old = s.get(ProposedAction, old_id)
        assert old.approval_state == ApprovalState.INVALIDATED and "expected mode" in old.invalidated_reason
        assert count(s, ToolExecution, ToolExecution.action_id == old_id) == 0
        new = action_by_key(s, ids["episode"], "caregiver_brief")
        assert new.id != old_id and new.approval_state == ApprovalState.PENDING and new.expected_mode == "live"


def test_a05_recipient_change_with_demo_phone_invalidates(session, monkeypatch):
    monkeypatch.setattr(settings, "demo_phone", "+919811111111")  # effective recipient never changes
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "caregiver_brief")
        assert a.effective_recipient == "+919811111111"
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))
        old_id = a.id
        p = s.get(Patient, s.get(DischargeEpisode, ids["episode"]).patient_id)
        p.caregiver_phone_e164 = "+919000000077"  # changed without any replan call
    run_worker()
    with db.session_scope() as s:
        assert s.get(ProposedAction, old_id).approval_state == ApprovalState.INVALIDATED
        assert count(s, ToolExecution, ToolExecution.action_id == old_id) == 0
        assert action_by_key(s, ids["episode"], "caregiver_brief").intended_recipient == "+919000000077"


def test_a05_hash_binds_intended_recipient_and_mode():
    base = dict(episode_id=1, action_type="send_message", recipient_role="patient", intended_recipient="+911",
                effective_recipient="+999", expected_mode="simulated", channel="sms", template_id="welcome_v1",
                payload={"body": "x"}, step_key="welcome_message", due_at=clock.now())
    h = policy.compute_hash(**base)
    assert h != policy.compute_hash(**{**base, "intended_recipient": "+912"})
    assert h != policy.compute_hash(**{**base, "expected_mode": "live"})


def test_a05_revoked_caregiver_consent_blocks_send(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "caregiver_brief")
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))
        aid = a.id
        s.get(Patient, s.get(DischargeEpisode, ids["episode"]).patient_id).caregiver_authorised = False
    run_worker()
    with db.session_scope() as s:
        assert count(s, ToolExecution, ToolExecution.action_id == aid) == 0
        assert s.get(ProposedAction, aid).approval_state == ApprovalState.INVALIDATED


# --------------------------------------------------------------------------- A-06
def test_a06_routine_coverage_fails_closed(session):
    ids = setup_active()
    with db.session_scope() as s:
        ra = s.scalars(select(RoutineAuthorisation).where(RoutineAuthorisation.episode_id == ids["episode"])).one()
        rem = action_by_key(s, ids["episode"], "reminder_24h")
        welcome = action_by_key(s, ids["episode"], "welcome_message")
        now = clock.now()
        assert policy.routine_covers(s, rem, ra, now, expected_payload=None)[0] is False   # missing content
        assert policy.routine_covers(s, rem, None, now, expected_payload=rem.payload)[0] is False
        assert policy.routine_covers(s, welcome, ra, now, expected_payload=welcome.payload)[0] is False  # explicit-only
        ra.template_ids = ["checkin_reminder_v1", "welcome_v1"]  # out-of-policy scope (tampered)
        assert policy.routine_covers(s, rem, ra, now, expected_payload=rem.payload)[0] is False
        ra.template_ids = ["checkin_reminder_v1"]
        assert policy.routine_covers(s, rem, ra, now, expected_payload=rem.payload)[0] is True
        # unknown approval state must never execute
        welcome.approval_state = "not_required"
        wid = welcome.id
    from tests.test_agent_helpers import resolve_reviews

    resolve_reviews("medication_review", ids)
    with db.session_scope() as s:
        assert count(s, ToolExecution, ToolExecution.action_id == wid) == 0
        t = s.scalars(select(ScheduledTask).where(ScheduledTask.action_id == wid)).first()
        assert t.status == TaskStatus.AWAITING_APPROVAL


# --------------------------------------------------------------------------- A-07
def test_a07_urgent_item_visible_before_llm_returns(session, fake_llm):
    seen = {}

    def fn(system, user, schema, purpose):
        seen.setdefault("purposes", []).append(purpose)
        if purpose == "reply_draft":  # the (possibly slow) AI reply draft for the red flag
            with db.SessionLocal() as other:  # what the hospital UI would see right now
                seen["items"] = other.scalars(select(ReviewItem).where(ReviewItem.kind == "urgent_concern")).all()
        return llm.LLMResult(False, "anthropic", "m", error="slow / failed")

    ids = setup_active()
    advance(24)
    fake_llm(fn)
    submit_checkin(ids["episode"], "red_flag")
    assert "triage" not in seen["purposes"]  # rules said red flag: the AI is never asked for the level
    assert len(seen["items"]) == 1 and seen["items"][0].priority == "urgent"
    with db.session_scope() as s:
        assert count(s, ReviewItem, ReviewItem.kind == "urgent_concern") == 1  # not duplicated afterwards


# --------------------------------------------------------------------------- A-08
def test_a08_no_write_lock_held_during_llm_extraction(session, fake_llm):
    result = {}

    def fn(system, user, schema, purpose):
        with db.SessionLocal() as other:
            other.execute(text("PRAGMA busy_timeout=300"))
            other.merge(SystemState(key="probe", value={"ok": True}))
            other.commit()  # would raise "database is locked" if the caller still held the write lock
            result[purpose] = True
        return llm.LLMResult(False, "anthropic", "m", error="test")

    fake_llm(fn)
    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor)
        from app.models import DischargeDocument
        from tests.test_agent_integrity import PROSE_SUMMARY

        # weak (prose) summary so the router sends extraction to the AI as well
        s.scalars(select(DischargeDocument).where(DischargeDocument.episode_id == ep.id)).first().text = PROSE_SUMMARY
        ops.run_extraction(s, ep, actor=nurse)
        ep2 = make_episode(s, nurse, doctor)  # normal summary: has a diagnosis, so planning asks the AI for additions
        ops.run_extraction(s, ep2, actor=nurse)
        ep2.status = "awaiting_review"
        ops.generate_draft_plan(s, ep2, actor=nurse)
    assert result == {"extraction": True, "planning": True}


# --------------------------------------------------------------------------- A-09
def test_a09_worker_tick_cannot_rewind_uncommitted_advance(session):
    before = clock.now()
    s = db.SessionLocal()
    try:
        clock.advance(s, 24)  # flushed, not yet committed
        worker.run_once()     # concurrent tick used to reload the old committed offset
        assert clock.now() >= before + timedelta(hours=24)
        s.commit()
    finally:
        s.close()
    r = advance(6)
    assert clock.now() >= before + timedelta(hours=30) and r["sim_now"]


# --------------------------------------------------------------------------- A-10
def test_a10_demo_fault_injection_produces_unknown_and_failure(session):
    ids = setup_active()
    with db.session_scope() as s:
        ops.set_demo_faults(s, {"messaging": "timeout"})
        aid = action_by_key(s, ids["episode"], "caregiver_brief").id
    approve(aid, ids["doctor"])
    with db.session_scope() as s:
        ex = s.scalars(select(ToolExecution).where(ToolExecution.action_id == aid)).one()
        assert ex.outcome == "unknown" and ex.provider == "demo_fault" and ex.mode == "simulated"
        assert s.get(ProposedAction, aid).execution_state == ExecutionState.UNKNOWN
        assert s.scalars(select(ReviewItem).where(ReviewItem.kind == "unknown_outcome")).first()
        ops.set_demo_faults(s, {"messaging": "none", "triage": "fail"})
    advance(24)
    cid = submit_checkin(ids["episode"], "all_good")
    with db.session_scope() as s:
        from app.models import CheckIn

        assert s.get(CheckIn, cid).processing_state == "failed"
        assert ops.get_demo_faults(s)["triage"] == "fail"


def test_a10_faults_disabled_outside_demo_mode(session, monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "production")
    with db.session_scope() as s:
        with pytest.raises(errors.ApiError) as e:
            ops.set_demo_faults(s, {"messaging": "fail"})
        assert e.value.code == "demo_disabled"
        s.add(SystemState(key="demo_faults", value={"messaging": "fail"}))
        s.flush()
        assert ops.get_demo_faults(s)["messaging"] == "none"


# --------------------------------------------------------------------------- A-11
def test_a11_urgent_action_survives_window_end(session):
    ids = setup_active()
    advance(24)
    cid = submit_checkin(ids["episode"], "red_flag")
    for _ in range(3):
        advance(24)  # past T+96
    with db.session_scope() as s:
        assert s.get(DischargeEpisode, ids["episode"]).status == "awaiting_closure_review"
        safety = action_by_key(s, ids["episode"], f"urgent_safety_{cid}")
        assert safety.approval_state == ApprovalState.PENDING and safety.execution_state == ExecutionState.NOT_STARTED
        t = s.scalars(select(ScheduledTask).where(ScheduledTask.action_id == safety.id)).first()
        assert t.status != TaskStatus.SKIPPED
        assert s.scalars(select(ReviewItem).where(ReviewItem.title.like("%urgent action(s) still open%"))).first()
        sid = safety.id
    approve(sid, ids["doctor"])
    with db.session_scope() as s:
        assert s.get(ProposedAction, sid).execution_state == ExecutionState.SUCCEEDED
        assert count(s, ToolExecution, ToolExecution.action_id == sid) == 1


def test_a11_approved_nonurgent_not_left_showing_approved(session):
    ids = setup_active()
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "closing_message")
        ops.approve_action(s, a.id, payload_hash=a.payload_hash, doctor=s.get(User, ids["doctor"]))
        aid = a.id
    for _ in range(4):
        advance(24)
    with db.session_scope() as s:
        a = s.get(ProposedAction, aid)
        # sent at T+95.5 if fresh; if it was skipped it must not still claim "approved"
        assert a.execution_state == ExecutionState.SUCCEEDED or a.approval_state == ApprovalState.INVALIDATED


# --------------------------------------------------------------------------- P1: medication review before activation
def test_medication_review_completes_before_activation(session):
    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor)
        ops.run_extraction(s, ep, actor=nurse)
        ep.status = "awaiting_review"
        v1 = ops.generate_draft_plan(s, ep, actor=nurse)
        eid, did, v1_id = ep.id, doctor.id, v1.id
    with db.session_scope() as s:  # doctor tries before the review finished
        ep = s.get(DischargeEpisode, eid)
        with pytest.raises(errors.ApiError) as e:
            ops.activate_plan(s, ep, plan_version_id=v1_id, confirm_fact_ids=[f.id for f in active_facts(s, eid)],
                              routine=None, doctor=s.get(User, did))
        assert e.value.code == "medication_review_pending"
    worker.run_until_idle()
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, eid)
        pv = s.get(PlanVersion, ep.current_plan_version_id)
        assert pv.version == 2 and pv.trigger == "tool_finding" and pv.status == "draft"
        with pytest.raises(errors.ApiError) as e:
            ops.activate_plan(s, ep, plan_version_id=v1_id, confirm_fact_ids=[f.id for f in active_facts(s, eid)],
                              routine=None, doctor=s.get(User, did))
        assert e.value.code == "stale_version"
