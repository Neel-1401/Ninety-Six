"""Episode intake/edit API and patient check-in submission (independent of agent services)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app import db
from app.clock import clock, iso
from app.models import CheckIn, CheckInResponse, FollowUpEvent, PatientFact
from tests.conftest import api_patch, api_post, api_put, login_staff
from tests.test_auth import SUMMARY, make_episode
from tests.test_permissions import world  # noqa: F401  (fixture)


def _body(staff_ids, **over):
    b = {
        "patient": {"full_name": "Intake Test", "age": 50, "phone_e164": "98765 43210"},
        "discharge_at": iso(clock.now() - timedelta(hours=2)),
        "doctor_id": staff_ids["dr.mehta"],
        "summary_text": SUMMARY,
    }
    b.update(over)
    return b


def test_intake_validation(client, staff_ids):
    login_staff(client, "nurse.asha")
    future = api_post(client, "/api/episodes", _body(staff_ids, discharge_at=iso(clock.now() + timedelta(hours=3))))
    assert future.status_code == 422 and future.json()["error"]["code"] == "validation_error"
    old = api_post(client, "/api/episodes", _body(staff_ids, discharge_at=iso(clock.now() - timedelta(hours=100))))
    assert old.status_code == 422
    naive = api_post(client, "/api/episodes", _body(staff_ids, discharge_at="2026-11-11T08:00:00"))
    assert naive.status_code == 422
    bad_phone = api_post(client, "/api/episodes", _body(staff_ids, patient={"full_name": "X", "phone_e164": "12"}))
    assert bad_phone.status_code == 422
    wrong_doctor = api_post(client, "/api/episodes", _body(staff_ids, doctor_id=staff_ids["nurse.ravi"]))
    assert wrong_doctor.status_code == 422
    ok = api_post(client, "/api/episodes", _body(staff_ids))
    assert ok.status_code == 201, ok.text
    d = ok.json()
    assert d["patient"]["phone_e164"] == "+919876543210"  # normalised to E.164
    assert d["team"]["doctor"]["user_id"] == staff_ids["dr.mehta"]
    assert d["team"]["nurse"]["user_id"] == staff_ids["nurse.asha"]
    assert d["document"]["text"] == SUMMARY
    assert d["permissions"]["can_submit"] is True and d["permissions"]["can_activate"] is False
    assert d["window_ends_at"] > d["discharge_at"]


def test_patch_uses_optimistic_concurrency(client, staff_ids):
    ep = make_episode(client, staff_ids)
    r = api_patch(client, f"/api/episodes/{ep['id']}", {"row_version": ep["row_version"], "patient": {"age": 61}})
    assert r.status_code == 200 and r.json()["patient"]["age"] == 61
    stale = api_patch(client, f"/api/episodes/{ep['id']}", {"row_version": ep["row_version"], "patient": {"age": 62}})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"


def test_fact_correction_supersedes_and_keeps_provenance(client, staff_ids):
    ep = make_episode(client, staff_ids)
    r = api_post(client, f"/api/episodes/{ep['id']}/facts",
                 {"category": "allergy", "label": "Allergy", "value": {"text": "Sulfa drugs"}})
    assert r.status_code == 201
    fact = r.json()
    assert fact["source_kind"] == "staff_entered" and fact["review_state"] == "unreviewed"
    c = api_patch(client, f"/api/episodes/{ep['id']}/facts/{fact['id']}",
                  {"value": {"text": "Sulfa drugs (rash)"}, "reason": "Clarified with patient"})
    assert c.status_code == 200, c.text
    new = c.json()
    assert new["supersedes_id"] == fact["id"] and new["source_kind"] == "staff_corrected"
    with db.session_scope() as s:
        old = s.get(PatientFact, fact["id"])
        assert old.active is False
    again = api_patch(client, f"/api/episodes/{ep['id']}/facts/{fact['id']}", {"value": {"text": "x"}})
    assert again.status_code == 409  # editing an already-superseded fact is refused
    memory = client.get(f"/api/episodes/{ep['id']}/memory").json()
    assert {f["id"] for f in memory["facts"]} >= {fact["id"], new["id"]}


def _due(world_, key="objs_a"):
    return world_[key]["checkin"]


def test_checkin_submit_validation_and_dedupe(world):  # noqa: F811
    p, cid = world["patient"], _due(world)
    missing = api_post(p, f"/api/me/checkins/{cid}/submit", {"submission_id": "sub-0000-0001", "answers": []})
    assert missing.status_code == 422
    assert missing.json()["error"]["details"]["fields"][0]["field"] == "q1"
    bad = api_post(p, f"/api/me/checkins/{cid}/submit",
                   {"submission_id": "sub-0000-0001", "answers": [{"question_id": "q1", "value": "nope"}]})
    assert bad.status_code == 422
    # Draft persists while open.
    d = api_put(p, f"/api/me/checkins/{cid}/draft", {"answers": {"q1": "ok"}, "free_text": "slept badly"})
    assert d.status_code == 200 and d.json()["draft"]["answers"] == {"q1": "ok"}
    assert p.get(f"/api/me/checkins/{cid}").json()["draft"]["free_text"] == "slept badly"

    good = {"submission_id": "sub-0000-0001", "answers": [{"question_id": "q1", "value": "ok"}], "free_text": "slept badly"}
    r = api_post(p, f"/api/me/checkins/{cid}/submit", good)
    assert r.status_code == 202, r.text
    rec = r.json()["receipt"]
    assert rec["received"] is True and rec["staff_review"] != "reviewed"
    assert r.json()["answers"][0]["label"] == "OK"
    # Same submission id → same receipt, no duplicate rows.
    again = api_post(p, f"/api/me/checkins/{cid}/submit", good)
    assert again.status_code == 200 and again.json()["id"] == cid
    other = api_post(p, f"/api/me/checkins/{cid}/submit", {**good, "submission_id": "sub-0000-0002"})
    assert other.status_code == 409 and other.json()["error"]["code"] == "already_submitted"
    with db.session_scope() as s:
        assert len(s.scalars(select(CheckInResponse).where(CheckInResponse.checkin_id == cid)).all()) == 1
        c = s.get(CheckIn, cid)
        # Answers persisted even though processing may not have run yet.
        assert c.status == "submitted" and c.free_text == "slept badly" and c.submitted_at is not None


def test_staff_mark_reviewed_is_distinct_from_processing(client, world):  # noqa: F811
    p, cid = world["patient"], _due(world)
    api_post(p, f"/api/me/checkins/{cid}/submit",
             {"submission_id": "sub-0000-0003", "answers": [{"question_id": "q1", "value": "ok"}]})
    assert p.get(f"/api/me/checkins/{cid}").json()["receipt"]["staff_review"] != "reviewed"
    login_staff(client, "nurse.asha")
    r = api_post(client, f"/api/checkins/{cid}/mark-reviewed", {"note": "Spoke to patient"})
    assert r.status_code == 200 and r.json()["reviewed_by"] == "Asha Nair"
    rec = p.get(f"/api/me/checkins/{cid}").json()["receipt"]
    assert rec["staff_review"] == "reviewed" and rec["staff_reviewed_at"]


def test_concern_submission_dedupes(world):  # noqa: F811
    p, a = world["patient"], world["a"]
    body = {"submission_id": "concern-0001", "text": "My ankles look swollen"}
    r = api_post(p, f"/api/me/episodes/{a}/concerns", body)
    assert r.status_code == 202 and r.json()["kind"] == "concern" and r.json()["receipt"]["received"]
    again = api_post(p, f"/api/me/episodes/{a}/concerns", body)
    assert again.status_code == 200 and again.json()["id"] == r.json()["id"]


def test_followup_confirmation_is_explicit(client, world):  # noqa: F811
    with db.session_scope() as s:
        f = FollowUpEvent(episode_id=world["a"], title="Cardiology review", status="calendar_event_created",
                          start_at=clock.now() + timedelta(days=5), calendar_mode="simulated")
        s.add(f)
        s.flush()
        fid = f.id
    p = world["patient"]
    before = p.get(f"/api/me/episodes/{world['a']}/followups").json()
    assert before[0]["status"] == "calendar_event_created"
    login_staff(client, "nurse.asha")
    r = api_post(client, f"/api/followups/{fid}/confirm", {"note": "OPD desk booked slot"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed_by_hospital"
    assert r.json()["confirmed_by"] == "Asha Nair"
    assert p.get(f"/api/me/episodes/{world['a']}/followups").json()[0]["status"] == "confirmed_by_hospital"


def test_review_assignment_must_target_assigned_staff(client, world, staff_ids):  # noqa: F811
    login_staff(client, "nurse.asha")
    rid = world["objs_a"]["review"]  # requires doctor
    assert api_post(client, f"/api/review-items/{rid}/assign", {"user_id": staff_ids["dr.iyer"]}).status_code == 422
    assert api_post(client, f"/api/review-items/{rid}/assign", {"user_id": staff_ids["nurse.asha"]}).status_code == 422
    r = api_post(client, f"/api/review-items/{rid}/assign", {"user_id": staff_ids["dr.mehta"]})
    assert r.status_code == 200 and r.json()["owner"]["display_name"] == "Dr. Arjun Mehta"
    ov = client.get("/api/overview").json()
    assert "counts" in ov and ov["role"] == "nurse"
