"""Episode routes (staff). Every route: staff role → assignment (404) → capability (403)."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..auth import otp
from ..auth.deps import (
    assigned_episode_ids,
    ensure_doctor,
    ensure_nurse,
    episode_for_staff,
    get_db,
    require_nurse,
    require_staff,
)
from ..auth.security import normalize_phone
from ..auth.sessions import Actor
from ..clock import clock
from ..models import (
    AuditEvent,
    CheckIn,
    DischargeDocument,
    DischargeEpisode,
    EpisodeStatus,
    FactReviewState,
    FactSourceKind,
    FactUsage,
    FollowUpEvent,
    Message,
    Patient,
    PatientFact,
    PlanStatus,
    PlanVersion,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
    Role,
    StaffAssignment,
    StaffNote,
    ToolExecution,
    User,
)
from ..schemas.requests import (
    ActivateIn,
    EpisodeCreateIn,
    EpisodePatchIn,
    FactCreateIn,
    FactPatchIn,
    NoteIn,
    NoteOnlyIn,
    OptionalReasonIn,
    ReasonIn,
    TeamIn,
)
from ..schemas.serializers import (
    Names,
    action_out,
    audit_out,
    checkin_out,
    current_plan,
    episode_detail,
    episode_row,
    execution_out,
    fact_out,
    followup_out,
    message_out,
    note_out,
    plan_version_out,
    review_out,
    sort_actions,
    sort_reviews,
    usage_out,
)
from ..services import agent_ops
from .common import audit, kick_worker

router = APIRouter(prefix="/api/episodes", tags=["episodes"])

PRE_ACTIVATION = (EpisodeStatus.DRAFT, EpisodeStatus.AWAITING_REVIEW)
CONTACT_FIELDS = {"phone_e164", "caregiver_name", "caregiver_relation", "caregiver_phone_e164", "caregiver_authorised", "preferred_language"}
WINDOW = timedelta(hours=96)


def _staff_user(session: Session, user_id: int, role: str) -> User:
    u = session.get(User, user_id)
    if u is None or u.role != role or not u.is_active:
        raise errors.validation(f"Choose an active {role}.", {"fields": [{"field": f"{role}_id", "message": f"Not an active {role}"}]})
    return u


def _phone_or_422(raw: Optional[str], field: str, required: bool = True) -> Optional[str]:
    if raw in (None, ""):
        if required:
            raise errors.validation("Phone number is required.", {"fields": [{"field": field, "message": "Required"}]})
        return None
    p = normalize_phone(raw)
    if not p:
        raise errors.validation(
            "Enter the phone number with country code, e.g. +91 98765 43210.",
            {"fields": [{"field": field, "message": "Not a valid phone number"}]},
        )
    return p


def _assign(session: Session, ep: DischargeEpisode, user: User, role: str, by: Actor) -> None:
    for a in session.scalars(
        select(StaffAssignment).where(
            StaffAssignment.episode_id == ep.id, StaffAssignment.role == role, StaffAssignment.active.is_(True)
        )
    ).all():
        if a.user_id == user.id:
            return
        a.active = False
        a.ended_at = clock.now()
    session.add(StaffAssignment(episode_id=ep.id, user_id=user.id, role=role, active=True, assigned_by_id=by.id))
    session.flush()


def _call(fn, *args, **kwargs):  # noqa: ANN001, ANN202
    try:
        return fn(*args, **kwargs)
    except NotImplementedError:
        raise errors.ApiError(501, "not_implemented", f"{fn.__name__} is not available yet.")


def _bump(ep: DischargeEpisode) -> None:
    ep.row_version = (ep.row_version or 1) + 1
    ep.updated_at = clock.now()


# --------------------------------------------------------------------------- list / create / read
@router.get("")
def list_episodes(status: Optional[str] = None, scope: Optional[str] = "mine", session: Session = Depends(get_db),
                  actor: Actor = Depends(require_staff)):
    ids = assigned_episode_ids(session, actor)
    if not ids:
        return []
    q = select(DischargeEpisode).where(DischargeEpisode.id.in_(ids))
    if status:
        q = q.where(DischargeEpisode.status.in_(status.split(",")))
    names = Names(session)
    now = clock.now()
    eps = session.scalars(q.order_by(DischargeEpisode.updated_at.desc())).all()
    return [episode_row(session, ep, names, now) for ep in eps]


@router.post("", status_code=201)
def create_episode(body: EpisodeCreateIn, session: Session = Depends(get_db), actor: Actor = Depends(require_nurse)):
    now = clock.now()
    if body.discharge_at > now + timedelta(minutes=5):
        raise errors.validation("Discharge time can't be in the future.", {"fields": [{"field": "discharge_at", "message": "In the future"}]})
    if body.discharge_at < now - WINDOW:
        raise errors.validation(
            "This discharge was more than 96 hours ago, so the monitoring window has already ended.",
            {"fields": [{"field": "discharge_at", "message": "More than 96 hours ago"}]},
        )
    doctor = _staff_user(session, body.doctor_id, Role.DOCTOR)
    nurse = _staff_user(session, body.nurse_id, Role.NURSE) if body.nurse_id else actor.user
    pin = body.patient
    phone = _phone_or_422(pin.phone_e164, "patient.phone_e164")
    cg_phone = _phone_or_422(pin.caregiver_phone_e164, "patient.caregiver_phone_e164", required=False)
    patient = Patient(
        full_name=pin.full_name,
        age=pin.age,
        sex=pin.sex,
        phone_e164=phone,
        preferred_language=pin.preferred_language,
        caregiver_name=pin.caregiver_name or None,
        caregiver_relation=pin.caregiver_relation or None,
        caregiver_phone_e164=cg_phone,
        caregiver_authorised=bool(pin.caregiver_authorised and cg_phone),
        hospital_number=pin.hospital_number or None,
    )
    session.add(patient)
    session.flush()
    ep = DischargeEpisode(
        patient_id=patient.id,
        status=EpisodeStatus.DRAFT,
        discharge_at=body.discharge_at,
        window_ends_at=body.discharge_at + WINDOW,
        risk_inputs=body.risk_inputs.model_dump(exclude_none=True),
        sample_key=body.sample_key,
        created_by_id=actor.id,
    )
    session.add(ep)
    session.flush()
    _assign(session, ep, doctor, Role.DOCTOR, actor)
    # If another nurse is named, the creator hands the episode over and has no further access.
    _assign(session, ep, nurse, Role.NURSE, actor)
    session.add(DischargeDocument(episode_id=ep.id, kind="discharge_summary", text=body.summary_text, created_by_id=actor.id))
    session.flush()
    audit(session, actor, "episode.created", f"Draft discharge created for {patient.full_name}", episode_id=ep.id,
          scopes=["episodes", "episode", "overview"])
    try:
        agent_ops.run_extraction(session, ep, actor=actor.user)
    except NotImplementedError:
        ep.extraction_method = None
        ep.extraction_notes = ["Automatic extraction is not available yet; enter the details manually."]
    session.commit()
    return episode_detail(session, ep, actor)


@router.get("/{episode_id}")
def get_episode(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    return episode_detail(session, ep, actor)


@router.patch("/{episode_id}")
def patch_episode(episode_id: int, body: EpisodePatchIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    if body.row_version != ep.row_version:
        raise errors.conflict(
            "Someone else changed this discharge. Reload to see the latest version before saving.",
            {"current_row_version": ep.row_version},
            code="stale_version",
        )
    pre = ep.status in PRE_ACTIVATION
    if ep.status in (EpisodeStatus.CLOSED, EpisodeStatus.CANCELLED):
        raise errors.invalid_state("This episode is closed.")
    changed: list[str] = []
    patient = session.get(Patient, ep.patient_id)
    old_phone = patient.phone_e164
    if body.patient is not None:
        data = body.patient.model_dump(exclude_unset=True)
        if not pre and set(data) - CONTACT_FIELDS:
            raise errors.invalid_state("Only contact details can be changed after the plan is active.")
        for field, value in data.items():
            if field == "phone_e164":
                value = _phone_or_422(value, "patient.phone_e164")
            elif field == "caregiver_phone_e164":
                value = _phone_or_422(value, "patient.caregiver_phone_e164", required=False)
            if getattr(patient, field) != value:
                setattr(patient, field, value)
                changed.append(f"patient.{field}")
        if not patient.caregiver_phone_e164 and patient.caregiver_authorised:
            patient.caregiver_authorised = False
            changed.append("patient.caregiver_authorised")
    if body.discharge_at is not None:
        if not pre:
            raise errors.invalid_state("The discharge time can't be changed after the plan is active.")
        now = clock.now()
        if body.discharge_at > now + timedelta(minutes=5) or body.discharge_at < now - WINDOW:
            raise errors.validation("Discharge time must be within the last 96 hours.",
                                    {"fields": [{"field": "discharge_at", "message": "Out of range"}]})
        if body.discharge_at != ep.discharge_at:
            ep.discharge_at = body.discharge_at
            ep.window_ends_at = body.discharge_at + WINDOW
            changed.append("discharge_at")
    if body.risk_inputs is not None:
        if not pre:
            raise errors.invalid_state("Risk inputs can't be changed after the plan is active.")
        new = body.risk_inputs.model_dump(exclude_none=True)
        if new != (ep.risk_inputs or {}):
            ep.risk_inputs = new
            changed.append("risk_inputs")
    if not changed:
        return episode_detail(session, ep, actor)
    if "patient.phone_e164" in changed:
        otp.revoke_patient_access_for_phone_change(session, patient, old_phone=old_phone,
                                                   new_phone=patient.phone_e164, actor=actor)
    _bump(ep)
    if ep.status == EpisodeStatus.AWAITING_REVIEW:
        ep.status = EpisodeStatus.DRAFT
        changed.append("status:returned_to_draft")
    audit(session, actor, "episode.updated", "Discharge details updated", episode_id=ep.id,
          detail={"changed": changed}, scopes=["episode", "episodes", "plan", "actions", "overview"])
    try:
        agent_ops.on_episode_context_changed(session, ep, changed_fields=changed, actor=actor.user)
    except NotImplementedError:
        pass
    session.commit()
    kick_worker()
    return episode_detail(session, ep, actor)


@router.put("/{episode_id}/team")
def put_team(episode_id: int, body: TeamIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    ensure_nurse(actor, "assign the care team")
    if ep.status in (EpisodeStatus.CLOSED, EpisodeStatus.CANCELLED):
        raise errors.invalid_state("This episode is closed.")
    doctor = _staff_user(session, body.doctor_id, Role.DOCTOR)
    nurse = _staff_user(session, body.nurse_id, Role.NURSE)
    _assign(session, ep, doctor, Role.DOCTOR, actor)
    _assign(session, ep, nurse, Role.NURSE, actor)
    _bump(ep)
    audit(session, actor, "episode.updated", f"Care team set: {doctor.display_name}, {nurse.display_name}",
          episode_id=ep.id, detail={"doctor_id": doctor.id, "nurse_id": nurse.id},
          scopes=["episode", "episodes", "overview"], patient_visible=True)
    session.commit()
    return episode_detail(session, ep, actor)


# --------------------------------------------------------------------------- facts
def _facts_editable(ep: DischargeEpisode) -> None:
    if ep.status not in PRE_ACTIVATION:
        raise errors.invalid_state("Extracted information can only be corrected before the plan is activated.")


def _after_fact_change(session: Session, ep: DischargeEpisode, actor: Actor, changed: str) -> None:
    _bump(ep)
    if ep.status == EpisodeStatus.AWAITING_REVIEW:
        ep.status = EpisodeStatus.DRAFT
    try:
        agent_ops.on_episode_context_changed(session, ep, changed_fields=[changed], actor=actor.user)
    except NotImplementedError:
        pass


@router.post("/{episode_id}/facts", status_code=201)
def add_fact(episode_id: int, body: FactCreateIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    _facts_editable(ep)
    f = PatientFact(
        episode_id=ep.id,
        category=body.category,
        label=body.label,
        value=dict(body.value),
        source_kind=FactSourceKind.STAFF_ENTERED,
        source_ref=f"user:{actor.id}",
        review_state=FactReviewState.UNREVIEWED,
        created_by=f"user:{actor.id}",
    )
    session.add(f)
    session.flush()
    # Adding a value for a "not provided" field retires the placeholder of the same category/label.
    for old in session.scalars(
        select(PatientFact).where(
            PatientFact.episode_id == ep.id, PatientFact.active.is_(True), PatientFact.is_missing.is_(True),
            PatientFact.category == body.category, PatientFact.id != f.id,
        )
    ).all():
        old.active = False
    audit(session, actor, "fact.added", f"Added {body.category}: {body.label}", episode_id=ep.id,
          detail={"fact_id": f.id}, scopes=["episode", "memory"])
    _after_fact_change(session, ep, actor, "facts")
    session.commit()
    return fact_out(f, Names(session))


@router.patch("/{episode_id}/facts/{fact_id}")
def correct_fact(episode_id: int, fact_id: int, body: FactPatchIn, session: Session = Depends(get_db),
                 actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    _facts_editable(ep)
    old = session.get(PatientFact, fact_id)
    if old is None or old.episode_id != ep.id:
        raise errors.not_found("Fact not found.")
    if not old.active:
        raise errors.conflict("This item was already changed. Reload to see the current value.", code="stale_version")
    new = PatientFact(
        episode_id=ep.id,
        category=old.category,
        label=body.label or old.label,
        value=dict(body.value),
        source_kind=FactSourceKind.STAFF_CORRECTED,
        source_ref=old.source_ref,
        source_excerpt=old.source_excerpt,
        extraction_method=old.extraction_method,
        is_missing=False,
        review_state=FactReviewState.UNREVIEWED,
        supersedes_id=old.id,
        created_by=f"user:{actor.id}",
    )
    old.active = False
    session.add(new)
    session.flush()
    audit(session, actor, "fact.corrected", f"Corrected {old.category}: {new.label}", episode_id=ep.id,
          detail={"old_fact_id": old.id, "new_fact_id": new.id, "reason": body.reason}, scopes=["episode", "memory"])
    _after_fact_change(session, ep, actor, "facts")
    session.commit()
    return fact_out(new, Names(session))


@router.post("/{episode_id}/facts/{fact_id}/reject")
def reject_fact(episode_id: int, fact_id: int, body: ReasonIn, session: Session = Depends(get_db),
                actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    _facts_editable(ep)
    f = session.get(PatientFact, fact_id)
    if f is None or f.episode_id != ep.id:
        raise errors.not_found("Fact not found.")
    f.review_state = FactReviewState.REJECTED
    f.active = False
    f.reviewed_by_id = actor.id
    f.reviewed_at = clock.now()
    audit(session, actor, "fact.rejected", f"Removed {f.category}: {f.label}", episode_id=ep.id,
          detail={"fact_id": f.id, "reason": body.reason}, scopes=["episode", "memory"])
    _after_fact_change(session, ep, actor, "facts")
    session.commit()
    return fact_out(f, Names(session))


# --------------------------------------------------------------------------- plan lifecycle
@router.post("/{episode_id}/submit")
def submit(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    ensure_nurse(actor, "submit a discharge for doctor review")
    if ep.status != EpisodeStatus.DRAFT:
        raise errors.invalid_state("Only a draft can be submitted for review.")
    _call(agent_ops.generate_draft_plan, session, ep, actor=actor.user, trigger="initial")
    ep.status = EpisodeStatus.AWAITING_REVIEW
    ep.submitted_at = clock.now()
    _bump(ep)
    audit(session, actor, "episode.submitted", "Submitted for doctor review", episode_id=ep.id,
          scopes=["episode", "episodes", "plan", "overview", "approvals"])
    session.commit()
    return episode_detail(session, ep, actor)


@router.post("/{episode_id}/plan/regenerate")
def regenerate(episode_id: int, body: OptionalReasonIn, session: Session = Depends(get_db),
               actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    if ep.status not in PRE_ACTIVATION:
        raise errors.invalid_state("Use replan for an active episode.")
    pv = _call(agent_ops.generate_draft_plan, session, ep, actor=actor.user, trigger="manual", reason=body.reason)
    _bump(ep)
    session.commit()
    return plan_version_out(session, pv, Names(session))


@router.post("/{episode_id}/return-to-draft")
def return_to_draft(episode_id: int, body: ReasonIn, session: Session = Depends(get_db),
                    actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    ensure_doctor(actor, "return a discharge to draft")
    if ep.status != EpisodeStatus.AWAITING_REVIEW:
        raise errors.invalid_state("Only a discharge awaiting review can be returned.")
    ep.status = EpisodeStatus.DRAFT
    _bump(ep)
    audit(session, actor, "episode.returned", "Returned to the nurse for changes", episode_id=ep.id,
          detail={"reason": body.reason}, scopes=["episode", "episodes", "overview"])
    session.commit()
    return episode_detail(session, ep, actor)


@router.post("/{episode_id}/activate")
def activate(episode_id: int, body: ActivateIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    ensure_doctor(actor, "activate the plan")
    if ep.status != EpisodeStatus.AWAITING_REVIEW:
        raise errors.invalid_state("Only a discharge awaiting doctor review can be activated.")
    _call(
        agent_ops.activate_plan,
        session,
        ep,
        plan_version_id=body.plan_version_id,
        confirm_fact_ids=list(body.confirm_fact_ids),
        routine=body.routine.model_dump() if body.routine else None,
        doctor=actor.user,
    )
    _bump(ep)
    session.commit()
    kick_worker()
    return episode_detail(session, ep, actor)


@router.post("/{episode_id}/replan")
def replan(episode_id: int, body: ReasonIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    if ep.status != EpisodeStatus.ACTIVE:
        raise errors.invalid_state("Only an active episode can be replanned.")
    pv = _call(agent_ops.request_replan, session, ep, reason=body.reason, actor=actor.user)
    _bump(ep)
    session.commit()
    kick_worker()
    return plan_version_out(session, pv, Names(session))


@router.post("/{episode_id}/close")
def close(episode_id: int, body: NoteOnlyIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    ensure_doctor(actor, "close the episode")
    if ep.status != EpisodeStatus.AWAITING_CLOSURE_REVIEW:
        raise errors.invalid_state("The episode can be closed only after the 96-hour window ends and the summary is ready.")
    open_urgent = session.scalar(
        select(ReviewItem.id).where(
            ReviewItem.episode_id == ep.id,
            ReviewItem.priority == "urgent",
            ReviewItem.status.in_((ReviewStatus.OPEN, ReviewStatus.ACKNOWLEDGED)),
        )
    )
    if open_urgent is not None:
        raise errors.invalid_state("Resolve the open urgent review items before closing this episode.")
    _call(agent_ops.close_episode, session, ep, note=body.note, doctor=actor.user)
    _bump(ep)
    session.commit()
    return episode_detail(session, ep, actor)


@router.post("/{episode_id}/invite")
def invite(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    from ..schemas.serializers import message_out as _m
    from ..clock import iso

    ep = episode_for_staff(session, actor, episode_id)
    inv, link, msg = otp.create_invitation(session, ep, actor=actor)
    return {"invitation": {"status": inv.status, "expires_at": iso(inv.expires_at)}, "link": link, "message": _m(msg)}


# --------------------------------------------------------------------------- sub-resources (read)
@router.get("/{episode_id}/plans")
def plans(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    pvs = session.scalars(select(PlanVersion).where(PlanVersion.episode_id == ep.id).order_by(PlanVersion.version.desc())).all()
    return [plan_version_out(session, pv, names) for pv in pvs]


@router.get("/{episode_id}/plans/current")
def plan_current(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    pv = current_plan(session, ep)
    if pv is None:
        raise errors.not_found("No plan yet.")
    return plan_version_out(session, pv, Names(session))


@router.get("/{episode_id}/actions")
def actions(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    rows = session.scalars(select(ProposedAction).where(ProposedAction.episode_id == ep.id)).all()
    return [action_out(session, a, names) for a in sort_actions(rows)]


@router.get("/{episode_id}/checkins")
def checkins(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    rows = session.scalars(select(CheckIn).where(CheckIn.episode_id == ep.id).order_by(CheckIn.due_at.desc(), CheckIn.id.desc())).all()
    return [checkin_out(session, c, names) for c in rows]


@router.get("/{episode_id}/messages")
def messages(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    rows = session.scalars(select(Message).where(Message.episode_id == ep.id).order_by(Message.created_at.desc(), Message.id.desc())).all()
    return [message_out(m) for m in rows]


@router.get("/{episode_id}/followups")
def followups(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    rows = session.scalars(select(FollowUpEvent).where(FollowUpEvent.episode_id == ep.id).order_by(FollowUpEvent.id)).all()
    return [followup_out(f, names) for f in rows]


@router.get("/{episode_id}/memory")
def memory(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    facts = session.scalars(select(PatientFact).where(PatientFact.episode_id == ep.id).order_by(PatientFact.id)).all()
    usage = session.scalars(select(FactUsage).where(FactUsage.episode_id == ep.id).order_by(FactUsage.id.desc())).all()
    return {"facts": [fact_out(f, names) for f in facts], "usage": [usage_out(u) for u in usage]}


@router.get("/{episode_id}/activity")
def activity(episode_id: int, after_id: Optional[int] = None, session: Session = Depends(get_db),
             actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    q = select(AuditEvent).where(AuditEvent.episode_id == ep.id)
    if after_id is not None:
        rows = session.scalars(q.where(AuditEvent.id > after_id).order_by(AuditEvent.id).limit(500)).all()
    else:
        rows = session.scalars(q.order_by(AuditEvent.id.desc()).limit(300)).all()
    return [audit_out(e) for e in rows]


@router.get("/{episode_id}/executions")
def executions(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    rows = session.scalars(select(ToolExecution).where(ToolExecution.episode_id == ep.id).order_by(ToolExecution.id.desc())).all()
    return [execution_out(e, names) for e in rows]


@router.get("/{episode_id}/review-items")
def episode_reviews(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    rows = session.scalars(select(ReviewItem).where(ReviewItem.episode_id == ep.id)).all()
    return [review_out(session, r, names) for r in sort_reviews(rows)]


@router.get("/{episode_id}/notes")
def notes(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    names = Names(session)
    rows = session.scalars(select(StaffNote).where(StaffNote.episode_id == ep.id).order_by(StaffNote.id.desc())).all()
    return [note_out(n, names) for n in rows]


@router.post("/{episode_id}/notes", status_code=201)
def add_note(episode_id: int, body: NoteIn, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    if body.review_item_id is not None:
        ri = session.get(ReviewItem, body.review_item_id)
        if ri is None or ri.episode_id != ep.id:
            raise errors.validation("That review item is not part of this episode.")
    n = StaffNote(episode_id=ep.id, author_id=actor.id, body=body.body, review_item_id=body.review_item_id)
    session.add(n)
    session.flush()
    audit(session, actor, "note.added", "Coordination note added", episode_id=ep.id, detail={"note_id": n.id},
          scopes=["notes"])
    session.commit()
    return note_out(n, Names(session))


@router.get("/{episode_id}/summary")
def summary(episode_id: int, session: Session = Depends(get_db), actor: Actor = Depends(require_staff)):
    ep = episode_for_staff(session, actor, episode_id)
    if not ep.summary:
        raise errors.not_found("The 96-hour summary has not been generated yet.")
    return dict(ep.summary)
