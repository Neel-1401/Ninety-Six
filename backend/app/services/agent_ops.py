"""Agent/execution service interface. SIGNATURES ARE A LEAD-OWNED CONTRACT; Agent B owns the bodies.

Agent A's routers do authentication + permission checks, then call these functions. Unless a
docstring says otherwise, functions do NOT commit: the caller (one request = one transaction)
commits, and `app.events.record_event` notices are published after that commit.
After committing a change that may make tasks runnable, routers call `execution.worker.kick()`.

Errors: raise `app.errors.ApiError` helpers (conflict / invalid_state / validation / not_found).
Permission checks are the caller's job; these functions assume the actor is allowed.
B may implement in other modules and re-export from here, but must keep these names/signatures.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import (
    CheckIn,
    DischargeEpisode,
    PlanVersion,
    ProposedAction,
    ReviewItem,
    RoutineAuthorisation,
    ScheduledTask,
    ToolExecution,
    User,
)


def run_extraction(session: Session, episode: DischargeEpisode, *, actor: User) -> None:
    """Extract facts from the episode's latest DischargeDocument into PatientFact rows (with
    source_ref/source_excerpt, and is_missing placeholders for absent fields), compute the demo
    risk category + reasons from episode.risk_inputs and extracted facts, set
    episode.extraction_method / extraction_notes, record events."""
    raise NotImplementedError


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
    trigger='tool_finding'. Discards older drafts. Returns the newest draft."""
    raise NotImplementedError


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
    raise NotImplementedError


def approve_action(session: Session, action_id: int, *, payload_hash: str, doctor: User) -> ProposedAction:
    raise NotImplementedError


def edit_and_approve_action(
    session: Session, action_id: int, *, base_payload_hash: str, payload: dict[str, Any], doctor: User
) -> ProposedAction:
    raise NotImplementedError


def reject_action(session: Session, action_id: int, *, base_payload_hash: str, reason: str, doctor: User) -> ProposedAction:
    raise NotImplementedError


def retry_action(session: Session, action_id: int, *, doctor: User) -> ProposedAction:
    raise NotImplementedError


def reconcile_execution(session: Session, execution_id: int, *, outcome: str, note: str, actor: User) -> ToolExecution:
    raise NotImplementedError


def revoke_routine(session: Session, routine_id: int, *, reason: str, doctor: User) -> RoutineAuthorisation:
    raise NotImplementedError


def request_replan(session: Session, episode: DischargeEpisode, *, reason: str, actor: User) -> PlanVersion:
    """Explicit manual replan of an ACTIVE episode (new active version; completed work kept)."""
    raise NotImplementedError


def on_episode_context_changed(
    session: Session, episode: DischargeEpisode, *, changed_fields: list[str], actor: User
) -> None:
    """Called after contact details or facts change. For draft/awaiting_review: discard draft
    plan (episode back to draft is the caller's job). For ACTIVE: re-render recipients/payloads,
    invalidate affected approvals, replan with trigger 'staff_correction' if needed."""
    raise NotImplementedError


def enqueue_checkin_processing(session: Session, checkin: CheckIn) -> ScheduledTask:
    """Called by the submit route AFTER the answers are persisted in the same transaction."""
    raise NotImplementedError


def on_review_item_resolved(session: Session, item: ReviewItem, *, actor: User) -> None:
    """Unblocks steps gated on a staff_review item (e.g. medication combination review)."""
    raise NotImplementedError


def close_episode(session: Session, episode: DischargeEpisode, *, note: str, doctor: User) -> None:
    raise NotImplementedError


def preset_checkin_answers(checkin: CheckIn, preset: str) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Demo helper: answers for preset all_good|concern|red_flag from the check-in's questions."""
    raise NotImplementedError


def advance_clock(session: Session, hours: float) -> dict[str, Any]:
    """DEMO ONLY (caller checks). Advances the shared clock, commits, then runs the worker until
    idle (approval gates + freshness rules apply). Returns
    {sim_now, ran, skipped_stale, awaiting_approval, notes}. COMMITS internally."""
    raise NotImplementedError


def seed_background_episodes(session: Session) -> None:
    """DEMO ONLY. After accounts are seeded, create a couple of fictional background episodes
    through the normal pipeline so hospital queues are not empty. Does not create the primary
    heart-failure scenario (that is created live during the demo)."""
    raise NotImplementedError
