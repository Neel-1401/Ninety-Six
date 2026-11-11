"""Agent B: extraction provenance, plan validation/fallback, triage + replanning."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app import db, llm
from app.config import settings
from app.events import record_event  # noqa: F401
from app.models import (
    ApprovalState,
    AuditEvent,
    CheckIn,
    CheckInResponse,
    DischargeEpisode,
    ExecutionState,
    FactUsage,
    PatientFact,
    PlanStep,
    PlanVersion,
    ProcessingState,
    ReviewItem,
    User,
)
from app.services import agent_ops as ops
from tests.test_agent_helpers import action_by_key, advance, approve, make_episode, make_staff, run_worker, setup_active, submit_checkin


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-not-real")
    calls = []

    def install(fn):
        def wrapped(system, user, schema, purpose):
            calls.append(purpose)
            return fn(system, user, schema, purpose)

        llm.set_override(wrapped)
        return calls

    yield install
    llm.set_override(None)


def test_extraction_keeps_sources_and_marks_missing(session):
    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor, key="diabetes")
        ops.run_extraction(s, ep, actor=nurse)
        eid = ep.id
    with db.session_scope() as s:
        facts = s.scalars(select(PatientFact).where(PatientFact.episode_id == eid, PatientFact.active.is_(True))).all()
        ep = s.get(DischargeEpisode, eid)
        allergy = [f for f in facts if f.category == "allergy"]
        assert len(allergy) == 1 and allergy[0].is_missing and allergy[0].value["text"] == "not provided"
        ator = next(f for f in facts if f.category == "medication" and f.value["name"] == "Atorvastatin")
        assert ator.value["dose"] == "not provided"  # never invented
        assert ator.source_ref.startswith("document:") and "#L" in ator.source_ref
        assert ator.source_excerpt == "- Atorvastatin once daily at night"
        assert all(f.source_ref for f in facts)
        assert any("Atorvastatin" in n for n in ep.extraction_notes)
        assert ep.extraction_method == "rules"
        assert ep.risk_category in ("low", "medium", "high") and ep.risk_reasons is not None


PROSE_SUMMARY = "\n".join([
    "Discharge note (fictional).",
    "He was treated for acute decompensated heart failure (NYHA III).",
    "He goes home on furosemide 40 mg once daily.",
    "Review in cardiology clinic with Dr. Mehta within 7 days.",
])


def test_ungrounded_llm_extraction_items_are_dropped(session, fake_llm):
    """Hybrid: a prose summary (no sections) is a WEAK regex result -> AI consulted; ungrounded AI items dropped."""
    from app.agent.extraction import LLMExtraction, _FollowUp, _Item, _Med
    from app.models import DischargeDocument

    def fn(system, user, schema, purpose):
        if purpose == "extraction":
            data = LLMExtraction(
                diagnosis=[_Item(text="Acute decompensated heart failure (NYHA III)", line=2)],
                medications=[_Med(name="Furosemide", dose="40 mg", frequency="once daily", line=3),
                             _Med(name="Warfarin", dose="5 mg", frequency="daily", line=3)],  # invented
                follow_up=[_FollowUp(title="Cardiology clinic review", provider="Dr. Mehta", within_days=7, line=4)],
            )
            return llm.LLMResult(True, "anthropic", "m", data=data)
        return llm.LLMResult(False, "anthropic", "m", error="test")

    calls = fake_llm(fn)
    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor)
        s.scalars(select(DischargeDocument).where(DischargeDocument.episode_id == ep.id)).first().text = PROSE_SUMMARY
        ops.run_extraction(s, ep, actor=nurse)
        eid = ep.id
    assert "extraction" in calls
    with db.session_scope() as s:
        meds = s.scalars(select(PatientFact).where(PatientFact.episode_id == eid, PatientFact.category == "medication",
                                                   PatientFact.active.is_(True))).all()
        assert [m.value["name"] for m in meds] == ["Furosemide"]
        ep = s.get(DischargeEpisode, eid)
        assert ep.extraction_method == "rules+llm:anthropic"
        assert any("Warfarin" in n for n in ep.extraction_notes)
        assert s.scalars(select(AuditEvent).where(AuditEvent.episode_id == eid, AuditEvent.kind == "ai.used")).first()


def test_strong_regex_result_never_calls_llm_for_extraction(session, fake_llm):
    calls = fake_llm(lambda *a: llm.LLMResult(False, "anthropic", "m", error="should not be called"))
    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor)
        ops.run_extraction(s, ep, actor=nurse)
        assert ep.extraction_method == "rules"
    assert "extraction" not in calls


def test_invalid_llm_plan_is_rejected_and_fallback_is_visible(session, fake_llm):
    from app.agent.planner import LLMPlan, _LLMStep

    def fn(system, user, schema, purpose):
        if purpose == "planning":
            return llm.LLMResult(True, "anthropic", "m", data=LLMPlan(rationale="x", steps=[
                _LLMStep(step_key="adjust_dose", title="Increase furosemide", purpose="p", action_type="change_medication",
                         arguments_json="{}", offset_hours=0, depends_on=[], rationale="r"),
                _LLMStep(step_key="late", title="Late message", purpose="p", action_type="send_message",
                         arguments_json='{"recipient_role":"patient","template_id":"welcome_v1"}', offset_hours=130,
                         depends_on=[], rationale="r"),
            ]))
        return llm.LLMResult(False, "anthropic", "m", error="test")

    calls = fake_llm(fn)
    with db.session_scope() as s:
        doctor, nurse = make_staff(s)
        ep = make_episode(s, nurse, doctor)
        ops.run_extraction(s, ep, actor=nurse)
        ep.status = "awaiting_review"
        pv = ops.generate_draft_plan(s, ep, actor=nurse)
        pid, eid = pv.id, ep.id
    assert "planning" in calls
    with db.session_scope() as s:
        pv = s.get(PlanVersion, pid)
        assert pv.source == "rules_fallback"
        assert pv.validation["llm_attempted"] and pv.validation["llm_valid"] is False and pv.validation["fallback_used"]
        assert pv.validation["errors"]
        steps = s.scalars(select(PlanStep).where(PlanStep.plan_version_id == pid)).all()
        assert not any(st.action_type == "change_medication" for st in steps)
        assert all(0 <= st.offset_hours <= 96 for st in steps)
        assert s.scalars(select(AuditEvent).where(AuditEvent.episode_id == eid, AuditEvent.kind == "plan.fallback_used")).first()
        assert s.scalars(select(FactUsage).where(FactUsage.episode_id == eid, FactUsage.used_for == "planning")).first()


def test_red_flag_checkin_creates_urgent_review_and_explicit_actions(session):
    ids = setup_active()
    advance(24)  # T+26: 24 h check-in open
    cid = submit_checkin(ids["episode"], "red_flag")
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert ci.processing_state == ProcessingState.PROCESSED and ci.triage_level == "urgent"
        assert ci.staff_review_state == "awaiting"  # processed by agent is NOT reviewed by staff
        item = s.scalars(select(ReviewItem).where(ReviewItem.kind == "urgent_concern")).one()
        assert item.priority == "urgent" and item.requires_role == "doctor" and item.status == "open"
        ep = s.get(DischargeEpisode, ids["episode"])
        pv = s.get(PlanVersion, ep.current_plan_version_id)
        assert pv.trigger == "patient_response"
        added = {c["step_key"] for c in pv.change_summary if c["change"] == "added"}
        assert {f"urgent_safety_{cid}", f"care_team_alert_{cid}", f"caregiver_update_{cid}"} <= added
        safety = action_by_key(s, ep.id, f"urgent_safety_{cid}")
        assert safety.approval_state == ApprovalState.PENDING and safety.priority == "urgent"
        assert action_by_key(s, ep.id, f"care_team_alert_{cid}").approval_requirement == "explicit"
        assert s.scalars(select(PatientFact).where(PatientFact.episode_id == ep.id,
                                                   PatientFact.source_kind == "patient_reported")).first()
        assert s.scalars(select(FactUsage).where(FactUsage.used_for == "triage", FactUsage.ref_id == cid)).first()
        sid = safety.id
    approve(sid, ids["doctor"])
    run_worker()
    with db.session_scope() as s:
        from app.models import Message

        a = action_by_key(s, ids["episode"], f"urgent_safety_{cid}")
        assert a.execution_state == ExecutionState.SUCCEEDED
        assert len(s.scalars(select(Message).where(Message.action_id == sid)).all()) == 1


def test_concern_changes_persisted_steps(session):
    ids = setup_active()
    advance(24)
    with db.session_scope() as s:
        before = s.get(DischargeEpisode, ids["episode"]).current_plan_version_id
    cid = submit_checkin(ids["episode"], "concern")
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, ids["episode"])
        assert ep.current_plan_version_id != before
        keys = {st.step_key for st in s.scalars(select(PlanStep).where(PlanStep.plan_version_id == ep.current_plan_version_id))}
        assert f"concern_reply_{cid}" in keys and f"extra_checkin_{cid}" in keys
        assert s.scalars(select(ReviewItem).where(ReviewItem.kind == "concern")).one().priority == "high"
        # the extra reminder is outside the doctor's routine schedule -> needs approval
        assert action_by_key(s, ep.id, f"extra_reminder_{cid}").approval_state == ApprovalState.PENDING
        # completed work was preserved
        old_keys = {st.step_key: st.status for st in s.scalars(select(PlanStep).where(PlanStep.plan_version_id == before))}
        new = {st.step_key: st.status for st in s.scalars(select(PlanStep).where(PlanStep.plan_version_id == ep.current_plan_version_id))}
        assert new["checkin_24h"] == "executed" and new["med_review"] == "executed"


def test_all_good_checkin_requests_no_review_and_keeps_plan(session):
    ids = setup_active()
    advance(24)
    with db.session_scope() as s:
        before = s.get(DischargeEpisode, ids["episode"]).current_plan_version_id
    cid = submit_checkin(ids["episode"], "all_good")
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert ci.triage_level == "ok" and ci.staff_review_state == "not_requested"
        assert s.get(DischargeEpisode, ids["episode"]).current_plan_version_id == before


def test_llm_can_only_raise_triage(session, fake_llm):
    from app.agent.triage import LLMTriage, combine, rules_triage

    rules = rules_triage("heart_failure_v1", {"breathless_flat": "yes"}, None, [])
    assert rules["level"] == "urgent"
    lowered = combine(rules, {"level": "ok", "reasons": ["fine"], "provider": "anthropic"})
    assert lowered["level"] == "urgent"
    raised = combine(rules_triage("heart_failure_v1", {"breathless_flat": "no"}, "feel odd", []),
                     {"level": "concern", "reasons": ["vague"], "provider": "anthropic"})
    assert raised["level"] == "concern" and LLMTriage


def test_processing_failure_preserves_answers(session, monkeypatch):
    from app.agent import triage

    ids = setup_active()
    advance(24)

    def boom(*a, **k):
        raise RuntimeError("triage crashed")

    monkeypatch.setattr(triage, "rules_triage", boom)
    cid = submit_checkin(ids["episode"], "red_flag")
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert ci.processing_state == ProcessingState.FAILED
        assert len(s.scalars(select(CheckInResponse).where(CheckInResponse.checkin_id == cid)).all()) == 6
        assert s.scalars(select(ReviewItem).where(ReviewItem.kind == "processing_failure")).first() is not None
    monkeypatch.undo()
    from app.models import ScheduledTask

    with db.session_scope() as s:  # skip real-time backoff, then the retry succeeds
        for t in s.scalars(select(ScheduledTask).where(ScheduledTask.checkin_id == cid, ScheduledTask.kind == "process_checkin")):
            t.next_attempt_at = None
    run_worker()
    with db.session_scope() as s:
        assert s.get(CheckIn, cid).processing_state == ProcessingState.PROCESSED


def test_missed_checkin_creates_overdue_review_item(session):
    ids = setup_active()
    advance(24)  # 24 h check-in opens
    advance(24)  # window closes unanswered
    with db.session_scope() as s:
        assert s.scalars(select(CheckIn).where(CheckIn.episode_id == ids["episode"], CheckIn.status == "missed")).first()
        assert s.scalars(select(ReviewItem).where(ReviewItem.kind == "missed_checkin")).first()


def test_seed_background_episodes(session):
    from app.models import Role

    with db.session_scope() as s:
        s.add_all([User(username="dr.iyer", display_name="Dr. Kavitha Iyer", role=Role.DOCTOR),
                   User(username="dr.mehta", display_name="Dr. Arjun Mehta", role=Role.DOCTOR),
                   User(username="nurse.asha", display_name="Asha Nair", role=Role.NURSE)])
    with db.session_scope() as s:
        ops.seed_background_episodes(s)
    run_worker()
    with db.session_scope() as s:
        eps = {e.sample_key: e.status for e in s.scalars(select(DischargeEpisode))}
        assert eps == {"appendectomy": "active", "diabetes": "draft"}
