"""Server-side permission matrix (CONTRACTS.md §2). Rows are created directly in the database where
needed so these tests do not depend on the agent services."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app import db
from app.clock import clock
from app.models import (
    CheckIn,
    CheckInStatus,
    DischargeEpisode,
    EpisodeStatus,
    PlanVersion,
    ProposedAction,
    ReviewItem,
    RoutineAuthorisation,
)
from tests.conftest import api_post, api_put, latest_otp_code, login_staff
from tests.test_auth import invite, make_episode


def _seed_episode_objects(episode_id: int, doctor_id: int) -> dict[str, int]:
    """Plan version + pending action + routine authorisation + due check-in + doctor-only review item."""
    now = clock.now()
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, episode_id)
        pv = PlanVersion(episode_id=ep.id, version=1, status="active", source="rules", trigger="initial")
        s.add(pv)
        s.flush()
        ep.current_plan_version_id = pv.id
        ep.status = EpisodeStatus.ACTIVE
        ep.activated_at = now
        ep.activated_by_id = doctor_id
        a = ProposedAction(
            episode_id=ep.id, plan_version_id=pv.id, step_key="welcome", action_type="send_message",
            recipient_role="patient", intended_recipient="+919000000101", effective_recipient="+919000000101",
            channel="sms", template_id="welcome_v1", payload={"body": "Welcome home"}, payload_hash="h" * 64,
            expected_mode="simulated", approval_requirement="explicit", approval_state="pending", due_at=now,
            idempotency_key=f"test-{ep.id}-welcome",
        )
        ra = RoutineAuthorisation(
            episode_id=ep.id, plan_version_id=pv.id, action_types=["send_message"], template_ids=["checkin_reminder_v1"],
            recipient_roles=["patient"], offsets_hours=[12, 24], expires_at=now + timedelta(hours=96),
            granted_by_id=doctor_id,
        )
        c = CheckIn(
            episode_id=ep.id, kind="scheduled", title="24-hour check-in", due_at=now, status=CheckInStatus.DUE,
            questions=[{"id": "q1", "text": "How are you?", "type": "single_choice",
                        "options": [{"value": "ok", "label": "OK"}], "required": True}],
        )
        ri = ReviewItem(episode_id=ep.id, kind="closure_review", priority="normal", title="Closure review",
                        requires_role="doctor")
        s.add_all([a, ra, c, ri])
        s.flush()
        return {"action": a.id, "routine": ra.id, "checkin": c.id, "review": ri.id, "plan": pv.id}


@pytest.fixture
def world(client, staff_ids, make_client):
    """Two patients (A, B) with episodes assigned to dr.mehta + nurse.asha; patient A signed in."""
    ep_a = make_episode(client, staff_ids, phone="+919000000101", name="Patient A")
    ep_b = make_episode(client, staff_ids, phone="+919000000102", name="Patient B")
    invite(client, ep_a["id"])
    invite(client, ep_b["id"])
    objs_a = _seed_episode_objects(ep_a["id"], staff_ids["dr.mehta"])
    objs_b = _seed_episode_objects(ep_b["id"], staff_ids["dr.mehta"])
    patient = make_client()
    api_post(patient, "/api/auth/patient/request-code", {"phone": "+919000000101"})
    code = latest_otp_code(patient, "+919000000101")
    assert api_post(patient, "/api/auth/patient/verify", {"phone": "+919000000101", "code": code}).status_code == 200
    return {"a": ep_a["id"], "b": ep_b["id"], "objs_a": objs_a, "objs_b": objs_b, "patient": patient}


# --------------------------------------------------------------------------- patients
def test_patient_cannot_access_other_patients_episode(world):
    p, b, ob = world["patient"], world["b"], world["objs_b"]
    for url in (
        f"/api/me/episodes/{b}/today",
        f"/api/me/episodes/{b}/instructions",
        f"/api/me/episodes/{b}/checkins",
        f"/api/me/episodes/{b}/messages",
        f"/api/me/episodes/{b}/followups",
        f"/api/me/checkins/{ob['checkin']}",
    ):
        r = p.get(url)
        assert r.status_code == 404, (url, r.status_code)
    r = api_post(p, f"/api/me/checkins/{ob['checkin']}/submit",
                 {"submission_id": "sub-other-0001", "answers": [{"question_id": "q1", "value": "ok"}]})
    assert r.status_code == 404
    r = api_put(p, f"/api/me/checkins/{ob['checkin']}/draft", {"answers": {"q1": "ok"}})
    assert r.status_code == 404
    r = api_post(p, f"/api/me/episodes/{b}/concerns", {"submission_id": "sub-other-0002", "text": "hi"})
    assert r.status_code == 404
    assert [e["id"] for e in p.get("/api/me/episodes").json()] == [world["a"]]


def test_patient_own_views_do_not_leak_internal_data(world):
    p, a = world["patient"], world["a"]
    today = p.get(f"/api/me/episodes/{a}/today")
    assert today.status_code == 200
    text = today.text.lower()
    for forbidden in ("risk_category", "risk_reasons", "triage_level", "rationale", "review_note", "actor_label"):
        assert forbidden not in text
    ci = p.get(f"/api/me/checkins/{world['objs_a']['checkin']}").json()
    assert "triage_level" not in ci and "triage_reasons" not in ci and "review_note" not in ci


def test_patient_cannot_call_staff_endpoints(world):
    p, a, oa = world["patient"], world["a"], world["objs_a"]
    for url in ("/api/episodes", f"/api/episodes/{a}", "/api/overview", "/api/approvals", "/api/settings",
                "/api/review-items", f"/api/episodes/{a}/activity", f"/api/episodes/{a}/notes", "/api/staff"):
        assert p.get(url).status_code == 403, url
    assert api_post(p, f"/api/actions/{oa['action']}/approve", {"payload_hash": "h" * 64}).status_code == 403
    assert api_post(p, f"/api/episodes/{a}/notes", {"body": "x"}).status_code == 403


# --------------------------------------------------------------------------- nurse vs doctor
def test_nurse_cannot_do_doctor_only_actions(client, world):
    login_staff(client, "nurse.asha")
    a, oa = world["a"], world["objs_a"]
    r = api_post(client, f"/api/actions/{oa['action']}/approve", {"payload_hash": "h" * 64})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert api_post(client, f"/api/actions/{oa['action']}/edit-approve",
                    {"base_payload_hash": "h" * 64, "payload": {"body": "x"}}).status_code == 403
    assert api_post(client, f"/api/actions/{oa['action']}/reject",
                    {"base_payload_hash": "h" * 64, "reason": "no"}).status_code == 403
    assert api_post(client, f"/api/actions/{oa['action']}/retry").status_code == 403
    assert api_post(client, f"/api/routine-authorisations/{oa['routine']}/revoke", {"reason": "x"}).status_code == 403
    assert api_post(client, f"/api/episodes/{a}/activate",
                    {"plan_version_id": oa["plan"], "confirm_fact_ids": []}).status_code == 403
    assert api_post(client, f"/api/episodes/{a}/close", {"note": "x"}).status_code == 403
    assert api_post(client, f"/api/episodes/{a}/return-to-draft", {"reason": "x"}).status_code == 403
    assert api_post(client, f"/api/review-items/{oa['review']}/resolve", {"note": "x"}).status_code == 403
    # ...but nurse-permitted actions on the same episode work.
    assert api_post(client, f"/api/review-items/{oa['review']}/acknowledge").status_code == 200
    assert api_post(client, f"/api/episodes/{a}/notes", {"body": "Called patient"}).status_code == 201


def test_doctor_cannot_create_episode_or_assign_team(client, staff_ids):
    login_staff(client, "dr.mehta")
    r = api_post(client, "/api/episodes", {
        "patient": {"full_name": "X", "phone_e164": "+919000000199"}, "discharge_at": "2026-11-11T00:00:00Z",
        "doctor_id": staff_ids["dr.mehta"], "summary_text": "x" * 30})
    assert r.status_code == 403


def test_nurse_assigns_team_and_validation(client, staff_ids):
    ep = make_episode(client, staff_ids)
    bad = api_put(client, f"/api/episodes/{ep['id']}/team",
                  {"doctor_id": staff_ids["nurse.ravi"], "nurse_id": staff_ids["nurse.asha"]})
    assert bad.status_code == 422  # a nurse cannot be made the doctor
    ok = api_put(client, f"/api/episodes/{ep['id']}/team",
                 {"doctor_id": staff_ids["dr.iyer"], "nurse_id": staff_ids["nurse.asha"]})
    assert ok.status_code == 200 and ok.json()["team"]["doctor"]["user_id"] == staff_ids["dr.iyer"]


# --------------------------------------------------------------------------- assignment scoping
def test_unassigned_staff_cannot_see_episode(client, world, make_client):
    a, oa = world["a"], world["objs_a"]
    for username in ("dr.iyer", "nurse.ravi"):
        c = make_client()
        login_staff(c, username)
        assert c.get(f"/api/episodes/{a}").status_code == 404
        assert c.get(f"/api/episodes/{a}/activity").status_code == 404
        assert c.get(f"/api/actions/{oa['action']}").status_code == 404
        assert c.get("/api/episodes").json() == []
        assert c.get("/api/approvals").json() == []
        assert c.get("/api/review-items").json() == []
        assert c.get("/api/overview").json()["counts"]["open_reviews"] == 0
    c = make_client()
    login_staff(c, "dr.iyer")
    # Doctor-only action on an unassigned episode: 404 (visibility first), not 403.
    assert api_post(c, f"/api/actions/{oa['action']}/approve", {"payload_hash": "h" * 64}).status_code == 404
    assert api_post(c, f"/api/routine-authorisations/{oa['routine']}/revoke", {"reason": "x"}).status_code == 404


def test_assigned_doctor_can_see(client, world):
    login_staff(client, "dr.mehta")
    assert client.get(f"/api/episodes/{world['a']}").status_code == 200
    ids = {e["id"] for e in client.get("/api/episodes").json()}
    assert {world["a"], world["b"]} <= ids


def test_unauthenticated_is_401(make_client, world):
    c = make_client()
    for url in ("/api/episodes", "/api/overview", "/api/me/episodes", f"/api/me/episodes/{world['a']}/today",
                "/api/settings", "/api/clock", "/api/stream"):
        assert c.get(url).status_code == 401, url


def test_missing_csrf_header_is_403(client, world):
    login_staff(client, "nurse.asha")
    r = client.post(f"/api/episodes/{world['a']}/notes", json={"body": "x"})
    assert r.status_code == 403
