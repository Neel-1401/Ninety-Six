"""Hybrid rules-first LLM routing: guardrails, tested with a fake LLM (no network)."""
from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from app import db, llm
from app.agent import drafting
from app.config import settings
from app.llm import router
from app.models import AuditEvent, CheckIn, PlanStep, PlanVersion
from app.services import agent_ops as ops
from tests.test_agent_helpers import action_by_key, advance, make_episode, make_staff, setup_active, submit_checkin


@pytest.fixture
def fake(monkeypatch):
    """install(fn) -> calls list. fn(purpose, user) returns an LLMResult, a raw string, or a pydantic object."""
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "test-key-not-real")
    calls: list[str] = []

    def install(fn):
        def wrapped(system, user, schema, purpose):
            calls.append(purpose)
            out = fn(purpose, user, schema)
            if out is None:
                return llm.LLMResult(False, "gemini", "fake-model", error="not handled")
            if isinstance(out, (str, llm.LLMResult)):
                return out
            return llm.LLMResult(True, "gemini", "fake-model", data=out)

        llm.set_override(wrapped)
        return calls

    yield install
    llm.set_override(None)
    llm.set_enabled(None)
    drafting.clear()


def _events(s, eid, kind):
    return s.scalars(select(AuditEvent).where(AuditEvent.episode_id == eid, AuditEvent.kind == kind)).all()


# --------------------------------------------------------------------------- (a) red flag can't be downgraded
def test_llm_cannot_downgrade_red_flag(session, fake):
    from app.agent.triage import LLMTriage

    calls = fake(lambda purpose, user, schema: LLMTriage(level="ok", reasons=["patient sounds fine"])
                 if purpose == "triage" else None)
    ids = setup_active()
    advance(24)
    calls.clear()
    cid = submit_checkin(ids["episode"], "red_flag")
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert ci.triage_level == "urgent"  # stays RED FLAG
        assert ci.triage_source == "rules"
        assert "triage" not in calls  # rules said red flag: the AI is never asked for the level
        assert any("red flag" in e.title.lower() for e in _events(s, ids["episode"], "ai.rules"))
        a = action_by_key(s, ids["episode"], "urgent_safety_")
        assert a.priority == "urgent" and a.approval_state == "pending"


def test_llm_ok_cannot_lower_rules_concern(session, fake):
    from app.agent.triage import LLMTriage

    calls = fake(lambda purpose, user, schema: LLMTriage(level="ok", reasons=["nothing to worry"])
                 if purpose == "triage" else None)
    ids = setup_active()
    advance(24)
    calls.clear()
    cid = submit_checkin(ids["episode"], "concern")  # long free text -> AI consulted
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert "triage" in calls
        assert ci.triage_level == "concern"  # max(rules=concern, AI=ok)
        assert ci.triage_source.startswith("llm:")


def test_llm_can_raise_concern_to_urgent(session, fake):
    from app.agent.triage import LLMTriage

    fake(lambda purpose, user, schema: LLMTriage(level="urgent", reasons=["sounds like fluid overload"])
         if purpose == "triage" else None)
    ids = setup_active()
    advance(24)
    cid = submit_checkin(ids["episode"], "concern")
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert ci.triage_level == "urgent"
        assert any(r.startswith("Language model raised level") for r in ci.triage_reasons)


# --------------------------------------------------------------------------- (b) invalid JSON / timeout -> rules
def test_invalid_json_falls_back_to_rules_and_flow_continues(session, fake):
    calls = fake(lambda purpose, user, schema: "sorry, I can't produce JSON {" if purpose == "triage" else None)
    ids = setup_active()
    advance(24)
    calls.clear()
    cid = submit_checkin(ids["episode"], "concern")
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert ci.processing_state == "processed" and ci.triage_level == "concern"
        assert ci.triage_source == "rules (LLM unavailable)"
        assert calls.count("triage") == 2  # strict JSON failed -> exactly one retry -> rules
        assert any("AI failed -> rules fallback" in e.title for e in _events(s, ids["episode"], "ai.fallback"))
        assert action_by_key(s, ids["episode"], "concern_reply_") is not None  # flow continued


def test_timeout_falls_back_to_rules_and_flow_continues(session, fake, monkeypatch):
    monkeypatch.setattr(settings, "llm_timeout", 0.2)
    assert llm.hard_timeout() == 0.2

    def slow(purpose, user, schema):
        time.sleep(0.6)
        return None

    calls = fake(slow)
    ids = setup_active()
    advance(24)
    calls.clear()
    t0 = time.perf_counter()
    cid = submit_checkin(ids["episode"], "concern")
    with db.session_scope() as s:
        ci = s.get(CheckIn, cid)
        assert ci.processing_state == "processed" and ci.triage_level == "concern"
        assert calls.count("triage") == 1  # timeouts are not retried
        a = action_by_key(s, ids["episode"], "concern_reply_")
        assert a is not None and "drafted_by" not in a.payload  # template fallback for the reply too
    assert time.perf_counter() - t0 < 10


def test_hard_timeout_is_capped_at_8_seconds(monkeypatch):
    monkeypatch.setattr(settings, "llm_timeout", 40)
    assert llm.hard_timeout() == 8.0


# --------------------------------------------------------------------------- (c) unknown tool dropped
def test_llm_plan_unknown_tool_is_dropped_valid_addition_kept(session, fake):
    from app.agent.planner import LLMPlan, _LLMStep

    def fn(purpose, user, schema):
        if purpose != "planning":
            return None
        return LLMPlan(rationale="HF extras", steps=[
            _LLMStep(step_key="adjust_dose", title="Increase furosemide", purpose="p", action_type="change_medication",
                     arguments_json="{}", offset_hours=0, depends_on=[], rationale="weight up"),
            _LLMStep(step_key="late_call", title="Late call", purpose="p", action_type="staff_review",
                     arguments_json='{"title": "Call", "summary": "x"}', offset_hours=130, depends_on=[], rationale="r"),
            _LLMStep(step_key="sneaky_sms", title="Extra SMS", purpose="p", action_type="send_message",
                     arguments_json='{"recipient_role": "patient", "template_id": "welcome_v1"}', offset_hours=5,
                     depends_on=[], rationale="r"),
            _LLMStep(step_key="weight_call", title="Nurse call: confirm daily weight log", purpose="Check fluid status",
                     action_type="staff_review",
                     arguments_json='{"title": "Confirm daily weight log", "summary": "Ask for the last 2 days of weights", "requires_role": "any"}',
                     offset_hours=36, depends_on=["med_review", "no_such_step"], rationale="Weight gain is the earliest HF warning"),
        ])

    calls = fake(fn)
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
        steps = s.scalars(select(PlanStep).where(PlanStep.plan_version_id == pid)).all()
        types = {st.action_type for st in steps}
        assert "change_medication" not in types
        ai = [st for st in steps if st.origin == "ai"]
        assert [st.step_key for st in ai] == ["ai_weight_call"]
        assert ai[0].offset_hours == 36 and ai[0].depends_on == ["med_review"]
        assert ai[0].arguments["kind"] == "care_task" and ai[0].rationale.startswith("AI-suggested")
        assert pv.source == "rules+llm:gemini"
        dropped = " | ".join(pv.validation["ai_dropped"])
        assert "unknown tool 'change_medication'" in dropped and "outside 0-96" in dropped and "not allowed" in dropped
        assert {st.step_key for st in steps} >= {"med_review", "welcome_message", "summary_96h"}  # rule base intact
        assert _events(s, eid, "ai.used")


# --------------------------------------------------------------------------- (d) simple check-ins never call the LLM
def test_simple_checkins_never_call_llm(session, fake):
    calls = fake(lambda purpose, user, schema: None)
    ids = setup_active()
    calls.clear()
    advance(24)  # opens the check-in + routine reminder (templated, rule-only)
    cid = submit_checkin(ids["episode"], "all_good")
    assert calls == []
    with db.session_scope() as s:
        assert s.get(CheckIn, cid).triage_level == "ok"
        assert any("simple check-in" in e.title for e in _events(s, ids["episode"], "ai.rules"))


def test_router_rules_only_tasks_and_short_known_replies():
    for t in ("scheduling", "risk", "approval", "interaction_check", "red_flag_keywords", "checkin_template"):
        assert router.should_use_llm(t, {}) is False


def test_router_triage_decisions(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    d = lambda lvl, txt: router.decide("triage", {"rules_level": lvl, "free_text": txt, "keyword_lists": [["dizzy"]]})
    assert not d("urgent", "a very long description of how I feel today")
    assert not d("ok", None) and not d("ok", "All good") and not d("ok", "dizzy")
    assert d("ok", "my legs feel kind of heavy and strange")
    assert d("ok", "hmm weird")  # short but no known pattern
    llm.set_enabled(False)
    try:
        assert not d("ok", "my legs feel kind of heavy and strange")  # Settings kill switch
    finally:
        llm.set_enabled(None)


# --------------------------------------------------------------------------- drafting still goes through approval
def test_ai_reply_draft_needs_approval_and_keeps_priority(session, fake):
    from app.agent.drafting import DraftOut

    def fn(purpose, user, schema):
        if purpose == "reply_draft":
            tpl = json.loads(user)["approved_template"]
            return DraftOut(body_en="Thank you for telling us you missed a tablet. " + tpl, body_translated=None)
        return None

    fake(fn)
    ids = setup_active()
    advance(24)
    submit_checkin(ids["episode"], "concern")
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "concern_reply_")
        assert a.payload["drafted_by"] == "ai" and a.payload["body"].startswith("Thank you for telling us")
        assert a.approval_state == "pending" and a.priority == "high" and a.approval_requirement == "explicit"


def test_unsafe_ai_draft_is_rejected_for_template(session, fake):
    from app.agent.drafting import DraftOut

    fake(lambda purpose, user, schema: DraftOut(body_en="Take 80 mg furosemide now. Your triage is concern.")
         if purpose == "reply_draft" else None)
    ids = setup_active()
    advance(24)
    submit_checkin(ids["episode"], "concern")
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "concern_reply_")
        assert "drafted_by" not in a.payload and "nurse will contact you" in a.payload["body"]
        assert any("draft rejected" in e.title for e in _events(s, ids["episode"], "ai.fallback"))


def test_non_english_welcome_is_translated_with_english_original(session, fake):
    from app.agent.drafting import DraftOut

    calls = fake(lambda purpose, user, schema: DraftOut(
        body_en=json.loads(user)["text"], body_translated="ನಮಸ್ಕಾರ " + json.loads(user)["text"])
        if purpose == "translate" else None)
    ids = setup_active(key="diabetes", resolve_med_review=True)
    assert "translate" in calls
    with db.session_scope() as s:
        a = action_by_key(s, ids["episode"], "welcome_message")
        assert a.payload["drafted_by"] == "ai" and a.payload["language"] == "Kannada"
        assert a.payload["body"].startswith("ನಮಸ್ಕಾರ") and a.payload["body_en"].startswith("Hello")
        assert a.approval_state == "pending"


def test_identical_calls_are_cached(fake):
    from app.agent.triage import LLMTriage

    calls = fake(lambda purpose, user, schema: LLMTriage(level="concern", reasons=["x"]))
    r1 = llm.structured("sys", "same prompt", LLMTriage, purpose="triage")
    r2 = llm.structured("sys", "same prompt", LLMTriage, purpose="triage")
    assert r1.ok and r2.ok and r2.cached and calls == ["triage"]
