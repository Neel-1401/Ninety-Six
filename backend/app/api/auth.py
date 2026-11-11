"""Authentication routes (CONTRACTS.md §3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..auth import otp, ratelimit, sessions
from ..auth.deps import current_actor, get_db, optional_actor
from ..auth.security import verify_secret
from ..auth.sessions import Actor
from ..models import Role, User
from ..schemas.requests import RequestCodeIn, StaffLoginIn, VerifyCodeIn
from ..schemas.serializers import me_out
from .common import audit, client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/staff/login")
def staff_login(body: StaffLoginIn, request: Request, response: Response, session: Session = Depends(get_db)):
    ratelimit.check_and_hit(session, "staff_login_ip", client_ip(request))
    ratelimit.check_and_hit(session, "staff_login_user", body.username.lower())
    user = session.scalar(select(User).where(User.username == body.username.lower()))
    ok = verify_secret(body.password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active or user.role not in Role.STAFF:
        session.commit()  # keep the rate-limit hit
        raise errors.ApiError(401, "invalid_credentials", "That username and password didn't match.")
    token = sessions.create_session(session, user, via="password", user_agent=request.headers.get("user-agent"))
    actor = Actor(user=user, auth=None)  # type: ignore[arg-type]
    audit(session, actor, "auth.login", f"{user.display_name} signed in")
    session.commit()
    sessions.set_cookie(response, token, user)
    return me_out(user, "password")


@router.post("/logout", status_code=204)
def logout(response: Response, session: Session = Depends(get_db), actor: Actor | None = Depends(optional_actor)):
    if actor is not None:
        sessions.revoke(session, actor)
        audit(session, actor, "auth.logout", f"{actor.label} signed out")
        session.commit()
    sessions.clear_cookie(response)
    response.status_code = 204
    return response


@router.get("/me")
def me(actor: Actor = Depends(current_actor)):
    return me_out(actor.user, actor.auth.via)


@router.post("/patient/request-code", status_code=202)
def request_code(body: RequestCodeIn, request: Request, session: Session = Depends(get_db)):
    return otp.request_code(session, phone_raw=body.phone, invitation_token=body.invitation_token, ip=client_ip(request))


@router.post("/patient/verify")
def verify(body: VerifyCodeIn, request: Request, response: Response, session: Session = Depends(get_db)):
    user = otp.verify_code(session, phone_raw=body.phone, code=body.code, ip=client_ip(request))
    token = sessions.create_session(session, user, via="otp", user_agent=request.headers.get("user-agent"))
    audit(session, Actor(user=user, auth=None), "auth.login", "Patient signed in with a one-time code")  # type: ignore[arg-type]
    session.commit()
    sessions.set_cookie(response, token, user)
    return me_out(user, "otp")
