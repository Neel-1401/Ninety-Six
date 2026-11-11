"""Server-side sessions. The cookie holds a random token; only its sha256 is stored."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import clock
from ..config import settings
from ..models import AuthSession, Role, User
from .security import new_token, sha256_hex

COOKIE_NAME = "ns_session"


@dataclass
class Actor:
    user: User
    auth: AuthSession

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def is_staff(self) -> bool:
        return self.user.role in Role.STAFF

    @property
    def is_doctor(self) -> bool:
        return self.user.role == Role.DOCTOR

    @property
    def is_patient(self) -> bool:
        return self.user.role == Role.PATIENT

    @property
    def label(self) -> str:
        return self.user.display_name


def create_session(session: Session, user: User, *, via: str, user_agent: Optional[str] = None) -> str:
    """Create a session row (flushes) and return the raw cookie token."""
    hours = settings.patient_session_hours if user.role == Role.PATIENT else settings.staff_session_hours
    token = new_token(32)
    now = clock.real_now()
    session.add(
        AuthSession(
            token_hash=sha256_hex(token),
            user_id=user.id,
            via=via,
            created_at=now,
            expires_at=now + timedelta(hours=hours),
            last_seen_at=now,
            user_agent=(user_agent or "")[:255] or None,
        )
    )
    user.last_login_at = now
    session.flush()
    return token


def resolve(session: Session, token: Optional[str]) -> Optional[Actor]:
    if not token or len(token) > 200:
        return None
    row = session.scalar(select(AuthSession).where(AuthSession.token_hash == sha256_hex(token)))
    if row is None or row.revoked_at is not None:
        return None
    now = clock.real_now()
    if row.expires_at <= now:
        return None
    if row.via == "demo" and not settings.demo_mode:
        return None  # demo sessions never work outside demo mode
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    if row.last_seen_at is None or (now - row.last_seen_at) > timedelta(minutes=5):
        row.last_seen_at = now
        session.commit()
    return Actor(user=user, auth=row)


def revoke(session: Session, actor: Actor) -> None:
    actor.auth.revoked_at = clock.real_now()
    session.flush()


def set_cookie(response: Response, token: str, user: User) -> None:
    hours = settings.patient_session_hours if user.role == Role.PATIENT else settings.staff_session_hours
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(hours * 3600),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=settings.cookie_secure)
