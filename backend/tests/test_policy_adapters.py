"""Agent B: adapter outcome mapping (Twilio HTTP mocked, no network) and approval policy."""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.policy import approval as policy
from app.tools.messaging import MessagingAdapter


@pytest.fixture
def twilio(monkeypatch):
    monkeypatch.setattr(settings, "twilio_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_token", "secret")
    monkeypatch.setattr(settings, "twilio_from", "+15550000000")
    monkeypatch.setattr(settings, "demo_phone", "+919811111111")
    monkeypatch.setattr(settings, "allow_live_to_real_recipients", False)
    return monkeypatch


def _resp(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://api.twilio.com"))


def test_live_only_to_demo_phone(twilio):
    m = MessagingAdapter()
    assert m.effective_recipient("+919000000001") == "+919811111111"
    assert m.mode_for("+919811111111") == "live"
    assert m.mode_for("+919000000001") == "simulated"  # never live to a non-demo number
    r = m.execute({"to": "+919000000001", "body": "hi"}, idempotency_key="k")
    assert r.mode == "simulated" and "disabled" in r.data["reason"]


def test_twilio_accept_is_not_delivery(twilio):
    twilio.setattr(httpx, "post", lambda *a, **k: _resp(201, {"sid": "SM123", "status": "queued"}))
    r = MessagingAdapter().execute({"to": "+919811111111", "body": "hi"}, idempotency_key="k")
    assert (r.outcome, r.mode, r.delivery_status, r.provider_ref) == ("success", "live", "provider_accepted", "SM123")


def test_twilio_errors_map_honestly(twilio):
    twilio.setattr(httpx, "post", lambda *a, **k: _resp(400, {"code": 21211, "message": "Invalid 'To' +919811111111"}))
    r = MessagingAdapter().execute({"to": "+919811111111", "body": "hi"}, idempotency_key="k")
    assert r.outcome == "failure" and not r.retryable and "+919811111111" not in (r.safe_error or "")

    def timeout(*a, **k):
        raise httpx.ReadTimeout("slow")

    twilio.setattr(httpx, "post", timeout)
    r = MessagingAdapter().execute({"to": "+919811111111", "body": "hi"}, idempotency_key="k")
    assert r.outcome == "unknown" and r.mode == "live"

    def refused(*a, **k):
        raise httpx.ConnectError("no route")

    twilio.setattr(httpx, "post", refused)
    r = MessagingAdapter().execute({"to": "+919811111111", "body": "hi"}, idempotency_key="k")
    assert r.outcome == "failure" and r.retryable


def test_policy_requirements():
    assert policy.requirement("send_message", recipient_role="patient", template_id="checkin_reminder_v1") == "routine_eligible"
    assert policy.requirement("send_message", recipient_role="caregiver", template_id="caregiver_brief_v1") == "explicit"
    assert policy.requirement("send_message", recipient_role="patient", template_id="welcome_v1") == "explicit"
    assert policy.requirement("create_calendar_event") == "explicit"
    assert policy.requirement("care_team_alert") == "explicit"
    assert policy.requirement("open_checkin") == "none"


def test_calendar_slot_is_weekday_morning_ist():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.tools.calendar import event_id_for, propose_slot

    start, end = propose_slot(3, now=datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc))  # Friday
    local = start.astimezone(ZoneInfo("Asia/Kolkata"))
    assert local.weekday() < 5 and (local.hour, local.minute) == (10, 30)
    assert event_id_for("a") == event_id_for("a") and set(event_id_for("a")) <= set("0123456789abcdefghijklmnopqrstuv")
