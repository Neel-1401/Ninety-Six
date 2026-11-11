"""96-hour episode summary (Agent B). Factual record of what happened; never a clearance."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import clock, iso
from ..models import (
    CheckIn,
    DischargeEpisode,
    FollowUpEvent,
    Message,
    PlanStep,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
)
from .memory import active_facts, log_usage
from .plan_store import current_plan, steps_of

STATEMENT = "The 96-hour monitoring window has ended. This is not a medical clearance."


def build_summary(session: Session, episode: DischargeEpisode) -> dict[str, Any]:
    pv = current_plan(session, episode)
    steps: list[PlanStep] = steps_of(session, pv) if pv else []
    completed = [{"step_key": s.step_key, "title": s.title, "executed_at": iso(s.executed_at)}
                 for s in steps if s.status == "executed"]
    missed = [{"step_key": s.step_key, "title": s.title, "status": s.status, "reason": s.result_summary}
              for s in steps if s.status in ("skipped", "failed", "unknown", "cancelled", "awaiting_approval", "planned", "blocked", "ready")]
    checkins = session.scalars(select(CheckIn).where(CheckIn.episode_id == episode.id).order_by(CheckIn.due_at)).all()
    reviews = session.scalars(select(ReviewItem).where(ReviewItem.episode_id == episode.id)).all()
    msgs = session.scalars(select(Message).where(Message.episode_id == episode.id, Message.kind != "otp")).all()
    fus = session.scalars(select(FollowUpEvent).where(FollowUpEvent.episode_id == episode.id)).all()
    actions = session.scalars(select(ProposedAction).where(ProposedAction.episode_id == episode.id)).all()
    facts = active_facts(session, episode.id)
    return {
        "generated_at": iso(clock.now()),
        "window": {"start": iso(episode.discharge_at), "end": iso(episode.window_ends_at)},
        "completed_steps": completed,
        "missed_or_skipped": missed,
        "checkins": [
            {"id": c.id, "title": c.title, "due_at": iso(c.due_at), "status": c.status,
             "submitted_at": iso(c.submitted_at), "triage_level": c.triage_level, "reasons": list(c.triage_reasons or []),
             "staff_review_state": c.staff_review_state}
            for c in checkins
        ],
        "escalations": [
            {"id": r.id, "title": r.title, "priority": r.priority, "status": r.status, "created_at": iso(r.created_at)}
            for r in reviews if r.kind in ("urgent_concern", "concern")
        ],
        "unresolved_review_items": [
            {"id": r.id, "kind": r.kind, "title": r.title, "priority": r.priority, "status": r.status}
            for r in reviews if r.status != ReviewStatus.RESOLVED and r.kind != "closure_review"
        ],
        "messages": {"by_status": dict(Counter(m.status for m in msgs)), "total": len(msgs)},
        "actions": {"by_approval_state": dict(Counter(a.approval_state for a in actions)),
                    "by_execution_state": dict(Counter(a.execution_state for a in actions))},
        "followups": [{"title": f.title, "status": f.status, "start_at": iso(f.start_at)} for f in fus],
        "facts_used": [f.id for f in facts],
        "statement": STATEMENT,
    }


def write_summary(session: Session, episode: DischargeEpisode) -> dict[str, Any]:
    s = build_summary(session, episode)
    episode.summary = s
    log_usage(session, episode.id, s["facts_used"], used_for="summary", ref_type="summary", ref_id=episode.id,
              note="96-hour summary")
    return s
