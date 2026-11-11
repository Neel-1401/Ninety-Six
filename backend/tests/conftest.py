"""Base fixtures (seeded by the lead; Agent A owns this file and may ADD fixtures, but must keep
`fresh_db` and `session` working for Agent B's tests).

Every test gets an isolated temporary SQLite database, demo mode, mock LLM, no network tools,
and the in-process worker disabled (tests drive the worker explicitly).

API helpers (Agent A):
    client                     FastAPI TestClient (lifespan run => staff seeded, demo mode)
    login_staff(client, user)  signs in a seeded staff account (password "demo1234")
    login_patient(client, phone) requests + verifies an OTP via the demo outbox
    api_post/api_put/api_patch add the CSRF header `X-Requested-With: ninety-six`
"""
from __future__ import annotations

import os

# Must be set before app.config is imported anywhere.
os.environ.update(
    {
        "APP_MODE": "demo",
        "LLM_PROVIDER": "mock",
        "OPENFDA_ENABLED": "false",
        "WORKER_ENABLED": "false",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
        "TWILIO_FROM": "",
        "DEMO_PHONE": "",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "",
        "GOOGLE_CALENDAR_ID": "",
        "SMTP_HOST": "",
        "CARE_TEAM_EMAIL": "",
        # Agent A: don't create background demo episodes when the test app starts.
        "NS_SEED_BACKGROUND": "false",
    }
)

import pytest  # noqa: E402

from app import db  # noqa: E402
from app.clock import clock  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

settings.reload()

CSRF = {"X-Requested-With": "ninety-six"}
DEMO_PASSWORD = "demo1234"


@pytest.fixture
def fresh_db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    db.configure(url)
    Base.metadata.create_all(db.engine)
    with db.session_scope() as s:
        clock.reset(s)
    yield url
    db.engine.dispose()


@pytest.fixture
def session(fresh_db):
    s = db.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------------- API fixtures (Agent A)
def _make_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app, base_url="http://testserver")


@pytest.fixture
def client(fresh_db):
    """TestClient with the app lifespan run (migrations stamped, staff seeded)."""
    settings.reload()
    with _make_client() as c:
        yield c
    settings.reload()


@pytest.fixture
def make_client(fresh_db):
    """Factory for extra independent clients (separate cookie jars) on the same database."""
    from fastapi.testclient import TestClient

    from app.main import app

    opened = []

    def _factory():
        c = TestClient(app, base_url="http://testserver")
        c.__enter__()
        opened.append(c)
        return c

    yield _factory
    for c in opened:
        c.__exit__(None, None, None)


def api_post(client, url, json=None, **kw):
    headers = {**CSRF, **kw.pop("headers", {})}
    return client.post(url, json=json if json is not None else {}, headers=headers, **kw)


def api_put(client, url, json=None, **kw):
    headers = {**CSRF, **kw.pop("headers", {})}
    return client.put(url, json=json if json is not None else {}, headers=headers, **kw)


def api_patch(client, url, json=None, **kw):
    headers = {**CSRF, **kw.pop("headers", {})}
    return client.patch(url, json=json if json is not None else {}, headers=headers, **kw)


def login_staff(client, username: str, password: str = DEMO_PASSWORD):
    r = api_post(client, "/api/auth/staff/login", {"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def latest_otp_code(client, phone: str) -> str:
    """Read the newest simulated OTP for `phone` from the demo outbox (demo mode only)."""
    import re

    r = client.get("/api/demo/outbox")
    assert r.status_code == 200, r.text
    for item in r.json():  # newest first
        if item["kind"] == "otp" and (item.get("to_masked") or "").endswith(phone[-4:]):
            m = re.search(r"\b(\d{6})\b", item["body"])
            if m:
                return m.group(1)
    raise AssertionError("no OTP found in demo outbox")


def login_patient(client, phone: str, invitation_token: str | None = None):
    body = {"phone": phone}
    if invitation_token:
        body["invitation_token"] = invitation_token
    r = api_post(client, "/api/auth/patient/request-code", body)
    assert r.status_code == 202, r.text
    code = latest_otp_code(client, phone)
    r = api_post(client, "/api/auth/patient/verify", {"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def staff_ids(client):
    """Map username -> user id for the seeded staff."""
    from app.models import User

    with db.session_scope() as s:
        return {u.username: u.id for u in s.query(User).filter(User.username.isnot(None)).all()}
