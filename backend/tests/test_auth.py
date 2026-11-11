"""Authentication: staff login, sessions, CSRF, patient invitation + one-time code, demo mode gate."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app import db
from app.config import settings
from app.models import Message, OtpChallenge
from tests.conftest import CSRF, api_post, latest_otp_code, login_staff

SUMMARY = """DISCHARGE SUMMARY
Diagnosis: Acute decompensated heart failure
Allergies: Sulfa drugs
Medications:
- Furosemide 40 mg once daily in the morning
Follow-up:
- Cardiology clinic review within 7 days (Dr. Mehta)
Red flags:
- Breathlessness at rest or when lying flat
"""


def make_episode(client, staff_ids, phone="+919000000101", name="Test Patient One"):
    from app.clock import clock, iso

    login_staff(client, "nurse.asha")
    r = api_post(
        client,
        "/api/episodes",
        {
            "patient": {"full_name": name, "age": 60, "sex": "M", "phone_e164": phone, "preferred_language": "English"},
            "discharge_at": iso(clock.now() - timedelta(hours=1)),
            "doctor_id": staff_ids["dr.mehta"],
            "summary_text": SUMMARY,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def invite(client, episode_id):
    r = api_post(client, f"/api/episodes/{episode_id}/invite")
    assert r.status_code == 200, r.text
    return r.json()


def otp_messages(phone):
    with db.session_scope() as s:
        return s.scalars(select(Message).where(Message.kind == "otp", Message.intended_recipient == phone)).all()


# --------------------------------------------------------------------------- staff
def test_staff_login_and_generic_failure(client):
    r = api_post(client, "/api/auth/staff/login", {"username": "dr.mehta", "password": "wrong"})
    assert r.status_code == 401
    bad_user = api_post(client, "/api/auth/staff/login", {"username": "nobody", "password": "wrong"})
    assert bad_user.status_code == 401
    assert r.json()["error"]["message"] == bad_user.json()["error"]["message"]  # no account enumeration
    me = login_staff(client, "dr.mehta")
    assert me["user"]["role"] == "doctor" and me["session_via"] == "password"
    assert client.get("/api/auth/me").json()["user"]["username"] == "dr.mehta"


def test_cookie_flags(client):
    r = api_post(client, "/api/auth/staff/login", {"username": "dr.mehta", "password": "demo1234"})
    cookie = r.headers["set-cookie"].lower()
    assert "ns_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_unauthenticated_and_logout_revokes(client):
    assert client.get("/api/auth/me").status_code == 401
    login_staff(client, "nurse.asha")
    token = client.cookies.get("ns_session")
    assert api_post(client, "/api/auth/logout").status_code == 204
    client.cookies.set("ns_session", token)  # replay the old cookie
    assert client.get("/api/auth/me").status_code == 401


def test_csrf_header_required(client):
    r = client.post("/api/auth/staff/login", json={"username": "dr.mehta", "password": "demo1234"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# --------------------------------------------------------------------------- patient OTP
def test_patient_code_flow_single_use(client, staff_ids, make_client):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    r = api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    assert r.status_code == 202
    code = latest_otp_code(p, phone)
    ok = api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": code})
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["role"] == "patient" and ok.json()["session_via"] == "otp"
    assert [e["id"] for e in p.get("/api/me/episodes").json()] == [ep["id"]]
    # Reuse of the same code fails with the generic message.
    p2 = make_client()
    again = api_post(p2, "/api/auth/patient/verify", {"phone": phone, "code": code})
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_code"


def test_generic_response_does_not_reveal_registration(client, staff_ids, make_client):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    p = make_client()
    reg = api_post(p, "/api/auth/patient/request-code", {"phone": ep["patient"]["phone_e164"]})
    unreg = api_post(p, "/api/auth/patient/request-code", {"phone": "+919000009999"})
    assert reg.status_code == unreg.status_code == 202
    assert reg.json() == unreg.json()
    assert otp_messages("+919000009999") == []  # nothing is sent to an unregistered phone
    bad = api_post(p, "/api/auth/patient/verify", {"phone": "+919000009999", "code": "123456"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_code"


def test_expired_code_rejected(client, staff_ids, make_client):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    code = latest_otp_code(p, phone)
    with db.session_scope() as s:
        ch = s.scalars(select(OtpChallenge).where(OtpChallenge.phone_e164 == phone)).all()[-1]
        ch.expires_at = ch.created_at - timedelta(seconds=1)
    r = api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": code})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"


def test_simulated_clock_does_not_expire_or_extend_codes(client, staff_ids, make_client):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    code = latest_otp_code(p, phone)
    from app.clock import clock

    with db.session_scope() as s:
        clock.advance(s, 24)  # simulated time jump must not affect real-time OTP expiry
    r = api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": code})
    assert r.status_code == 200, r.text


def test_attempt_limit_locks_code(client, staff_ids, make_client):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    code = latest_otp_code(p, phone)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.otp_max_attempts):
        assert api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": wrong}).status_code == 400
    # Correct code no longer works after the attempt limit.
    r = api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": code})
    assert r.status_code == 400


def test_resend_cooldown(client, staff_ids, make_client):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    second = api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    assert second.status_code == 202
    assert second.json()["resend_after_seconds"] > 0
    assert len(otp_messages(phone)) == 1  # no second SMS during the cooldown


def test_rate_limit_per_phone(client, staff_ids, make_client, monkeypatch):
    monkeypatch.setattr(settings, "otp_resend_cooldown_seconds", 0)
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    statuses = [api_post(p, "/api/auth/patient/request-code", {"phone": phone}).status_code for _ in range(6)]
    assert statuses[:5] == [202] * 5 and statuses[5] == 429


def test_only_newest_code_works(client, staff_ids, make_client, monkeypatch):
    monkeypatch.setattr(settings, "otp_resend_cooldown_seconds", 0)
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    first = latest_otp_code(p, phone)
    api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    second = latest_otp_code(p, phone)
    if first != second:
        assert api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": first}).status_code == 400
    assert api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": second}).status_code == 200


def test_invitation_link_alone_grants_nothing(client, staff_ids, make_client):
    ep = make_episode(client, staff_ids)
    inv = invite(client, ep["id"])
    token = inv["link"].rsplit("/", 1)[-1]
    assert "Furosemide" not in inv["message"]["body"] and "heart" not in inv["message"]["body"].lower()
    p = make_client()
    assert p.get("/api/me/episodes").status_code == 401
    # Token with the wrong phone: generic response, no code sent.
    r = api_post(p, "/api/auth/patient/request-code", {"phone": "+919000008888", "invitation_token": token})
    assert r.status_code == 202
    assert otp_messages("+919000008888") == []


# --------------------------------------------------------------------------- demo / production gate
def test_demo_endpoints_disabled_in_production(client, staff_ids, monkeypatch):
    # A demo session created while in demo mode...
    me = api_post(client, "/api/demo/login-as", {"user_id": staff_ids["dr.mehta"]})
    assert me.status_code == 200 and me.json()["session_via"] == "demo"
    monkeypatch.setattr(settings, "app_mode", "production")
    assert api_post(client, "/api/demo/login-as", {"user_id": staff_ids["dr.mehta"]}).status_code == 404
    assert client.get("/api/demo/state").status_code == 404
    assert client.get("/api/demo/outbox").status_code == 404
    assert api_post(client, "/api/demo/advance", {"hours": 6}).status_code == 404
    assert api_post(client, "/api/demo/reset", {"confirm": "RESET"}).status_code == 404
    # ...stops working once the server is in production mode.
    assert client.get("/api/auth/me").status_code == 401


def test_otp_body_redacted_in_production(client, staff_ids, make_client, monkeypatch):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    monkeypatch.setattr(settings, "app_mode", "production")
    phone = ep["patient"]["phone_e164"]
    p = make_client()
    assert api_post(p, "/api/auth/patient/request-code", {"phone": phone}).status_code == 202
    msgs = otp_messages(phone)
    assert msgs and "••••••" in msgs[-1].body
    import re

    assert re.search(r"\b\d{6}\b", msgs[-1].body) is None


def test_reset_requires_confirmation_and_states_non_undo(client, staff_ids):
    login_staff(client, "nurse.asha")
    bad = api_post(client, "/api/demo/reset", {"confirm": "yes"})
    assert bad.status_code == 422
    r = api_post(client, "/api/demo/reset", {"confirm": "RESET"})
    assert r.status_code == 200, r.text
    notice = r.json()["notice"]
    assert "NOT undone" in notice and "Messages already sent" in notice
    # The caller is signed back in after the wipe.
    assert client.get("/api/auth/me").status_code == 200


def test_staff_cannot_self_register_or_pick_role(client):
    # There is no registration endpoint; unknown routes are 404 JSON.
    r = api_post(client, "/api/auth/staff/register", {"username": "x", "password": "y", "role": "doctor"})
    assert r.status_code == 404
    assert r.headers.get("content-type", "").startswith("application/json")
    assert CSRF  # header constant exported for other tests


# --------------------------------------------------------------------------- ACCEPTANCE A-04 regression
def test_phone_change_revokes_old_number_access(client, staff_ids, make_client):
    from tests.conftest import api_patch

    ep = make_episode(client, staff_ids, phone="+919000000301")
    invite(client, ep["id"])
    old = "+919000000301"
    # Holder of the (wrong) old number signs in and can read the episode.
    holder = make_client()
    api_post(holder, "/api/auth/patient/request-code", {"phone": old})
    assert api_post(holder, "/api/auth/patient/verify", {"phone": old, "code": latest_otp_code(holder, old)}).status_code == 200
    assert holder.get(f"/api/me/episodes/{ep['id']}/today").status_code == 200
    # A second, not yet used code for the old number is outstanding.
    pending = make_client()
    with db.session_scope() as s:  # bypass cooldown for the second request
        for ch in s.scalars(select(OtpChallenge).where(OtpChallenge.phone_e164 == old)).all():
            ch.created_at = ch.created_at - timedelta(minutes=5)
    api_post(pending, "/api/auth/patient/request-code", {"phone": old})
    unused_code = latest_otp_code(pending, old)

    detail = client.get(f"/api/episodes/{ep['id']}").json()
    r = api_patch(client, f"/api/episodes/{ep['id']}",
                  {"row_version": detail["row_version"], "patient": {"phone_e164": "+919000000302"}})
    assert r.status_code == 200, r.text
    assert r.json()["invitation"]["status"] == "revoked"

    # Old session is dead; the outstanding code fails; the old number can't get a new code.
    assert holder.get(f"/api/me/episodes/{ep['id']}/today").status_code == 401
    assert api_post(pending, "/api/auth/patient/verify", {"phone": old, "code": unused_code}).status_code == 400
    before = len(otp_messages(old))
    with db.session_scope() as s:
        for ch in s.scalars(select(OtpChallenge).where(OtpChallenge.phone_e164 == old)).all():
            ch.created_at = ch.created_at - timedelta(minutes=5)
    assert api_post(make_client(), "/api/auth/patient/request-code", {"phone": old}).status_code == 202
    assert len(otp_messages(old)) == before  # nothing sent to the old number
    # The new number needs a NEW invitation before it can sign in.
    newp = make_client()
    api_post(newp, "/api/auth/patient/request-code", {"phone": "+919000000302"})
    assert otp_messages("+919000000302") == []
    invite(client, ep["id"])
    with db.session_scope() as s:
        for ch in s.scalars(select(OtpChallenge).where(OtpChallenge.phone_e164 == "+919000000302")).all():
            ch.created_at = ch.created_at - timedelta(minutes=5)
    api_post(newp, "/api/auth/patient/request-code", {"phone": "+919000000302"})
    code = latest_otp_code(newp, "+919000000302")
    assert api_post(newp, "/api/auth/patient/verify", {"phone": "+919000000302", "code": code}).status_code == 200
    assert newp.get(f"/api/me/episodes/{ep['id']}/today").status_code == 200
    kinds = [e["kind"] for e in client.get(f"/api/episodes/{ep['id']}/activity").json()]
    assert "invite.revoked" in kinds


# --------------------------------------------------------------------------- ACCEPTANCE A-03 / A-14 / A-12
def test_unauthenticated_outbox_hides_clinical_messages(client, staff_ids, make_client):
    from app.models import Message as M

    ep = make_episode(client, staff_ids)
    with db.session_scope() as s:
        s.add(M(episode_id=ep["id"], kind="welcome", audience="patient", channel="sms", body="Furosemide 40 mg",
                mode="simulated", status="simulated", intended_recipient="+919000000101"))
    anon = make_client()
    kinds = {m["kind"] for m in anon.get("/api/demo/outbox").json()}
    assert "welcome" not in kinds
    assert "welcome" in {m["kind"] for m in client.get("/api/demo/outbox").json()}  # signed-in staff


def test_no_demo_phone_redirect_for_otp_outside_demo_mode(client, staff_ids, make_client, monkeypatch):
    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    monkeypatch.setattr(settings, "demo_phone", "+919999999999")
    monkeypatch.setattr(settings, "app_mode", "production")
    phone = ep["patient"]["phone_e164"]
    api_post(make_client(), "/api/auth/patient/request-code", {"phone": phone})
    msg = otp_messages(phone)[-1]
    assert msg.status == "failed" and "DEMO_PHONE" in (msg.error or "")


def test_patient_does_not_see_unapproved_proposed_slot(client, staff_ids, make_client):
    from app.clock import clock
    from app.models import FollowUpEvent
    from tests.test_permissions import _seed_episode_objects

    ep = make_episode(client, staff_ids)
    invite(client, ep["id"])
    objs = _seed_episode_objects(ep["id"], staff_ids["dr.mehta"])
    with db.session_scope() as s:
        s.add(FollowUpEvent(episode_id=ep["id"], action_id=objs["action"], title="Cardiology review",
                            status="proposed", start_at=clock.now() + timedelta(days=5), location="OPD 2"))
    p = make_client()
    phone = ep["patient"]["phone_e164"]
    api_post(p, "/api/auth/patient/request-code", {"phone": phone})
    api_post(p, "/api/auth/patient/verify", {"phone": phone, "code": latest_otp_code(p, phone)})
    f = p.get(f"/api/me/episodes/{ep['id']}/followups").json()[0]
    assert f["status"] == "proposed" and f["start_at"] is None and f["location"] is None
