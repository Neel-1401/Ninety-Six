"""Staff-wide routes: staff list, samples, overview, settings, clock, health."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.deps import assigned_episode_ids, current_actor, get_db, require_staff
from ..auth.security import mask_phone
from ..auth.sessions import Actor
from ..clock import clock, iso
from ..config import settings
from ..models import (
    ApprovalState,
    DischargeEpisode,
    EpisodeStatus,
    ExecutionState,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
    Role,
    User,
)
from ..schemas.serializers import (
    Names,
    action_out,
    episode_row,
    review_out,
    sort_actions,
    sort_reviews,
    user_out,
)

router = APIRouter(prefix="/api", tags=["staff"])


@router.get("/health")
def health():
    return {"ok": True, "mode": settings.app_mode}


@router.get("/clock")
def get_clock(actor: Actor = Depends(current_actor)):
    return {
        "now": iso(clock.now()),
        "sim_offset_hours": round(clock.offset_hours, 4),
        "demo_mode": settings.demo_mode,
        "display_timezone": settings.display_timezone,
    }


@router.get("/staff")
def list_staff(role: Optional[str] = None, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    q = select(User).where(User.role.in_(Role.STAFF), User.is_active.is_(True)).order_by(User.role, User.display_name)
    if role in Role.STAFF:
        q = q.where(User.role == role)
    return [user_out(u) for u in session.scalars(q).all()]


@router.get("/samples")
def samples(actor: Actor = Depends(require_staff)):
    try:
        from ..samples import SAMPLES  # Agent B
    except Exception:
        return []
    return SAMPLES


def _tool_statuses() -> list[dict]:
    try:
        from ..tools.registry import tool_statuses  # Agent B

        out = []
        for t in tool_statuses():
            d = t.__dict__.copy() if hasattr(t, "__dict__") else dict(t)
            out.append(d)
        return out
    except Exception:
        # Fallback: configuration-only view (no claim of a successful live call).
        def row(name, label, configured, detail):
            return {
                "name": name,
                "label": label,
                "mode": "live" if configured else "simulated",
                "configured": configured,
                "detail": detail,
                "last_live_success_at": None,
                "last_failure_at": None,
            }

        return [
            row("messaging", "SMS (Twilio)", settings.twilio_configured, "Twilio credentials " + ("set" if settings.twilio_configured else "not set")),
            row("calendar", "Google Calendar", settings.calendar_configured, "Service account " + ("set" if settings.calendar_configured else "not set")),
            row("medication_info", "Medication information (openFDA)", settings.openfda_enabled, "openFDA " + ("enabled" if settings.openfda_enabled else "disabled")),
            row("care_team_email", "Care-team email (SMTP)", settings.email_configured, "SMTP " + ("set" if settings.email_configured else "not set")),
        ]


def _llm_status() -> dict:
    try:
        from ..tools.registry import llm_status  # Agent B

        return dict(llm_status())
    except Exception:
        model = settings.anthropic_model if settings.llm_provider == "anthropic" else (
            settings.gemini_model if settings.llm_provider == "gemini" else None
        )
        return {
            "provider": settings.llm_provider,
            "mode": "live" if settings.llm_configured else "simulated",
            "model": model,
            "configured": settings.llm_configured,
        }


def _worker_last_tick() -> Optional[str]:
    try:
        from ..execution import worker  # Agent B

        return iso(worker.last_tick_at())
    except Exception:
        return None


@router.get("/settings")
def get_settings(actor: Actor = Depends(require_staff)):
    return {
        "app_mode": settings.app_mode,
        "demo_mode": settings.demo_mode,
        "hospital_name": settings.hospital_name,
        "emergency_number": settings.emergency_number,
        "display_timezone": settings.display_timezone,
        "sim_now": iso(clock.now()),
        "sim_offset_hours": round(clock.offset_hours, 4),
        "llm": _llm_status(),
        "tools": _tool_statuses(),
        "demo_phone_redirect": {"active": bool(settings.demo_phone), "masked": mask_phone(settings.demo_phone)},
        "worker": {"enabled": settings.worker_enabled, "last_tick_at": _worker_last_tick()},
    }


class LLMToggle(BaseModel):
    enabled: bool


@router.post("/settings/llm")
def toggle_llm(body: LLMToggle, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    """Instant AI kill switch (rules only). Any staff in demo mode; doctors only otherwise.
    Turning it back on only works if LLM_PROVIDER + key are set in backend/.env."""
    from .. import errors, llm
    from ..events import record_event

    if not settings.demo_mode and actor.role != "doctor":
        raise errors.forbidden("Only a doctor can switch the AI on or off.")
    llm.set_enabled(None if body.enabled else False)
    record_event(session, "ai.toggled", f"{actor.user.display_name} switched AI {'ON' if body.enabled else 'OFF'}"
                 + ("" if llm.is_configured() or not body.enabled else " (no key configured: rules only)"),
                 actor_type="user", actor_id=actor.id, actor_label=actor.user.display_name,
                 detail={"enabled": body.enabled, "llm": llm.status()}, scopes=["clock"])
    session.commit()
    return _llm_status()


@router.get("/overview")
def overview(session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    names = Names(session)
    now = clock.now()
    ids = assigned_episode_ids(session, actor)
    episodes = session.scalars(select(DischargeEpisode).where(DischargeEpisode.id.in_(ids))).all() if ids else []
    rows = {ep.id: episode_row(session, ep, names, now) for ep in episodes}

    reviews = (
        session.scalars(
            select(ReviewItem).where(
                ReviewItem.episode_id.in_(ids), ReviewItem.status.in_((ReviewStatus.OPEN, ReviewStatus.ACKNOWLEDGED))
            )
        ).all()
        if ids
        else []
    )
    reviews = sort_reviews(reviews)
    pending = []
    failed = []
    for ep in episodes:
        q = select(ProposedAction).where(ProposedAction.episode_id == ep.id)
        for a in session.scalars(q).all():
            if (
                a.approval_state == ApprovalState.PENDING
                and a.execution_state == ExecutionState.NOT_STARTED
                and (ep.current_plan_version_id is None or a.plan_version_id == ep.current_plan_version_id)
            ):
                pending.append(a)
            if a.execution_state in (ExecutionState.FAILED, ExecutionState.UNKNOWN):
                failed.append(a)

    def eps(status):  # noqa: ANN001, ANN202
        return [rows[e.id] for e in sorted(episodes, key=lambda e: e.updated_at, reverse=True) if e.status == status]

    needs_follow_up = [
        rows[e.id]
        for e in episodes
        if e.status in (EpisodeStatus.ACTIVE, EpisodeStatus.AWAITING_CLOSURE_REVIEW)
        and (rows[e.id]["counts"]["overdue_checkins"] or rows[e.id]["counts"]["open_reviews"])
    ]
    unassigned = [r for r in reviews if r.owner_id is None]
    counts = {
        "urgent_reviews": sum(1 for r in reviews if r.priority == "urgent"),
        "open_reviews": len(reviews),
        "pending_approvals": len(pending),
        "overdue_checkins": sum(r["counts"]["overdue_checkins"] for r in rows.values()),
        "failed_actions": sum(1 for a in failed if a.execution_state == ExecutionState.FAILED),
        "unknown_outcomes": sum(1 for a in failed if a.execution_state == ExecutionState.UNKNOWN),
        "unassigned": len(unassigned),
        "drafts": sum(1 for e in episodes if e.status == EpisodeStatus.DRAFT),
        "awaiting_review": sum(1 for e in episodes if e.status == EpisodeStatus.AWAITING_REVIEW),
        "active": sum(1 for e in episodes if e.status == EpisodeStatus.ACTIVE),
    }
    return {
        "role": actor.role,
        "counts": counts,
        "urgent_reviews": [review_out(session, r, names, now) for r in reviews if r.priority == "urgent"],
        "pending_approvals": [action_out(session, a, names) for a in sort_actions(pending)],
        "awaiting_review": eps(EpisodeStatus.AWAITING_REVIEW),
        "drafts": eps(EpisodeStatus.DRAFT),
        "needs_follow_up": needs_follow_up,
        "failed_actions": [action_out(session, a, names) for a in sort_actions(failed)],
        "unassigned_reviews": [review_out(session, r, names, now) for r in unassigned],
    }
