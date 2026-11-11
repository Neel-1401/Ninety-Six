"""SSE: Last-Event-ID replay, access filtering, patient scrubbing (CONTRACTS.md §7)."""
from __future__ import annotations

import json

from app import db
from app.api.stream import filter_notice
from app.events import record_event
from tests.conftest import latest_otp_code, login_staff, api_post
from tests.test_auth import invite, make_episode


def _frames(body: str) -> list[dict]:
    out = []
    for block in body.split("\n\n"):
        data = [ln[6:] for ln in block.splitlines() if ln.startswith("data: ")]
        ids = [ln[4:] for ln in block.splitlines() if ln.startswith("id: ")]
        if data:
            d = json.loads(data[0])
            assert ids and int(ids[0]) == d["id"]
            out.append(d)
    return out


def test_filter_notice_rules():
    n = {"id": 5, "kind": "action.approved", "episode_id": 1, "scopes": ["actions", "messages"], "patient_visible": True, "at": "x"}
    assert filter_notice(n, is_patient=False, episode_ids={1})["kind"] == "action.approved"
    assert filter_notice(n, is_patient=False, episode_ids={2}) is None
    p = filter_notice(n, is_patient=True, episode_ids={1})
    assert p == {"id": 5, "kind": "update", "episode_id": 1, "scopes": ["messages"], "at": "x"}
    assert filter_notice({**n, "patient_visible": False}, is_patient=True, episode_ids={1}) is None
    assert filter_notice({**n, "scopes": ["actions"]}, is_patient=True, episode_ids={1}) is None
    assert filter_notice({**n, "episode_id": None}, is_patient=True, episode_ids={1}) is None
    assert filter_notice({**n, "episode_id": None}, is_patient=False, episode_ids=set())["id"] == 5


def test_replay_after_last_event_id_is_filtered(client, staff_ids, make_client):
    a = make_episode(client, staff_ids, phone="+919000000201", name="Stream A")
    b = make_episode(client, staff_ids, phone="+919000000202", name="Stream B")
    invite(client, a["id"])
    with db.session_scope() as s:
        e1 = record_event(s, "message.sent", "to A", episode_id=a["id"], scopes=["messages"], patient_visible=True)
        e2 = record_event(s, "action.proposed", "internal A", episode_id=a["id"], scopes=["actions"])
        e3 = record_event(s, "message.sent", "to B", episode_id=b["id"], scopes=["messages"], patient_visible=True)
    first = e1.id - 1

    # Assigned staff: sees all three, in order, with real kinds.
    nurse = make_client()
    login_staff(nurse, "nurse.asha")
    r = nurse.get(f"/api/stream?replay_only=true", headers={"Last-Event-ID": str(first)})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    got = [f["id"] for f in _frames(r.text)]
    assert [e1.id, e2.id, e3.id] == [i for i in got if i in (e1.id, e2.id, e3.id)]

    # Unassigned staff: none of them.
    other = make_client()
    login_staff(other, "dr.iyer")
    got = [f["id"] for f in _frames(other.get(f"/api/stream?replay_only=true&last_event_id={first}").text)]
    assert not set(got) & {e1.id, e2.id, e3.id}

    # Patient A: only the patient-visible event of their own episode, scrubbed.
    p = make_client()
    api_post(p, "/api/auth/patient/request-code", {"phone": "+919000000201"})
    api_post(p, "/api/auth/patient/verify", {"phone": "+919000000201", "code": latest_otp_code(p, "+919000000201")})
    frames = _frames(p.get(f"/api/stream?replay_only=true&last_event_id={first}").text)
    assert [f["id"] for f in frames] == [e1.id]
    assert frames[0]["kind"] == "update" and frames[0]["scopes"] == ["messages"]
    assert "to A" not in p.get(f"/api/stream?replay_only=true&last_event_id={first}").text
