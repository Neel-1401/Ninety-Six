"""Deterministic approval policy (Agent B). The LLM never decides approval requirements.

See CONTRACTS.md §6. Everything here is pure or read-only so it can be re-evaluated immediately
before execution.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import iso
from ..models import (
    ApprovalDecision,
    ApprovalRequirement,
    ExecutionState,
    Patient,
    ProposedAction,
    RoutineAuthorisation,
    RoutineStatus,
    DischargeEpisode,
    PlanStep,
)

EXTERNAL_ACTIONS = {"send_message", "create_calendar_event", "care_team_alert"}
INTERNAL_ACTIONS = {"open_checkin", "medication_review", "staff_review", "generate_summary"}
ALLOWED_ACTIONS = EXTERNAL_ACTIONS | INTERNAL_ACTIONS
ROUTINE_TEMPLATES = {"checkin_reminder_v1", "closing_v1"}
ROUTINE_RECIPIENTS = {"patient"}
EDITABLE_FIELDS = {
    "send_message": ["body"],
    "care_team_alert": ["subject", "body"],
    "create_calendar_event": ["title", "start_at", "location", "notes"],
}


def requirement(action_type: str, *, recipient_role: Optional[str] = None, template_id: Optional[str] = None) -> str:
    if action_type in INTERNAL_ACTIONS:
        return ApprovalRequirement.NONE
    if action_type == "send_message" and recipient_role == "patient" and template_id in ROUTINE_TEMPLATES:
        return ApprovalRequirement.ROUTINE_ELIGIBLE
    return ApprovalRequirement.EXPLICIT


def editable_fields(action_type: str) -> list[str]:
    return list(EDITABLE_FIELDS.get(action_type, []))


def compute_hash(
    *,
    action_type: str,
    recipient_role: str,
    effective_recipient: str,
    channel: str,
    template_id: Optional[str],
    payload: dict[str, Any],
    step_key: str,
    due_at: datetime,
) -> str:
    canonical = json.dumps(
        {
            "action_type": action_type,
            "recipient_role": recipient_role,
            "effective_recipient": effective_recipient,
            "channel": channel,
            "template_id": template_id,
            "payload": payload,
            "step_key": step_key,
            "due_at": iso(due_at),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def action_hash(a: ProposedAction) -> str:
    return compute_hash(
        action_type=a.action_type,
        recipient_role=a.recipient_role,
        effective_recipient=a.effective_recipient,
        channel=a.channel,
        template_id=a.template_id,
        payload=dict(a.payload or {}),
        step_key=a.step_key,
        due_at=a.due_at,
    )


def latest_valid_decision(session: Session, action_id: int) -> Optional[ApprovalDecision]:
    return session.scalars(
        select(ApprovalDecision)
        .where(ApprovalDecision.action_id == action_id, ApprovalDecision.valid.is_(True))
        .order_by(ApprovalDecision.id.desc())
    ).first()


def explicit_approval_valid(session: Session, a: ProposedAction) -> tuple[bool, str]:
    d = latest_valid_decision(session, a.id)
    if d is None or d.decision not in ("approved", "edited_approved"):
        return False, "no valid approval"
    current = action_hash(a)
    if d.payload_hash != a.payload_hash or current != a.payload_hash:
        return False, "approval is bound to a different payload"
    return True, "approved"


def _offset_hours(session: Session, a: ProposedAction) -> Optional[float]:
    if a.step_id:
        st = session.get(PlanStep, a.step_id)
        if st is not None:
            return float(st.offset_hours)
    ep = session.get(DischargeEpisode, a.episode_id)
    if ep is None:
        return None
    return (a.due_at - ep.discharge_at).total_seconds() / 3600


def routine_covers(
    session: Session, a: ProposedAction, ra: RoutineAuthorisation, now: datetime, *, expected_payload: Optional[dict] = None
) -> tuple[bool, str]:
    """Check every scope dimension of a routine authorisation for this exact action."""
    if ra.status != RoutineStatus.ACTIVE:
        return False, f"routine authorisation is {ra.status}"
    if now >= ra.expires_at:
        return False, "routine authorisation expired"
    if ra.episode_id != a.episode_id:
        return False, "different episode"
    if a.action_type not in (ra.action_types or []):
        return False, f"{a.action_type} not in authorised action types"
    if a.template_id not in (ra.template_ids or []):
        return False, f"template {a.template_id} not authorised"
    if a.recipient_role not in (ra.recipient_roles or []):
        return False, f"recipient {a.recipient_role} not authorised"
    off = _offset_hours(session, a)
    if off is None or not any(abs(off - float(x)) < 0.01 for x in (ra.offsets_hours or [])):
        return False, f"T+{off:g}h is outside the authorised schedule" if off is not None else "unknown schedule"
    if expected_payload is not None and dict(a.payload or {}) != dict(expected_payload):
        return False, "content differs from the authorised template"
    ep = session.get(DischargeEpisode, a.episode_id)
    patient = session.get(Patient, ep.patient_id) if ep else None
    if a.recipient_role == "patient" and patient is not None and a.intended_recipient != patient.phone_e164:
        return False, "recipient is not the patient's current registered number"
    window_start = now - timedelta(hours=24)
    sent = session.scalar(
        select(func.count(ProposedAction.id)).where(
            ProposedAction.routine_authorisation_id == ra.id,
            ProposedAction.execution_state.in_([ExecutionState.SUCCEEDED, ExecutionState.UNKNOWN, ExecutionState.RUNNING]),
            ProposedAction.updated_at >= window_start,
            ProposedAction.id != a.id,
        )
    ) or 0
    if sent >= (ra.max_per_day or 0):
        return False, f"daily limit of {ra.max_per_day} routine messages reached"
    return True, f"covered by routine authorisation #{ra.id}"


def find_covering_routine(
    session: Session, a: ProposedAction, now: datetime, *, expected_payload: Optional[dict] = None
) -> tuple[Optional[RoutineAuthorisation], str]:
    ras = session.scalars(
        select(RoutineAuthorisation).where(
            RoutineAuthorisation.episode_id == a.episode_id, RoutineAuthorisation.status == RoutineStatus.ACTIVE
        )
    ).all()
    reason = "no active routine authorisation"
    for ra in ras:
        ok, reason = routine_covers(session, a, ra, now, expected_payload=expected_payload)
        if ok:
            return ra, reason
    return None, reason
