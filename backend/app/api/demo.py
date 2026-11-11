"""Demo-only routes (CONTRACTS.md §4.4). Every route returns 404 `demo_disabled` unless
APP_MODE=demo. These are deliberately convenient (role switching, visible codes) and therefore
must never be reachable in production mode."""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from .. import errors
from ..auth import sessions
from ..auth.deps import (
    current_actor,
    episode_for_patient,
    episode_for_staff,
    get_db,
    optional_actor,
    patient_episode_ids,
)
from ..auth.security import mask_phone
from ..auth.sessions import Actor
from ..clock import clock, iso
from ..config import settings
from ..models import (
    Base,
    CheckIn,
    CheckInStatus,
    DischargeEpisode,
    Message,
    Mode,
    Role,
    StaffAssignment,
    User,
)
from ..schemas.requests import AdvanceIn, LoginAsIn, PatientResponseIn, ResetIn
from ..schemas.serializers import me_out, p_checkin_out
from ..services import agent_ops
from .common import audit, kick_worker
from .patient import create_concern, submit_checkin
from .staff import _tool_statuses

log = logging.getLogger("ninety_six.api.demo")
router = APIRouter(prefix="/api/demo", tags=["demo"])

RESET_NOTICE = (
    "Local demo data and the simulated clock were reset. Messages already sent and calendar events "
    "already created by live integrations were NOT undone."
)
PRESET_CONCERN_TEXT = {
    "all_good": "Feeling fine today, taking my tablets.",
    "concern": "I missed my evening tablets yesterday and feel a bit dizzy when I stand up.",
    "red_flag": "I get breathless when I lie flat at night and my ankles are more swollen.",
}


def demo_only() -> None:
    if not settings.demo_mode:
        raise errors.demo_disabled()


@router.get("/state", dependencies=[Depends(demo_only)])
def state(session: Session = Depends(get_db)):
    accounts = []
    for u in session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.role, User.id)).all():
        episode_id = None
        if u.role == Role.PATIENT and u.patient_id:
            episode_id = session.scalar(
                select(DischargeEpisode.id).where(DischargeEpisode.patient_id == u.patient_id).order_by(DischargeEpisode.id.desc())
            )
        accounts.append({"user_id": u.id, "role": u.role, "display_name": u.display_name, "episode_id": episode_id})
    return {
        "sim_now": iso(clock.now()),
        "sim_offset_hours": round(clock.offset_hours, 4),
        "tools": _tool_statuses(),
        "accounts": accounts,
    }


@router.post("/login-as", dependencies=[Depends(demo_only)])
def login_as(body: LoginAsIn, request: Request, response: Response, session: Session = Depends(get_db),
             actor: Actor | None = Depends(optional_actor)):
    user = session.get(User, body.user_id)
    if user is None or not user.is_active:
        raise errors.not_found("Account not found.")
    if actor is not None:
        sessions.revoke(session, actor)
    token = sessions.create_session(session, user, via="demo", user_agent=request.headers.get("user-agent"))
    audit(session, Actor(user=user, auth=None), "auth.login", f"Demo role switch: {user.display_name}")  # type: ignore[arg-type]
    session.commit()
    sessions.set_cookie(response, token, user)
    return me_out(user, "demo")


@router.post("/advance", dependencies=[Depends(demo_only)])
def advance(body: AdvanceIn, session: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    try:
        result = agent_ops.advance_clock(session, float(body.hours))
    except NotImplementedError:
        clock.advance(session, float(body.hours))
        audit(session, actor, "clock.advanced", f"Simulated clock advanced {body.hours} h", scopes=["clock"],
              patient_visible=False)
        session.commit()
        result = {"sim_now": iso(clock.now()), "ran": 0, "skipped_stale": 0, "awaiting_approval": 0,
                  "notes": ["Worker not available yet: no tasks were run."]}
    kick_worker()
    return result


@router.get("/outbox", dependencies=[Depends(demo_only)])
def outbox(session: Session = Depends(get_db), actor: Actor | None = Depends(optional_actor)):
    q = select(Message).where(Message.mode == Mode.SIMULATED)
    if actor is None:
        # Signed out (e.g. the patient sign-in screen): only sign-in codes and invitations.
        q = q.where(Message.kind.in_(("otp", "invitation")))
    elif actor.is_patient:
        q = q.where(Message.episode_id.in_(patient_episode_ids(session, actor)))
    rows = session.scalars(q.order_by(Message.id.desc()).limit(50)).all()
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "to_masked": mask_phone(m.effective_recipient or m.intended_recipient) if m.channel != "email"
            else (m.effective_recipient or m.intended_recipient),
            "body": m.body,
            "mode": m.mode,
            "status": m.status,
            "created_at": iso(m.created_at),
        }
        for m in rows
    ]


@router.post("/episodes/{episode_id}/patient-response", dependencies=[Depends(demo_only)])
def patient_response(episode_id: int, body: PatientResponseIn, session: Session = Depends(get_db),
                     actor: Actor = Depends(current_actor)):
    ep = episode_for_patient(session, actor, episode_id) if actor.is_patient else episode_for_staff(session, actor, episode_id)
    due = session.scalars(
        select(CheckIn)
        .where(CheckIn.episode_id == ep.id, CheckIn.kind == "scheduled", CheckIn.status == CheckInStatus.DUE)
        .order_by(CheckIn.due_at.desc())
    ).first()
    label = "Patient (demo test response)"
    if due is not None:
        try:
            answers, free_text = agent_ops.preset_checkin_answers(due, body.preset)
        except NotImplementedError:
            raise errors.ApiError(501, "not_implemented", "Preset answers are not available yet.")
        c, _ = submit_checkin(session, due, submission_id=f"demo-{uuid.uuid4()}", answers=answers, free_text=free_text,
                              actor_type="patient", actor_id=None, actor_label=label)
    else:
        c, _ = create_concern(session, ep, submission_id=f"demo-{uuid.uuid4()}", text=PRESET_CONCERN_TEXT[body.preset],
                              actor_type="patient", actor_id=None, actor_label=label)
    return p_checkin_out(session, c)


def wipe_all(session: Session) -> None:
    """Delete every row (FK-safe order). Schema and alembic_version are kept."""
    session.execute(text("UPDATE discharge_episode SET current_plan_version_id = NULL"))
    for table in reversed(Base.metadata.sorted_tables):
        for col in table.columns:
            for fk in col.foreign_keys:
                if fk.column.table is table and col.nullable:
                    session.execute(update(table).values({col.name: None}))
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.flush()


@router.post("/reset", dependencies=[Depends(demo_only)])
def reset(body: ResetIn, request: Request, response: Response, session: Session = Depends(get_db),
          actor: Actor = Depends(current_actor)):
    if body.confirm != "RESET":
        raise errors.validation('Type RESET to confirm.', {"fields": [{"field": "confirm", "message": 'Must be "RESET"'}]})
    username = actor.user.username if actor.is_staff else None
    by_label = actor.label
    from ..seed_accounts import seed_staff

    wipe_all(session)
    session.expunge_all()  # rows were deleted with Core statements; drop stale identities
    clock.reset(session)
    seed_staff(session)
    session.commit()
    try:
        agent_ops.seed_background_episodes(session)
        session.commit()
    except NotImplementedError:
        session.rollback()
    except Exception:  # never leave the demo half-reset without saying so
        log.exception("seed_background_episodes failed during reset")
        session.rollback()
    audit(session, None, "demo.reset", "Demo data reset", detail={"by": by_label},
          scopes=["overview", "episodes", "clock", "settings", "approvals", "reviews"])
    session.commit()
    me = None
    if username:
        user = session.scalar(select(User).where(User.username == username))
        if user is not None:
            token = sessions.create_session(session, user, via="demo", user_agent=request.headers.get("user-agent"))
            session.commit()
            sessions.set_cookie(response, token, user)
            me = me_out(user, "demo")
    else:
        sessions.clear_cookie(response)
    kick_worker()
    return {"ok": True, "notice": RESET_NOTICE, "me": me}
