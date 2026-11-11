"""Patient routes (/api/me/**). Own episodes only (404 otherwise); never expose triage, risk,
agent rationale, audit events, staff notes or other audiences' messages."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import errors
from ..auth.deps import episode_for_patient, get_db, patient_episode_ids, require_patient
from ..auth.sessions import Actor
from ..clock import clock, iso
from ..config import settings
from ..events import record_event
from ..models import (
    CheckIn,
    CheckInResponse,
    CheckInStatus,
    DischargeEpisode,
    EpisodeStatus,
    FollowUpEvent,
    PlanStep,
    ProcessingState,
)
from ..schemas.requests import CheckInDraftIn, CheckInSubmitIn, ConcernIn
from ..schemas.serializers import (
    Names,
    current_plan,
    p_checkin_out,
    p_episode_out,
    p_followup_out,
    p_instructions_out,
    p_message_out,
    p_visible_messages_q,
)
from ..services import agent_ops
from .common import kick_worker

log = logging.getLogger("ninety_six.api.patient")
router = APIRouter(prefix="/api/me", tags=["patient"])

MONITORING_TEXT = (
    "Your answers are saved and processed automatically, and your care team can see them. "
    "This page is not watched around the clock, so it is not for emergencies. "
    "If you have chest pain, severe breathlessness, fainting or feel very unwell, call {emergency} now."
)


# --------------------------------------------------------------------------- submission (shared with demo)
def validate_answers(checkin: CheckIn, answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate against the reviewed questions snapshot. Returns [{question_id, value, label}]."""
    qmap = {q.get("id"): q for q in (checkin.questions or []) if isinstance(q, dict)}
    seen: dict[str, dict[str, Any]] = {}
    field_errors = []
    for a in answers:
        qid, value = a.get("question_id"), a.get("value")
        q = qmap.get(qid)
        if q is None:
            field_errors.append({"field": str(qid), "message": "Unknown question"})
            continue
        if qid in seen:
            field_errors.append({"field": qid, "message": "Answered twice"})
            continue
        qtype = q.get("type")
        if qtype in ("single_choice", "yes_no"):
            opts = {str(o.get("value")): o.get("label") for o in (q.get("options") or [])}
            if value is None or str(value) not in opts:
                if value in (None, "") and not q.get("required", True):
                    continue
                field_errors.append({"field": qid, "message": "Choose one of the options"})
                continue
            seen[qid] = {"question_id": qid, "value": str(value), "label": opts[str(value)]}
        else:  # text
            if value is None or str(value).strip() == "":
                if q.get("required", True):
                    field_errors.append({"field": qid, "message": "This question needs an answer"})
                continue
            text = str(value).strip()
            if len(text) > 2000:
                field_errors.append({"field": qid, "message": "Keep the answer under 2000 characters"})
                continue
            seen[qid] = {"question_id": qid, "value": text, "label": text}
    for qid, q in qmap.items():
        if q.get("required", True) and qid not in seen and not any(e["field"] == qid for e in field_errors):
            field_errors.append({"field": qid, "message": "This question needs an answer"})
    if field_errors:
        raise errors.validation("Some answers need attention.", {"fields": field_errors})
    order = list(qmap)
    return sorted(seen.values(), key=lambda r: order.index(r["question_id"]))


def submit_checkin(
    session: Session,
    checkin: CheckIn,
    *,
    submission_id: str,
    answers: list[dict[str, Any]],
    free_text: Optional[str],
    actor_type: str,
    actor_id: Optional[int],
    actor_label: str,
) -> tuple[CheckIn, bool]:
    """Persist answers, THEN enqueue agent processing, in ONE transaction. Returns (checkin, created).

    Duplicate-safe: same submission_id → returns the existing receipt (created=False); a different
    submission for an already-submitted check-in → 409 already_submitted.
    """
    existing = session.scalar(select(CheckIn).where(CheckIn.submission_id == submission_id))
    if existing is not None:
        if existing.id != checkin.id:
            raise errors.conflict("This submission id was already used.", code="already_submitted")
        return existing, False
    if checkin.status == CheckInStatus.SUBMITTED:
        raise errors.conflict("You've already sent this check-in. Thank you.", code="already_submitted")
    if checkin.status == CheckInStatus.UPCOMING:
        raise errors.invalid_state("This check-in isn't open yet.")
    if checkin.status in (CheckInStatus.MISSED, CheckInStatus.CANCELLED):
        raise errors.invalid_state("This check-in has closed. You can still send a message to your care team.")
    clean = validate_answers(checkin, answers)
    text = (free_text or "").strip() or None
    now = clock.now()
    # Atomic claim: only one submission can move the check-in out of DUE.
    res = session.execute(
        update(CheckIn)
        .where(CheckIn.id == checkin.id, CheckIn.status == CheckInStatus.DUE, CheckIn.submission_id.is_(None))
        .values(
            status=CheckInStatus.SUBMITTED,
            submission_id=submission_id,
            submitted_at=now,
            free_text=text,
            processing_state=ProcessingState.RECEIVED,
            draft={},
        )
    )
    if res.rowcount != 1:
        session.rollback()
        again = session.scalar(select(CheckIn).where(CheckIn.submission_id == submission_id))
        if again is not None:
            return again, False
        raise errors.conflict("You've already sent this check-in. Thank you.", code="already_submitted")
    session.refresh(checkin)
    for a in clean:
        session.add(CheckInResponse(checkin_id=checkin.id, question_id=a["question_id"],
                                    answer={"value": a["value"], "label": a["label"]}, created_at=now))
    session.flush()
    record_event(
        session,
        "checkin.submitted",
        f"Check-in received: {checkin.title}",
        episode_id=checkin.episode_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        detail={"checkin_id": checkin.id, "answers": len(clean), "has_free_text": bool(text)},
        scopes=["checkins", "today", "episode", "overview"],
        patient_visible=True,
    )
    try:
        agent_ops.enqueue_checkin_processing(session, checkin)
    except NotImplementedError:
        log.warning("enqueue_checkin_processing not implemented; answers saved, processing pending")
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        again = session.scalar(select(CheckIn).where(CheckIn.submission_id == submission_id))
        if again is not None:
            return again, False
        raise
    kick_worker()
    return checkin, True


def create_concern(session: Session, ep: DischargeEpisode, *, submission_id: str, text: str, actor_type: str,
                   actor_id: Optional[int], actor_label: str) -> tuple[CheckIn, bool]:
    existing = session.scalar(select(CheckIn).where(CheckIn.submission_id == submission_id))
    if existing is not None:
        if existing.episode_id != ep.id:
            raise errors.conflict("This submission id was already used.", code="already_submitted")
        return existing, False
    if ep.status not in (EpisodeStatus.ACTIVE, EpisodeStatus.AWAITING_CLOSURE_REVIEW):
        raise errors.invalid_state(
            f"Messages can't be sent here right now. Please call {settings.hospital_phone}, or {settings.emergency_number} in an emergency."
        )
    now = clock.now()
    c = CheckIn(
        episode_id=ep.id,
        kind="concern",
        title="Message to your care team",
        questions=[],
        due_at=now,
        status=CheckInStatus.SUBMITTED,
        submission_id=submission_id,
        submitted_at=now,
        free_text=text.strip(),
        processing_state=ProcessingState.RECEIVED,
    )
    session.add(c)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        again = session.scalar(select(CheckIn).where(CheckIn.submission_id == submission_id))
        if again is not None:
            return again, False
        raise
    record_event(
        session,
        "checkin.submitted",
        "Concern received from patient",
        episode_id=ep.id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        detail={"checkin_id": c.id, "kind": "concern"},
        scopes=["checkins", "today", "episode", "overview"],
        patient_visible=True,
    )
    try:
        agent_ops.enqueue_checkin_processing(session, c)
    except NotImplementedError:
        log.warning("enqueue_checkin_processing not implemented; concern saved")
    session.commit()
    kick_worker()
    return c, True


# --------------------------------------------------------------------------- routes
def _checkin_for_patient(session: Session, actor: Actor, checkin_id: int) -> CheckIn:
    c = session.get(CheckIn, checkin_id)
    if c is None:
        raise errors.not_found("Not found.")
    episode_for_patient(session, actor, c.episode_id)
    return c


@router.get("/episodes")
def my_episodes(session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    ids = patient_episode_ids(session, actor)
    names = Names(session)
    eps = session.scalars(
        select(DischargeEpisode)
        .where(DischargeEpisode.id.in_(ids), DischargeEpisode.status.notin_((EpisodeStatus.CANCELLED,)))
        .order_by(DischargeEpisode.discharge_at.desc())
    ).all() if ids else []
    return [p_episode_out(session, ep, names) for ep in eps]


@router.get("/episodes/{episode_id}/today")
def today(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    ep = episode_for_patient(session, actor, episode_id)
    names = Names(session)
    now = clock.now()
    episode = p_episode_out(session, ep, names)
    due = session.scalars(
        select(CheckIn)
        .where(CheckIn.episode_id == ep.id, CheckIn.kind == "scheduled", CheckIn.status == CheckInStatus.DUE)
        .order_by(CheckIn.due_at.desc())
    ).first()
    upcoming = None
    nxt = session.scalars(
        select(CheckIn)
        .where(CheckIn.episode_id == ep.id, CheckIn.status == CheckInStatus.UPCOMING)
        .order_by(CheckIn.due_at)
    ).first()
    if nxt is not None:
        upcoming = {"title": nxt.title, "due_at": iso(nxt.due_at)}
    else:
        pv = current_plan(session, ep)
        if pv is not None and ep.activated_at is not None:
            st = session.scalars(
                select(PlanStep)
                .where(PlanStep.plan_version_id == pv.id, PlanStep.action_type == "open_checkin",
                       PlanStep.status.in_(("planned", "blocked", "ready")), PlanStep.due_at > now)
                .order_by(PlanStep.due_at)
            ).first()
            if st is not None:
                upcoming = {"title": "Next check-in", "due_at": iso(st.due_at)}
    reminders = _reminders(session, ep.id)
    followup = session.scalars(
        select(FollowUpEvent).where(FollowUpEvent.episode_id == ep.id, FollowUpEvent.status != "cancelled").order_by(FollowUpEvent.start_at)
    ).first()
    if due is not None:
        next_action = {"kind": "checkin", "title": due.title, "description": "Answer a few short questions about how you are feeling.",
                       "checkin_id": due.id}
    elif ep.activated_at is None:
        next_action = {"kind": "none", "title": "Your care team is preparing your plan",
                       "description": "Your reviewed instructions will appear here once your doctor has checked them.",
                       "checkin_id": None}
    elif followup is not None and followup.status != "confirmed_by_hospital":
        next_action = {"kind": "followup", "title": "Check your follow-up details",
                       "description": "See what has been arranged and what still needs confirming.", "checkin_id": None}
    else:
        next_action = {"kind": "instructions", "title": "Review your medicines and warning signs",
                       "description": "Your doctor-reviewed discharge instructions.", "checkin_id": None}
    hours = max(0.0, episode["hours_since_discharge"])
    ended = now >= ep.window_ends_at or ep.monitoring_ended_at is not None
    return {
        "episode": episode,
        "next_action": next_action,
        "due_checkin": p_checkin_out(session, due) if due else None,
        "upcoming_checkin": upcoming,
        "reminders": reminders,
        "progress": {"hours_elapsed": round(min(hours, 96.0), 1), "window_hours": 96, "ended": ended},
        "monitoring_expectations": MONITORING_TEXT.format(emergency=settings.emergency_number),
        "contact": {"hospital_phone": settings.hospital_phone, "emergency_number": settings.emergency_number},
    }


def _reminders(session: Session, ep_id: int) -> list[dict[str, Any]]:
    rows = [m for m in session.scalars(p_visible_messages_q(ep_id)).all() if m.kind == "reminder"][:3]
    return [{"id": m.id, "body": m.body, "sent_at": iso(m.sent_at), "status": m.status} for m in rows]


@router.get("/episodes/{episode_id}/instructions")
def instructions(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    ep = episode_for_patient(session, actor, episode_id)
    return p_instructions_out(session, ep, Names(session))


@router.get("/episodes/{episode_id}/checkins")
def my_checkins(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    ep = episode_for_patient(session, actor, episode_id)
    rows = session.scalars(
        select(CheckIn)
        .where(CheckIn.episode_id == ep.id, CheckIn.status != CheckInStatus.UPCOMING)
        .order_by(CheckIn.due_at.desc(), CheckIn.id.desc())
    ).all()
    return [p_checkin_out(session, c) for c in rows]


@router.get("/checkins/{checkin_id}")
def my_checkin(checkin_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    c = _checkin_for_patient(session, actor, checkin_id)
    return p_checkin_out(session, c)


@router.put("/checkins/{checkin_id}/draft")
def save_draft(checkin_id: int, body: CheckInDraftIn, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    c = _checkin_for_patient(session, actor, checkin_id)
    if c.status != CheckInStatus.DUE:
        raise errors.invalid_state("This check-in is not open, so the draft can't be saved.")
    qids = {q.get("id") for q in (c.questions or []) if isinstance(q, dict)}
    answers = {k: v for k, v in body.answers.items() if k in qids and isinstance(v, (str, int, float, bool))}
    c.draft = {"answers": answers, "free_text": body.free_text}
    c.draft_updated_at = clock.now()
    session.commit()
    return p_checkin_out(session, c)


@router.post("/checkins/{checkin_id}/submit", status_code=202)
def submit(checkin_id: int, body: CheckInSubmitIn, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    c = _checkin_for_patient(session, actor, checkin_id)
    c, created = submit_checkin(
        session,
        c,
        submission_id=body.submission_id,
        answers=[a.model_dump() for a in body.answers],
        free_text=body.free_text,
        actor_type="patient",
        actor_id=actor.id,
        actor_label="Patient",
    )
    payload = p_checkin_out(session, c)
    return payload if created else JSONResponse(payload, status_code=200)


@router.post("/episodes/{episode_id}/concerns", status_code=202)
def concern(episode_id: int, body: ConcernIn, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    ep = episode_for_patient(session, actor, episode_id)
    c, created = create_concern(session, ep, submission_id=body.submission_id, text=body.text, actor_type="patient",
                                actor_id=actor.id, actor_label="Patient")
    payload = p_checkin_out(session, c)
    return payload if created else JSONResponse(payload, status_code=200)


@router.get("/episodes/{episode_id}/messages")
def my_messages(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    ep = episode_for_patient(session, actor, episode_id)
    return [p_message_out(m) for m in session.scalars(p_visible_messages_q(ep.id)).all()]


@router.get("/episodes/{episode_id}/followups")
def my_followups(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_patient)):
    ep = episode_for_patient(session, actor, episode_id)
    rows = session.scalars(select(FollowUpEvent).where(FollowUpEvent.episode_id == ep.id).order_by(FollowUpEvent.id)).all()
    return [p_followup_out(f, session) for f in rows]
