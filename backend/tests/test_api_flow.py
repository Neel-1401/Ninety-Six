"""Milestone 1 end-to-end through the HTTP API:
nurse intake → submit → doctor activates (routine scope) → doctor approves welcome message →
worker runs → simulated message recorded → nurse invites patient → patient signs in with a code →
reviewed instructions → clock advances to the first check-in → patient submits → receipt.

Marked xfail automatically while Agent B's services are still stubs (never weakened)."""
from __future__ import annotations

import inspect
import uuid
from datetime import timedelta

import pytest

from app.services import agent_ops
from tests.conftest import api_post, latest_otp_code, login_staff


def _b_ready() -> bool:
    try:
        return "raise NotImplementedError" not in inspect.getsource(agent_ops.activate_plan)
    except (OSError, TypeError):
        return True


pytestmark = pytest.mark.xfail(not _b_ready(), reason="awaiting agent B: agent_ops services", strict=False)


def _run_worker():
    from app.execution import worker

    return worker.run_until_idle()


def _sample(client):
    samples = client.get("/api/samples").json()
    return next(s for s in samples if s.get("primary"))


def test_milestone1_flow(client, staff_ids, make_client):
    from app.clock import clock, iso

    # ---- nurse intake from the primary (heart failure) sample
    login_staff(client, "nurse.asha")
    sample = _sample(client)
    patient = dict(sample["patient"])
    hours_ago = float(sample.get("discharge_hours_ago") or 2)
    body = {
        "patient": patient,
        "discharge_at": iso(clock.now() - timedelta(hours=hours_ago)),
        "doctor_id": staff_ids["dr.mehta"],
        "risk_inputs": sample.get("risk_inputs") or {},
        "summary_text": sample["summary_text"],
        "sample_key": sample["key"],
    }
    r = api_post(client, "/api/episodes", body)
    assert r.status_code == 201, r.text
    ep = r.json()
    eid = ep["id"]
    assert ep["status"] == "draft"
    assert ep["facts"], "extraction produced no facts"
    assert all(f["source_ref"] or f["is_missing"] for f in ep["facts"] if f["source_kind"] == "extracted")

    r = api_post(client, f"/api/episodes/{eid}/submit")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "awaiting_review"
    plan = client.get(f"/api/episodes/{eid}/plans/current").json()
    assert plan["status"] == "draft" and plan["steps"]

    # ---- doctor reviews extraction + plan and activates with a bounded routine scope
    doc = make_client()
    login_staff(doc, "dr.mehta")
    detail = doc.get(f"/api/episodes/{eid}").json()
    plan = doc.get(f"/api/episodes/{eid}/plans/current").json()
    reminder_offsets = sorted(
        {s["offset_hours"] for s in plan["steps"] if (s["arguments"] or {}).get("template_id") == "checkin_reminder_v1"}
    ) or [12, 24, 48, 72]
    r = api_post(doc, f"/api/episodes/{eid}/activate", {
        "plan_version_id": plan["id"],
        "confirm_fact_ids": [f["id"] for f in detail["facts"]],
        "routine": {"template_ids": ["checkin_reminder_v1"], "recipient_roles": ["patient"],
                    "offsets_hours": reminder_offsets, "max_per_day": 3},
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    assert r.json()["routine_authorisations"][0]["status"] == "active"
    _run_worker()

    # Medication-combination review (if the agent raised one) gates the welcome message: doctor resolves it.
    for item in doc.get(f"/api/episodes/{eid}/review-items").json():
        if item["kind"] == "medication_review" and item["status"] != "resolved":
            assert api_post(doc, f"/api/review-items/{item['id']}/resolve",
                            {"note": "Regimen confirmed as prescribed"}).status_code == 200
    _run_worker()

    # ---- doctor approves the welcome message (bound to the exact payload hash)
    queue = doc.get("/api/approvals").json()
    welcome = next(a for a in queue if a["episode_id"] == eid and a["template_id"] == "welcome_v1")
    assert welcome["effective_recipient"] and welcome["expected_mode"] == "simulated"
    r = api_post(doc, f"/api/actions/{welcome['id']}/approve", {"payload_hash": welcome["payload_hash"]})
    assert r.status_code == 200, r.text
    again = api_post(doc, f"/api/actions/{welcome['id']}/approve", {"payload_hash": welcome["payload_hash"]})
    assert again.status_code in (200, 409)  # duplicate click never creates a second decision/execution
    _run_worker()
    _run_worker()  # repeated ticks
    msgs = [m for m in doc.get(f"/api/episodes/{eid}/messages").json() if m["action_id"] == welcome["id"]]
    assert len(msgs) == 1, msgs
    assert msgs[0]["mode"] == "simulated" and msgs[0]["status"] == "simulated"
    action = doc.get(f"/api/actions/{welcome['id']}").json()
    assert action["execution_state"] == "succeeded"
    assert len([d for d in action["decisions"] if d["decision"] == "approved"]) == 1

    # ---- nurse invites the patient; patient signs in with a one-time code
    inv = api_post(client, f"/api/episodes/{eid}/invite")
    assert inv.status_code == 200, inv.text
    phone = detail["patient"]["phone_e164"]
    p = make_client()
    assert api_post(p, "/api/auth/patient/request-code", {"phone": phone}).status_code == 202
    assert api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": latest_otp_code(p, phone)}).status_code == 200

    instr = p.get(f"/api/me/episodes/{eid}/instructions").json()
    assert instr["reviewed"] is True and instr["reviewed_by"] == "Dr. Arjun Mehta"
    assert instr["medicines"], "reviewed medicines should be visible to the patient"
    msgs = p.get(f"/api/me/episodes/{eid}/messages").json()
    assert any(m["kind"] == "welcome" for m in msgs)

    # ---- advance the simulated clock until the first check-in is due
    due = None
    for _ in range(16):
        today = p.get(f"/api/me/episodes/{eid}/today").json()
        if today["due_checkin"]:
            due = today["due_checkin"]
            break
        r = api_post(client, "/api/demo/advance", {"hours": 6})
        assert r.status_code == 200, r.text
    assert due is not None, "no check-in became due"
    assert due["questions"]

    answers = [{"question_id": q["id"], "value": q["options"][0]["value"]}
               for q in due["questions"] if q["type"] in ("single_choice", "yes_no")]
    sub_id = str(uuid.uuid4())
    r = api_post(p, f"/api/me/checkins/{due['id']}/submit", {"submission_id": sub_id, "answers": answers, "free_text": None})
    assert r.status_code == 202, r.text
    receipt = r.json()["receipt"]
    assert receipt["received"] is True
    assert receipt["staff_review"] in ("not_requested", "awaiting")  # never "reviewed" from agent processing
    dup = api_post(p, f"/api/me/checkins/{due['id']}/submit", {"submission_id": sub_id, "answers": answers})
    assert dup.status_code == 200 and dup.json()["id"] == due["id"]
    _run_worker()
    after = p.get(f"/api/me/checkins/{due['id']}").json()
    assert after["receipt"]["agent_processing"] == "complete"
    assert after["receipt"]["staff_review"] != "reviewed"
