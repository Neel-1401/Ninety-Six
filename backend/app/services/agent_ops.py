"""Agent/execution service interface. SIGNATURES ARE A LEAD-OWNED CONTRACT; Agent B owns the bodies.

Agent A's routers do authentication + permission checks, then call these functions. Unless a
docstring says otherwise, functions do NOT commit: the caller (one request = one transaction)
commits, and `app.events.record_event` notices are published after that commit.
After committing a change that may make tasks runnable, routers call `execution.worker.kick()`.

Errors: raise `app.errors.ApiError` helpers (conflict / invalid_state / validation / not_found).
Permission checks are the caller's job; these functions assume the actor is allowed.
B may implement in other modules and re-export from here, but must keep these names/signatures.

Implementation notes (Agent B):
- run_extraction / generate_draft_plan make their optional LLM call BEFORE writing anything; call
  them before making other changes in the request so no write lock is held during the call.
- Default routine scope the UI should offer at activation:
  {"template_ids": ["checkin_reminder_v1"], "recipient_roles": ["patient"],
   "offsets_hours": <offsets of checkin_reminder_v1 steps in the plan>, "max_per_day": 2}
  (`services.actions.routine_offsets_in_plan` returns those offsets).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..clock import clock, iso
from ..config import settings
from ..events import record_event
from ..models import (
    CheckIn,
    DischargeDocument,
    DischargeEpisode,
    EpisodeStatus,
    FactReviewState,
    FactSourceKind,
    Patient,
    PlanStatus,
    PlanStep,
    PlanVersion,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
    RoutineAuthorisation,
    ScheduledTask,
    StaffAssignment,
    StaffReviewState,
    TaskStatus,
    ToolExecution,
    User,
)
from . import actions as _actions

# re-exports with the contract signatures
approve_action = _actions.approve_action
edit_and_approve_action = _actions.edit_and_approve_action
reject_action = _actions.reject_action
retry_action = _actions.retry_action
reconcile_execution = _actions.reconcile_execution
revoke_routine = _actions.revoke_routine


def run_extraction(session: Session, episode: DischargeEpisode, *, actor: User) -> None:
    """Extract facts from the episode's latest DischargeDocument into PatientFact rows (with
    source_ref/source_excerpt, and is_missing placeholders for absent fields), compute the demo
    risk category + reasons from episode.risk_inputs and extracted facts, set
    episode.extraction_method / extraction_notes, record events."""
    from ..agent.extraction import run_extraction as _run

    _run(session, episode, actor=actor)


def generate_draft_plan(
    session: Session,
    episode: DischargeEpisode,
    *,
    actor: Optional[User],
    trigger: str = "initial",
    reason: Optional[str] = None,
) -> PlanVersion:
    """Create a new DRAFT plan version from current active facts (LLM if configured, validated;
    rule-based fallback otherwise, fallback recorded in plan.validation + an audit event). Runs the
    medication review inline; an interaction finding may produce a further draft version with
    trigger='tool_finding'. Discards older drafts. Returns the newest draft.

    (Agent B note: the medication review is queued as an internal task that the worker runs right
    after commit; an interaction finding then produces the next draft version, trigger
    'tool_finding'. Routers should `worker.kick()` after committing.)"""
    from ..agent.planner import generate_draft_plan as _gen

    if episode.status not in (EpisodeStatus.DRAFT, EpisodeStatus.AWAITING_REVIEW):
        raise errors.invalid_state("A draft plan can only be generated before activation.")
    return _gen(session, episode, actor=actor, trigger=trigger, reason=reason)


def activate_plan(
    session: Session,
    episode: DischargeEpisode,
    *,
    plan_version_id: int,
    confirm_fact_ids: list[int],
    routine: Optional[dict[str, Any]],
    doctor: User,
) -> PlanVersion:
    """Doctor review: plan_version_id must be the current draft (else conflict code
    'stale_version'); every active non-missing fact id must be in confirm_fact_ids (else
    validation error). Confirms facts, activates plan, grants RoutineAuthorisation (if routine),
    creates ProposedActions + ScheduledTasks, sets episode ACTIVE."""
    from ..agent.memory import CLINICAL_CATEGORIES, PATIENT_VISIBLE_CATEGORIES, active_facts
    from ..agent.plan_store import current_plan, materialize
    from ..policy.approval import ROUTINE_RECIPIENTS, ROUTINE_TEMPLATES

    if episode.status != EpisodeStatus.AWAITING_REVIEW:
        raise errors.invalid_state("Only an episode awaiting doctor review can be activated.",
                                   details={"status": episode.status})
    pv = current_plan(session, episode)
    if pv is None or pv.status != PlanStatus.DRAFT or pv.id != plan_version_id:
        raise errors.conflict("The plan changed since you opened it. Review the latest version.", code="stale_version",
                              details={"current_plan_version_id": pv.id if pv else None,
                                       "current_version": pv.version if pv else None})
    pending_med_review = session.scalars(select(ScheduledTask).where(
        ScheduledTask.episode_id == episode.id, ScheduledTask.kind == "medication_review",
        ScheduledTask.status.in_([TaskStatus.SCHEDULED, TaskStatus.READY, TaskStatus.RUNNING]))).first()
    facts = active_facts(session, episode.id)
    required = [f for f in facts if f.category in CLINICAL_CATEGORIES and not f.is_missing
                and f.source_kind in (FactSourceKind.EXTRACTED, FactSourceKind.STAFF_ENTERED, FactSourceKind.STAFF_CORRECTED)]
    confirm = set(int(x) for x in confirm_fact_ids or [])
    missing = [f.id for f in required if f.id not in confirm]
    if missing:
        raise errors.validation("Confirm every extracted fact before activating.", {"unconfirmed_fact_ids": missing})
    now = clock.now()
    for f in facts:
        if f.category in CLINICAL_CATEGORIES and f.source_kind != FactSourceKind.INFERRED:
            f.review_state = FactReviewState.CONFIRMED
            f.reviewed_by_id, f.reviewed_at = doctor.id, now
            f.patient_visible = f.category in PATIENT_VISIBLE_CATEGORIES
    ra = None
    if routine:
        tpls = list(routine.get("template_ids") or [])
        roles = list(routine.get("recipient_roles") or ["patient"])
        offsets = sorted(float(x) for x in routine.get("offsets_hours") or [])
        max_per_day = int(routine.get("max_per_day") or 2)
        problems = []
        if not tpls or not set(tpls) <= ROUTINE_TEMPLATES:
            problems.append({"field": "routine.template_ids", "message": f"allowed: {sorted(ROUTINE_TEMPLATES)}"})
        if not set(roles) <= ROUTINE_RECIPIENTS:
            problems.append({"field": "routine.recipient_roles", "message": "routine messages may only go to the patient"})
        planned = set(_actions.routine_offsets_in_plan(session, episode, tpls))
        if not offsets or not all(any(abs(o - p) < 0.01 for p in planned) for o in offsets):
            problems.append({"field": "routine.offsets_hours", "message": f"must be a subset of the planned offsets {sorted(planned)}"})
        if not 1 <= max_per_day <= 6:
            problems.append({"field": "routine.max_per_day", "message": "must be between 1 and 6"})
        if problems:
            raise errors.validation("The routine authorisation scope is invalid.", {"fields": problems})
        ra = RoutineAuthorisation(
            episode_id=episode.id, plan_version_id=pv.id, action_types=["send_message"], template_ids=tpls,
            recipient_roles=roles, offsets_hours=offsets, max_per_day=max_per_day, expires_at=episode.window_ends_at,
            granted_by_id=doctor.id,
            scope_summary=(f"Templates {', '.join(tpls)} to {', '.join(roles)} at "
                           f"{', '.join(f'T+{o:g} h' for o in offsets)}; max {max_per_day}/day; "
                           f"expires at the end of the 96-hour window. Any other message needs individual approval."),
        )
        session.add(ra)
        session.flush()
    pv.status = PlanStatus.ACTIVE
    pv.activated_at, pv.activated_by_id = now, doctor.id
    episode.status = EpisodeStatus.ACTIVE
    episode.activated_at, episode.activated_by_id = now, doctor.id
    session.flush()
    counts = materialize(session, episode, pv)
    record_event(session, "plan.activated", f"{doctor.display_name} reviewed the extraction and activated plan v{pv.version}",
                 episode_id=episode.id, actor_type="user", actor_id=doctor.id, actor_label=doctor.display_name,
                 detail={"plan_version_id": pv.id, "confirmed_fact_ids": sorted(confirm), "counts": counts,
                         "medication_review_pending": pending_med_review is not None},
                 scopes=["plan", "episode", "episodes", "actions", "approvals", "overview", "instructions", "today", "memory"],
                 patient_visible=True)
    if ra is not None:
        record_event(session, "routine.granted", f"{doctor.display_name} granted routine authorisation: {ra.scope_summary}",
                     episode_id=episode.id, actor_type="user", actor_id=doctor.id, actor_label=doctor.display_name,
                     detail={"routine_id": ra.id}, scopes=["routine", "episode", "approvals", "actions"])
    return pv


def request_replan(session: Session, episode: DischargeEpisode, *, reason: str, actor: User) -> PlanVersion:
    """Explicit manual replan of an ACTIVE episode (new active version; completed work kept)."""
    from ..agent.replanner import replan

    if episode.status in (EpisodeStatus.DRAFT, EpisodeStatus.AWAITING_REVIEW):
        return generate_draft_plan(session, episode, actor=actor, trigger="manual", reason=reason)
    if episode.status != EpisodeStatus.ACTIVE:
        raise errors.invalid_state("Only an active episode can be replanned.")
    return replan(session, episode, trigger="manual", reason=reason or "manual request", recompute_initial=True,
                  actor_type="user", actor_id=actor.id, actor_label=actor.display_name, created_by=f"user:{actor.id}",
                  rationale=f"Manual replan requested by {actor.display_name}: {reason}")


def on_episode_context_changed(
    session: Session, episode: DischargeEpisode, *, changed_fields: list[str], actor: User
) -> None:
    """Called after contact details or facts change. For draft/awaiting_review: discard draft
    plan (episode back to draft is the caller's job). For ACTIVE: re-render recipients/payloads,
    invalidate affected approvals, replan with trigger 'staff_correction' if needed."""
    from ..agent.plan_store import current_plan, set_task_status, steps_of
    from ..agent.planner import caregiver_ok
    from ..agent.replanner import replan

    pv = current_plan(session, episode)
    if pv is None:
        return
    if episode.status in (EpisodeStatus.DRAFT, EpisodeStatus.AWAITING_REVIEW):
        if pv.status == PlanStatus.DRAFT:
            pv.status = PlanStatus.DISCARDED
            for st in steps_of(session, pv):
                for t in session.scalars(select(ScheduledTask).where(ScheduledTask.step_id == st.id,
                                                                     ScheduledTask.status.notin_(list(TaskStatus.TERMINAL)))):
                    set_task_status(session, t, TaskStatus.SUPERSEDED, "draft discarded after a correction")
            episode.current_plan_version_id = None
            record_event(session, "plan.replanned", f"Draft plan v{pv.version} discarded after a correction ({', '.join(changed_fields)})",
                         episode_id=episode.id, actor_type="user", actor_id=actor.id, actor_label=actor.display_name,
                         detail={"plan_version_id": pv.id, "changed_fields": changed_fields}, scopes=["plan", "episode"])
        return
    if episode.status != EpisodeStatus.ACTIVE:
        return
    patient = session.get(Patient, episode.patient_id)
    fact_change = any(f.startswith("fact") or f in ("facts", "summary_text") for f in changed_fields)

    def drop_unauthorised_caregiver(specs):
        if caregiver_ok(patient):
            return specs
        done = {s.step_key for s in steps_of(session, pv) if s.status in ("executed", "failed", "unknown", "skipped", "cancelled")}
        return [s for s in specs if not (s.action_type == "send_message" and s.arguments.get("recipient_role") == "caregiver"
                                         and s.step_key not in done)]

    replan(session, episode, trigger="staff_correction", reason="Changed: " + ", ".join(changed_fields),
           modify=drop_unauthorised_caregiver, recompute_initial=fact_change,
           actor_type="user", actor_id=actor.id, actor_label=actor.display_name, created_by=f"user:{actor.id}",
           rationale=f"{actor.display_name} corrected {', '.join(changed_fields)}; affected pending actions re-proposed.")


def enqueue_checkin_processing(session: Session, checkin: CheckIn) -> ScheduledTask:
    """Called by the submit route AFTER the answers are persisted in the same transaction."""
    from ..models import ProcessingState

    key = f"ep{checkin.episode_id}-process-ci{checkin.id}"
    existing = session.scalars(select(ScheduledTask).where(ScheduledTask.idempotency_key == key)).first()
    if existing is not None:
        return existing
    if checkin.processing_state in (None, ProcessingState.NOT_STARTED):
        checkin.processing_state = ProcessingState.RECEIVED
    t = ScheduledTask(episode_id=checkin.episode_id, checkin_id=checkin.id, kind="process_checkin",
                      due_at=clock.now(), status=TaskStatus.SCHEDULED, idempotency_key=key, max_attempts=3)
    session.add(t)
    session.flush()
    return t


def on_review_item_resolved(session: Session, item: ReviewItem, *, actor: User) -> None:
    """Unblocks steps gated on a staff_review item (e.g. medication combination review)."""
    from ..agent.plan_store import set_task_status

    step_key = (item.detail or {}).get("step_key")
    if step_key:
        for t in session.scalars(select(ScheduledTask).where(ScheduledTask.episode_id == item.episode_id,
                                                             ScheduledTask.kind == "staff_review",
                                                             ScheduledTask.status.notin_(list(TaskStatus.TERMINAL)))):
            st = session.get(PlanStep, t.step_id) if t.step_id else None
            if st is not None and st.step_key == step_key:
                set_task_status(session, t, TaskStatus.SUCCEEDED, f"review completed by {actor.display_name}")
                st.executed_at = clock.now()
                st.result_summary = f"Reviewed by {actor.display_name}: {item.resolution_note or 'no note'}"
                t.claimed_by = None
        record_event(session, "review.resolved", f"{actor.display_name} completed review: {item.title}",
                     episode_id=item.episode_id, actor_type="user", actor_id=actor.id, actor_label=actor.display_name,
                     detail={"review_item_id": item.id, "step_key": step_key}, scopes=["plan", "reviews", "overview"])
    if item.source_type == "checkin" and item.source_id:
        ci = session.get(CheckIn, item.source_id)
        if ci is not None and ci.staff_review_state != StaffReviewState.REVIEWED:
            ci.staff_review_state = StaffReviewState.REVIEWED
            ci.reviewed_by_id, ci.reviewed_at = actor.id, clock.now()
            ci.review_note = item.resolution_note
            record_event(session, "checkin.reviewed", f"{actor.display_name} reviewed check-in #{ci.id}",
                         episode_id=ci.episode_id, actor_type="user", actor_id=actor.id, actor_label=actor.display_name,
                         detail={"checkin_id": ci.id}, scopes=["checkins", "today"], patient_visible=True)
    session.flush()


def close_episode(session: Session, episode: DischargeEpisode, *, note: str, doctor: User) -> None:
    if episode.status != EpisodeStatus.AWAITING_CLOSURE_REVIEW:
        raise errors.invalid_state("Only an episode awaiting closure review can be closed.")
    urgent = session.scalars(select(ReviewItem).where(ReviewItem.episode_id == episode.id, ReviewItem.priority == "urgent",
                                                      ReviewItem.status != ReviewStatus.RESOLVED)).all()
    if urgent:
        raise errors.conflict("Resolve the open urgent review items before closing.",
                              details={"open_urgent_review_item_ids": [i.id for i in urgent]})
    now = clock.now()
    episode.status = EpisodeStatus.CLOSED
    episode.closed_at, episode.closed_by_id, episode.closure_note = now, doctor.id, (note or "").strip()
    for i in session.scalars(select(ReviewItem).where(ReviewItem.episode_id == episode.id, ReviewItem.kind == "closure_review",
                                                      ReviewItem.status != ReviewStatus.RESOLVED)):
        i.status, i.resolved_at, i.resolved_by_id, i.resolution_note = ReviewStatus.RESOLVED, now, doctor.id, note
    record_event(session, "episode.closed",
                 f"{doctor.display_name} completed the closure review (record closed; not a medical clearance)",
                 episode_id=episode.id, actor_type="user", actor_id=doctor.id, actor_label=doctor.display_name,
                 detail={"note": note}, scopes=["episode", "episodes", "overview", "reviews", "summary", "today"],
                 patient_visible=True)


def preset_checkin_answers(checkin: CheckIn, preset: str) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Demo helper: answers for preset all_good|concern|red_flag from the check-in's questions."""
    from ..agent.checkin_templates import preset_answers

    try:
        return preset_answers(checkin.template_id, list(checkin.questions or []), preset)
    except ValueError as e:
        raise errors.validation(str(e))


def advance_clock(session: Session, hours: float) -> dict[str, Any]:
    """DEMO ONLY (caller checks). Advances the shared clock, commits, then runs the worker until
    idle (approval gates + freshness rules apply). Returns
    {sim_now, ran, skipped_stale, awaiting_approval, notes}. COMMITS internally."""
    from ..execution import worker

    if not settings.demo_mode:
        raise errors.demo_disabled()
    if hours not in (6, 24, 6.0, 24.0):
        raise errors.validation("You can advance by 6 or 24 hours.")
    clock.load(session)
    new_now = clock.advance(session, float(hours))
    record_event(session, "clock.advanced", f"Simulated clock advanced {hours:g} h", actor_type="system",
                 actor_label="Demo controls", detail={"hours": hours, "sim_now": iso(new_now)},
                 scopes=["clock", "overview", "episodes", "today", "plan", "checkins"], patient_visible=True)
    session.commit()
    r = worker.run_until_idle()
    notes = []
    if r["skipped_stale"]:
        notes.append(f"{r['skipped_stale']} stale task(s) skipped instead of being sent late.")
    if r["awaiting_approval"]:
        notes.append(f"{r['awaiting_approval']} task(s) are waiting for doctor approval and were not executed.")
    return {"sim_now": iso(clock.now()), "ran": r["ran"], "skipped_stale": r["skipped_stale"],
            "awaiting_approval": r["awaiting_approval"], "notes": notes}


def seed_background_episodes(session: Session) -> None:
    """DEMO ONLY. After accounts are seeded, create a couple of fictional background episodes
    through the normal pipeline so hospital queues are not empty. Does not create the primary
    heart-failure scenario (that is created live during the demo)."""
    from ..agent.memory import active_facts
    from ..models import Role
    from ..samples import get_sample

    users = {u.username: u for u in session.scalars(select(User).where(User.username.isnot(None)))}
    nurse = users.get("nurse.asha") or next((u for u in users.values() if u.role == Role.NURSE), None)
    if nurse is None:
        return

    def create(key: str, doctor_username: str) -> DischargeEpisode:
        sm = get_sample(key)
        doctor = users.get(doctor_username) or next(u for u in users.values() if u.role == Role.DOCTOR)
        p = Patient(**sm["patient"])
        session.add(p)
        session.flush()
        discharge = clock.now() - timedelta(hours=sm["discharge_hours_ago"])
        ep = DischargeEpisode(patient_id=p.id, status=EpisodeStatus.DRAFT, discharge_at=discharge,
                              window_ends_at=discharge + timedelta(hours=96), risk_inputs=dict(sm["risk_inputs"]),
                              sample_key=key, created_by_id=nurse.id)
        session.add(ep)
        session.flush()
        for role, u in (("doctor", doctor), ("nurse", nurse)):
            session.add(StaffAssignment(episode_id=ep.id, user_id=u.id, role=role, assigned_by_id=nurse.id))
        session.add(DischargeDocument(episode_id=ep.id, text=sm["summary_text"], created_by_id=nurse.id))
        session.flush()
        record_event(session, "episode.created", f"Draft episode created for {p.full_name} (demo seed)", episode_id=ep.id,
                     actor_type="system", actor_label="Demo seed", scopes=["episodes", "overview"])
        run_extraction(session, ep, actor=nurse)
        return ep

    if "appendectomy" not in {e.sample_key for e in session.scalars(select(DischargeEpisode))}:
        ep = create("appendectomy", "dr.iyer")
        ep.status = EpisodeStatus.AWAITING_REVIEW
        ep.submitted_at = clock.now()
        pv = generate_draft_plan(session, ep, actor=nurse)
        doctor = session.get(User, next(a.user_id for a in session.scalars(
            select(StaffAssignment).where(StaffAssignment.episode_id == ep.id, StaffAssignment.role == "doctor"))))
        facts = active_facts(session, ep.id)
        offsets = _actions.routine_offsets_in_plan(session, ep, ["checkin_reminder_v1"])
        activate_plan(session, ep, plan_version_id=pv.id, confirm_fact_ids=[f.id for f in facts], doctor=doctor,
                      routine={"template_ids": ["checkin_reminder_v1"], "recipient_roles": ["patient"],
                               "offsets_hours": offsets, "max_per_day": 2})
    if "diabetes" not in {e.sample_key for e in session.scalars(select(DischargeEpisode))}:
        create("diabetes", "dr.mehta")
    session.flush()


__all__ = [
    "run_extraction", "generate_draft_plan", "activate_plan", "approve_action", "edit_and_approve_action",
    "reject_action", "retry_action", "reconcile_execution", "revoke_routine", "request_replan",
    "on_episode_context_changed", "enqueue_checkin_processing", "on_review_item_resolved", "close_episode",
    "preset_checkin_answers", "advance_clock", "seed_background_episodes", "ProposedAction", "ToolExecution",
]
