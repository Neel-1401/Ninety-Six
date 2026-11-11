"""Plan versions, steps, proposed actions and scheduled tasks (Agent B).

`apply_new_version` is the single place where a plan changes:
  - completed work (executed/failed/unknown/skipped/cancelled steps) is copied forward unchanged;
  - unchanged pending steps are carried over, re-pointing their task/action (approval kept only
    if the exact payload hash is unchanged);
  - changed steps supersede the old task and invalidate the old action, then get new ones;
  - removed pending steps are superseded; already-created calendar events are never cancelled.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import clock, iso
from ..config import settings
from ..events import record_event
from ..models import (
    ApprovalDecision,
    ApprovalRequirement,
    ApprovalState,
    DischargeEpisode,
    EpisodeStatus,
    ExecutionState,
    FollowUpEvent,
    FollowUpStatus,
    Patient,
    PlanStatus,
    PlanStep,
    PlanVersion,
    ProposedAction,
    ScheduledTask,
    StepStatus,
    TaskStatus,
)
from ..policy import approval as policy
from ..tools.calendar import propose_slot
from ..tools.registry import get_adapter, get_messaging
from . import message_templates as mt
from .plan_validation import StepSpec

TERMINAL_STEP = {StepStatus.EXECUTED, StepStatus.FAILED, StepStatus.UNKNOWN, StepStatus.SKIPPED,
                 StepStatus.CANCELLED, StepStatus.SUPERSEDED}
TASK_TO_STEP = {
    TaskStatus.SCHEDULED: StepStatus.PLANNED,
    TaskStatus.AWAITING_APPROVAL: StepStatus.AWAITING_APPROVAL,
    TaskStatus.READY: StepStatus.READY,
    TaskStatus.RUNNING: StepStatus.RUNNING,
    TaskStatus.SUCCEEDED: StepStatus.EXECUTED,
    TaskStatus.FAILED: StepStatus.FAILED,
    TaskStatus.UNKNOWN: StepStatus.UNKNOWN,
    TaskStatus.CANCELLED: StepStatus.CANCELLED,
    TaskStatus.SUPERSEDED: StepStatus.SUPERSEDED,
    TaskStatus.SKIPPED: StepStatus.SKIPPED,
}
EXTERNAL = policy.EXTERNAL_ACTIONS
TASK_KIND = {
    "send_message": "execute_action",
    "create_calendar_event": "execute_action",
    "care_team_alert": "execute_action",
    "open_checkin": "open_checkin",
    "medication_review": "medication_review",
    "staff_review": "staff_review",
    "generate_summary": "generate_summary",
}


def slug(text: str) -> str:
    words = [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w and w not in {"for", "and", "the", "of", "a"}]
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > 28:
            break
        out = f"{out}_{w}" if out else w
    return out or "item"


def fmt_h(h: float) -> str:
    return f"T+{h:g} h"


# ----------------------------------------------------------------------------- lookups
def current_plan(session: Session, episode: DischargeEpisode) -> Optional[PlanVersion]:
    if episode.current_plan_version_id is None:
        return None
    return session.get(PlanVersion, episode.current_plan_version_id)


def steps_of(session: Session, pv: PlanVersion) -> list[PlanStep]:
    return list(session.scalars(select(PlanStep).where(PlanStep.plan_version_id == pv.id).order_by(PlanStep.seq)))


def open_tasks_for_step(session: Session, step_id: int) -> list[ScheduledTask]:
    return list(
        session.scalars(
            select(ScheduledTask).where(
                ScheduledTask.step_id == step_id, ScheduledTask.status.notin_(list(TaskStatus.TERMINAL))
            )
        )
    )


def open_action_for_step(session: Session, step_id: int) -> Optional[ProposedAction]:
    return session.scalars(
        select(ProposedAction)
        .where(
            ProposedAction.step_id == step_id,
            ProposedAction.approval_state.notin_([ApprovalState.REJECTED, ApprovalState.INVALIDATED]),
        )
        .order_by(ProposedAction.id.desc())
    ).first()


def step_to_spec(st: PlanStep) -> StepSpec:
    return StepSpec(
        step_key=st.step_key, title=st.title, purpose=st.purpose, action_type=st.action_type,
        arguments=dict(st.arguments or {}), offset_hours=st.offset_hours, depends_on=list(st.depends_on or []),
        rationale=st.rationale, freshness_hours=st.freshness_hours, origin=st.origin,
    )


def same_spec(st: PlanStep, spec: StepSpec) -> bool:
    return (
        st.action_type == spec.action_type
        and dict(st.arguments or {}) == dict(spec.arguments)
        and abs(st.offset_hours - spec.offset_hours) < 1e-6
        and list(st.depends_on or []) == list(spec.depends_on)
        and st.freshness_hours == spec.freshness_hours
        and st.title == spec.title
    )


def describe_change(st: PlanStep, spec: StepSpec) -> str:
    parts = []
    if abs(st.offset_hours - spec.offset_hours) >= 1e-6:
        parts.append(f"timing {fmt_h(st.offset_hours)} -> {fmt_h(spec.offset_hours)}")
    added = [d for d in spec.depends_on if d not in (st.depends_on or [])]
    removed = [d for d in (st.depends_on or []) if d not in spec.depends_on]
    if added:
        parts.append("now waits for " + ", ".join(added))
    if removed:
        parts.append("no longer waits for " + ", ".join(removed))
    if dict(st.arguments or {}) != dict(spec.arguments):
        parts.append("arguments changed")
    if st.title != spec.title:
        parts.append(f"renamed to '{spec.title}'")
    return "; ".join(parts) or "updated"


# ----------------------------------------------------------------------------- status helpers
def set_task_status(session: Session, task: ScheduledTask, status: str, reason: Optional[str] = None) -> None:
    task.status = status
    if reason is not None:
        task.status_reason = reason
    if task.step_id:
        st = session.get(PlanStep, task.step_id)
        if st is not None and st.status not in (StepStatus.SUPERSEDED,) or (st is not None and status == TaskStatus.SUPERSEDED):
            st.status = TASK_TO_STEP.get(status, st.status)
            if reason is not None and status in (TaskStatus.SKIPPED, TaskStatus.AWAITING_APPROVAL, TaskStatus.SCHEDULED,
                                                 TaskStatus.CANCELLED, TaskStatus.SUPERSEDED):
                st.result_summary = reason


def invalidate_action(session: Session, action: ProposedAction, reason: str, *, actor_label: str = "Ninety-Six agent") -> None:
    if action.execution_state in (ExecutionState.SUCCEEDED, ExecutionState.RUNNING, ExecutionState.UNKNOWN):
        return  # history is never rewritten
    action.approval_state = ApprovalState.INVALIDATED
    action.invalidated_reason = reason
    if action.execution_state == ExecutionState.NOT_STARTED:
        action.execution_state = ExecutionState.CANCELLED
    for d in session.scalars(
        select(ApprovalDecision).where(ApprovalDecision.action_id == action.id, ApprovalDecision.valid.is_(True))
    ):
        d.valid = False
        d.invalidated_reason = reason
    for t in session.scalars(
        select(ScheduledTask).where(
            ScheduledTask.action_id == action.id, ScheduledTask.status.notin_(list(TaskStatus.TERMINAL))
        )
    ):
        set_task_status(session, t, TaskStatus.SUPERSEDED, f"superseded: {reason}")
    record_event(
        session, "action.invalidated", f"Approval invalidated: {action.step_key} ({reason})",
        episode_id=action.episode_id, actor_type="agent", actor_label=actor_label,
        detail={"action_id": action.id, "reason": reason},
        scopes=["actions", "approvals", "plan", "overview"],
    )


# ----------------------------------------------------------------------------- action rendering
def render_action_fields(
    session: Session, episode: DischargeEpisode, step: PlanStep, *, prior: Optional[ProposedAction] = None
) -> dict[str, Any]:
    """Exact recipient + payload for an external step (deterministic)."""
    p = session.get(Patient, episode.patient_id)
    args = dict(step.arguments or {})
    if step.action_type == "send_message":
        role = args["recipient_role"]
        intended = (p.caregiver_phone_e164 if role == "caregiver" else p.phone_e164) or ""
        messaging = get_messaging()
        effective = messaging.effective_recipient(intended)
        extra = {"checkin_title": args.get("checkin_title") or "check-in"}
        ctx = mt.build_context(session, episode, confirmed_only=True, extra=extra)
        payload = mt.render(args["template_id"], ctx)
        if role == "patient":  # AI draft/translation prepared earlier (network-free lookup; template if none)
            from .drafting import overlay

            payload = overlay(args["template_id"], payload, language=p.preferred_language,
                              checkin_id=args.get("checkin_id"))
        return {
            "recipient_role": role, "intended_recipient": intended, "effective_recipient": effective,
            "channel": args.get("channel") or settings.twilio_channel or "sms", "template_id": args["template_id"],
            "payload": payload, "expected_mode": messaging.mode_for(effective), "priority": args.get("priority", "normal"),
        }
    if step.action_type == "care_team_alert":
        email = get_adapter("care_team_email")
        extra = {"urgency": args.get("urgency", "urgent"), "reasons": args.get("reasons", []),
                 "answer_lines": args.get("answer_lines", []), "free_text": args.get("free_text")}
        if args.get("checkin_id"):
            from ..models import CheckIn, CheckInResponse

            ci = session.get(CheckIn, args["checkin_id"])
            if ci is not None:
                qtext = {q["id"]: q["text"] for q in (ci.questions or [])}
                resp = session.scalars(select(CheckInResponse).where(CheckInResponse.checkin_id == ci.id)).all()
                extra["answer_lines"] = [f"{qtext.get(r.question_id, r.question_id)} -> {r.answer.get('label', r.answer.get('value'))}"
                                         for r in resp]
                extra["free_text"] = ci.free_text
        ctx = mt.build_context(session, episode, confirmed_only=True, extra=extra)
        payload = mt.render("care_team_alert_v1", ctx)
        return {
            "recipient_role": "care_team", "intended_recipient": email.recipient(), "effective_recipient": email.recipient(),
            "channel": "email", "template_id": "care_team_alert_v1", "payload": payload,
            "expected_mode": email.mode(), "priority": args.get("urgency", "urgent"),
        }
    if step.action_type == "create_calendar_event":
        cal = get_adapter("calendar")
        if prior is not None and prior.action_type == "create_calendar_event" and \
                prior.payload.get("title") == args.get("title") and prior.payload.get("within_days") == args.get("within_days"):
            payload = dict(prior.payload)
        else:
            start, end = propose_slot(args.get("within_days"), now=clock.now())
            payload = {
                "title": args.get("title"),
                "provider_name": args.get("provider_name"),
                "location": args.get("location") or settings.hospital_name,
                "within_days": args.get("within_days"),
                "start_at": iso(start), "end_at": iso(end),
                "notes": "Proposed slot from the scheduling rule (next weekday 10:30 AM IST). Availability NOT checked.",
            }
        target = f"calendar:{settings.google_calendar_id}" if settings.calendar_configured else "calendar:simulated"
        return {
            "recipient_role": "calendar", "intended_recipient": target, "effective_recipient": target,
            "channel": "calendar", "template_id": None, "payload": payload, "expected_mode": cal.mode(),
            "priority": "normal",
        }
    raise ValueError(f"{step.action_type} is not an external action")


def _next_payload_version(session: Session, episode_id: int, step_key: str) -> int:
    cur = session.scalar(
        select(func.max(ProposedAction.payload_version)).where(
            ProposedAction.episode_id == episode_id, ProposedAction.step_key == step_key
        )
    )
    return int(cur or 0) + 1


def propose_action(
    session: Session, episode: DischargeEpisode, step: PlanStep, *, prior: Optional[ProposedAction] = None
) -> ProposedAction:
    f = render_action_fields(session, episode, step, prior=prior)
    req = policy.requirement(step.action_type, recipient_role=f["recipient_role"], template_id=f["template_id"])
    version = _next_payload_version(session, episode.id, step.step_key)
    supporting = list((step.arguments or {}).get("supporting_fact_ids") or [])
    a = ProposedAction(
        episode_id=episode.id, plan_version_id=step.plan_version_id, step_id=step.id, step_key=step.step_key,
        action_type=step.action_type, recipient_role=f["recipient_role"], intended_recipient=f["intended_recipient"],
        effective_recipient=f["effective_recipient"], channel=f["channel"], template_id=f["template_id"],
        payload=f["payload"], payload_hash="", payload_version=version, expected_mode=f["expected_mode"],
        priority=f["priority"], reason=step.rationale or step.purpose, supporting_fact_ids=supporting,
        approval_requirement=req, approval_state=ApprovalState.PENDING, due_at=step.due_at,
        idempotency_key=f"ep{episode.id}-{step.step_key}-v{version}",
    )
    a.payload_hash = policy.action_hash(a)
    session.add(a)
    session.flush()
    note = "needs doctor approval"
    if req == ApprovalRequirement.ROUTINE_ELIGIBLE:
        ra, why = policy.find_covering_routine(session, a, clock.now(), expected_payload=f["payload"])
        if ra is not None:
            a.approval_state = ApprovalState.ROUTINE
            a.routine_authorisation_id = ra.id
            note = why
        else:
            note = f"routine-eligible but not covered ({why}); needs doctor approval"
    if f["recipient_role"] == "calendar":
        _ensure_followup(session, episode, a, prior)
    record_event(
        session, "action.proposed",
        f"Proposed {step.title} -> {f['recipient_role']} ({note})",
        episode_id=episode.id, actor_type="agent", actor_label="Ninety-Six agent",
        detail={"action_id": a.id, "step_key": a.step_key, "approval_state": a.approval_state,
                "expected_mode": a.expected_mode, "priority": a.priority},
        scopes=["actions", "approvals", "plan", "overview", "followups"] if f["recipient_role"] == "calendar"
        else ["actions", "approvals", "plan", "overview"],
        patient_visible=f["recipient_role"] == "calendar",
    )
    return a


def _ensure_followup(session: Session, episode: DischargeEpisode, a: ProposedAction, prior: Optional[ProposedAction]) -> None:
    fu = None
    if prior is not None:
        fu = session.scalars(select(FollowUpEvent).where(FollowUpEvent.action_id == prior.id)).first()
    if fu is None:
        fu = session.scalars(
            select(FollowUpEvent)
            .join(ProposedAction, FollowUpEvent.action_id == ProposedAction.id)
            .where(FollowUpEvent.episode_id == episode.id, ProposedAction.step_key == a.step_key,
                   FollowUpEvent.status == FollowUpStatus.PROPOSED)
        ).first()
    p = a.payload
    from datetime import datetime

    start = datetime.fromisoformat(p["start_at"].replace("Z", "+00:00")) if p.get("start_at") else None
    end = datetime.fromisoformat(p["end_at"].replace("Z", "+00:00")) if p.get("end_at") else None
    if fu is None:
        st = session.get(PlanStep, a.step_id) if a.step_id else None
        fu = FollowUpEvent(episode_id=episode.id, source_fact_id=(st.arguments or {}).get("source_fact_id") if st else None,
                           title=p.get("title") or "Follow-up")
        session.add(fu)
    fu.action_id = a.id
    fu.title = p.get("title") or fu.title
    fu.provider_name = p.get("provider_name")
    fu.location = p.get("location")
    fu.start_at, fu.end_at = start, end
    fu.availability_checked = False
    if fu.status not in (FollowUpStatus.CALENDAR_EVENT_CREATED, FollowUpStatus.CONFIRMED_BY_HOSPITAL):
        fu.status = FollowUpStatus.PROPOSED
    session.flush()


# ----------------------------------------------------------------------------- tasks
def make_task(session: Session, episode: DischargeEpisode, step: PlanStep, action: Optional[ProposedAction]) -> ScheduledTask:
    kind = TASK_KIND[step.action_type]
    due = step.due_at
    expires = None
    if kind == "execute_action" and step.freshness_hours:
        expires = due + timedelta(hours=step.freshness_hours)
    if kind == "open_checkin":
        expires = min(due + timedelta(hours=float((step.arguments or {}).get("available_hours", 12))), episode.window_ends_at)
    t = ScheduledTask(
        episode_id=episode.id, step_id=step.id, action_id=action.id if action else None, kind=kind, due_at=due,
        expires_at=expires, status=TaskStatus.SCHEDULED,
        max_attempts=3, idempotency_key=f"ep{episode.id}-pv{step.plan_version_id}-{step.step_key}-{kind}",
    )
    session.add(t)
    session.flush()
    return t


def materialize(session: Session, episode: DischargeEpisode, pv: PlanVersion) -> dict[str, int]:
    """Ensure every pending step of `pv` has its task (and action, for external steps).
    Draft plans only get a medication_review task (internal, no side effects)."""
    counts = {"tasks": 0, "actions": 0}
    for st in steps_of(session, pv):
        if st.status in TERMINAL_STEP:
            continue
        if pv.status == PlanStatus.DRAFT and st.action_type != "medication_review":
            continue
        if open_tasks_for_step(session, st.id):
            continue
        action = None
        if st.action_type in EXTERNAL:
            action = open_action_for_step(session, st.id)
            if action is None or action.execution_state != ExecutionState.NOT_STARTED:
                action = propose_action(session, episode, st)
                counts["actions"] += 1
        make_task(session, episode, st, action)
        counts["tasks"] += 1
    return counts


# ----------------------------------------------------------------------------- versions
def _offset_now(episode: DischargeEpisode) -> float:
    return round(max(0.0, (clock.now() - episode.discharge_at).total_seconds() / 3600), 2)


def apply_new_version(
    session: Session,
    episode: DischargeEpisode,
    specs: list[StepSpec],
    *,
    status: str,
    source: str,
    trigger: str,
    trigger_ref: Optional[str] = None,
    rationale: str = "",
    validation: Optional[dict] = None,
    created_by: str = "agent",
    facts_used: Optional[list[int]] = None,
    base: Optional[PlanVersion] = None,
    actor_type: str = "agent",
    actor_id: Optional[int] = None,
    actor_label: str = "Ninety-Six agent",
) -> PlanVersion:
    n = session.scalar(select(func.max(PlanVersion.version)).where(PlanVersion.episode_id == episode.id)) or 0
    pv = PlanVersion(
        episode_id=episode.id, version=n + 1, status=status, source=source, trigger=trigger, trigger_ref=trigger_ref,
        rationale=rationale, validation=validation or {}, facts_used=list(facts_used or []),
        based_on_version_id=base.id if base else None, created_by=created_by,
    )
    if status == PlanStatus.ACTIVE:
        pv.activated_at = clock.now()
    session.add(pv)
    session.flush()

    base_steps = {s.step_key: s for s in steps_of(session, base)} if base else {}
    changes: list[dict[str, str]] = []
    new_steps: list[PlanStep] = []
    ordered = sorted(enumerate(specs), key=lambda x: (x[1].offset_hours, x[0]))
    for seq, (_, spec) in enumerate(ordered, 1):
        bs = base_steps.get(spec.step_key)
        st = PlanStep(
            plan_version_id=pv.id, episode_id=episode.id, step_key=spec.step_key, seq=seq, title=spec.title,
            purpose=spec.purpose, action_type=spec.action_type, arguments=dict(spec.arguments),
            offset_hours=spec.offset_hours, due_at=episode.discharge_at + timedelta(hours=spec.offset_hours),
            freshness_hours=spec.freshness_hours, depends_on=list(spec.depends_on),
            approval_requirement=policy.requirement(
                spec.action_type, recipient_role=spec.arguments.get("recipient_role"),
                template_id=spec.arguments.get("template_id"),
            ),
            rationale=spec.rationale, origin=spec.origin, status=StepStatus.PLANNED,
        )
        if bs is not None and bs.status in TERMINAL_STEP and bs.status != StepStatus.SUPERSEDED:
            # completed work is preserved exactly as it happened
            st.arguments, st.offset_hours, st.due_at = dict(bs.arguments or {}), bs.offset_hours, bs.due_at
            st.depends_on, st.title, st.purpose = list(bs.depends_on or []), bs.title, bs.purpose
            st.status, st.executed_at, st.result_summary = bs.status, bs.executed_at, bs.result_summary
            st.carried_from_step_id, st.origin, st.rationale = bs.id, bs.origin, bs.rationale
            session.add(st)
            session.flush()
            changes.append({"change": "kept", "step_key": spec.step_key, "description": f"'{bs.title}' already {bs.status}"})
        elif bs is not None and same_spec(bs, spec):
            st.status, st.result_summary, st.carried_from_step_id = bs.status, bs.result_summary, bs.id
            session.add(st)
            session.flush()
            for t in open_tasks_for_step(session, bs.id):
                t.step_id = st.id
            a = open_action_for_step(session, bs.id)
            if a is not None and a.execution_state == ExecutionState.NOT_STARTED:
                a.step_id, a.plan_version_id = st.id, pv.id
            changes.append({"change": "kept", "step_key": spec.step_key, "description": f"'{spec.title}' unchanged"})
        else:
            session.add(st)
            session.flush()
            if bs is not None:
                st.carried_from_step_id = bs.id
                desc = describe_change(bs, spec)
                for t in open_tasks_for_step(session, bs.id):
                    set_task_status(session, t, TaskStatus.SUPERSEDED, f"superseded by plan v{pv.version}: {desc}")
                a = open_action_for_step(session, bs.id)
                if a is not None:
                    invalidate_action(session, a, f"step changed in plan v{pv.version}: {desc}")
                changes.append({"change": "changed", "step_key": spec.step_key, "description": f"'{spec.title}': {desc}"})
            else:
                changes.append({"change": "added", "step_key": spec.step_key,
                                "description": f"'{spec.title}' at {fmt_h(spec.offset_hours)}" + (f": {spec.rationale}" if spec.rationale else "")})
        new_steps.append(st)

    new_keys = {s.step_key for s in specs}
    for key, bs in base_steps.items():
        if key in new_keys:
            continue
        if bs.status not in TERMINAL_STEP:
            for t in open_tasks_for_step(session, bs.id):
                set_task_status(session, t, TaskStatus.SUPERSEDED, f"removed in plan v{pv.version}")
            a = open_action_for_step(session, bs.id)
            if a is not None:
                invalidate_action(session, a, f"superseded by plan v{pv.version}")
            bs.status = StepStatus.SUPERSEDED
            changes.append({"change": "removed", "step_key": key, "description": f"'{bs.title}' removed"})

    if base is not None:
        base.status = PlanStatus.SUPERSEDED if base.status == PlanStatus.ACTIVE else PlanStatus.DISCARDED
        for bs in base_steps.values():
            if bs.status not in TERMINAL_STEP:
                bs.status = StepStatus.SUPERSEDED
    pv.change_summary = changes
    episode.current_plan_version_id = pv.id
    session.flush()

    if status == PlanStatus.ACTIVE:
        # content/recipient of carried actions may have changed (e.g. corrected facts, new phone)
        for st in new_steps:
            if st.action_type not in EXTERNAL or st.status in TERMINAL_STEP:
                continue
            a = open_action_for_step(session, st.id)
            if a is None or a.execution_state != ExecutionState.NOT_STARTED:
                continue
            f = render_action_fields(session, episode, st, prior=a)
            probe = ProposedAction(
                episode_id=a.episode_id, action_type=a.action_type, recipient_role=f["recipient_role"],
                intended_recipient=f["intended_recipient"], effective_recipient=f["effective_recipient"],
                expected_mode=f["expected_mode"], channel=f["channel"], template_id=f["template_id"],
                payload=f["payload"], step_key=a.step_key, due_at=st.due_at,
            )
            if policy.action_hash(probe) != a.payload_hash or f["intended_recipient"] != a.intended_recipient:
                invalidate_action(session, a, f"content or recipient changed in plan v{pv.version}")
                for t in open_tasks_for_step(session, st.id):
                    set_task_status(session, t, TaskStatus.SUPERSEDED, "superseded: content changed")
                new_a = propose_action(session, episode, st, prior=a)
                make_task(session, episode, st, new_a)
                for c in changes:
                    if c["step_key"] == st.step_key and c["change"] == "kept":
                        c["change"], c["description"] = "changed", f"'{st.title}': message content or recipient updated; approval needs renewing"
        pv.change_summary = list(changes)
        materialize(session, episode, pv)
    elif status == PlanStatus.DRAFT:
        materialize(session, episode, pv)

    record_event(
        session, "plan.created" if base is None else "plan.replanned",
        f"Plan v{pv.version} ({status}, {source}, trigger: {trigger}): "
        + ", ".join(f"{sum(1 for c in changes if c['change'] == k)} {k}" for k in ("added", "changed", "removed")),
        episode_id=episode.id, actor_type=actor_type, actor_id=actor_id, actor_label=actor_label,
        detail={"plan_version_id": pv.id, "version": pv.version, "trigger": trigger, "changes": changes,
                "source": source, "validation": validation or {}},
        scopes=["plan", "episode", "actions", "approvals", "overview", "memory", "today", "checkins"],
        patient_visible=False,
    )
    return pv


def offset_now(episode: DischargeEpisode) -> float:
    return _offset_now(episode)


def episode_is_monitoring(episode: DischargeEpisode) -> bool:
    return episode.status == EpisodeStatus.ACTIVE


def context_drift(session: Session, episode: DischargeEpisode, step: PlanStep, a: ProposedAction) -> Optional[str]:
    """Pre-flight re-check of recipient + mode (A-05). Returns a reason if the stored action no
    longer matches the current context (the approval must not be used), else None."""
    p = session.get(Patient, episode.patient_id)
    if a.recipient_role == "caregiver" and not (p.caregiver_authorised and p.caregiver_phone_e164):
        return "caregiver is no longer an authorised contact with a phone number"
    if a.action_type not in EXTERNAL:
        return None
    f = render_action_fields(session, episode, step, prior=a)
    for k in ("intended_recipient", "effective_recipient", "expected_mode", "recipient_role", "channel"):
        if (f[k] or "") != (getattr(a, k) or ""):
            return f"{k.replace('_', ' ')} changed since approval ({getattr(a, k)} -> {f[k]})"
    return None


def reprepose_after_drift(session: Session, episode: DischargeEpisode, step: PlanStep, a: ProposedAction, reason: str) -> None:
    invalidate_action(session, a, reason, actor_label="Executor pre-flight")
    p = session.get(Patient, episode.patient_id)
    if a.recipient_role == "caregiver" and not (p.caregiver_authorised and p.caregiver_phone_e164):
        step.status, step.result_summary = StepStatus.CANCELLED, f"cancelled: {reason}"
        return
    new_a = propose_action(session, episode, step, prior=a)
    make_task_retry_key = f"ep{episode.id}-pv{step.plan_version_id}-{step.step_key}-execute_action-v{new_a.payload_version}"
    t = ScheduledTask(episode_id=episode.id, step_id=step.id, action_id=new_a.id, kind="execute_action", due_at=step.due_at,
                      expires_at=(step.due_at + timedelta(hours=step.freshness_hours)) if step.freshness_hours else None,
                      status=TaskStatus.SCHEDULED, max_attempts=3, idempotency_key=make_task_retry_key)
    session.add(t)
    session.flush()
