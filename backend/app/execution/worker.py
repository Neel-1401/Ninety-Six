"""Durable task worker (Agent B). One worker per process (the initial build runs it in-process).

Cycle (`run_once`):
  1. load the shared clock; recover running tasks whose lease expired;
  2. evaluate every due, non-terminal task with `preflight` (episode state, current plan version,
     dependencies, freshness window, approval / routine coverage) and persist status changes;
  3. for each READY task: atomic claim (UPDATE ... WHERE status='ready', rowcount must be 1),
     then the handler re-runs `preflight` inside its own transaction before doing anything.
Provider calls happen outside database transactions (handlers commit first).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import db
from ..clock import clock
from ..config import settings
from ..events import record_event
from ..models import (
    ApprovalState,
    CheckIn,
    DischargeEpisode,
    EpisodeStatus,
    ExecutionState,
    Message,
    MessageStatus,
    PlanStep,
    ProcessingState,
    ProposedAction,
    ReviewItem,
    ScheduledTask,
    StepStatus,
    TaskStatus,
    ToolExecution,
)
from ..policy import approval as policy

log = logging.getLogger("ninety_six.worker")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
_run_lock = threading.RLock()
_wake = threading.Event()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_last_tick: Optional[datetime] = None

OPEN = [TaskStatus.SCHEDULED, TaskStatus.AWAITING_APPROVAL, TaskStatus.READY]


def kick() -> None:
    _wake.set()


def last_tick_at() -> Optional[datetime]:
    return _last_tick


# ----------------------------------------------------------------------------- preflight
def _fmt(dt: datetime) -> str:
    from zoneinfo import ZoneInfo

    return dt.astimezone(ZoneInfo(settings.display_timezone)).strftime("%d %b %I:%M %p")


def preflight(s: Session, t: ScheduledTask, now: datetime) -> tuple[str, Optional[str], Optional[str]]:
    """Returns (task_status, reason, step_status_override). Pure check except routine
    re-evaluation, which may move an action back to PENDING when no longer covered."""
    ep = s.get(DischargeEpisode, t.episode_id)
    if t.kind in ("process_checkin", "close_checkin"):
        if ep.status in (EpisodeStatus.CANCELLED,):
            return TaskStatus.CANCELLED, "episode cancelled", None
        return TaskStatus.READY, None, None
    step = s.get(PlanStep, t.step_id) if t.step_id else None
    if step is None:
        return TaskStatus.CANCELLED, "task has no plan step", None
    if step.plan_version_id != ep.current_plan_version_id:
        return TaskStatus.SUPERSEDED, "step is no longer in the current plan version", None
    if step.status in (StepStatus.SUPERSEDED, StepStatus.CANCELLED):
        return (TaskStatus.SUPERSEDED if step.status == StepStatus.SUPERSEDED else TaskStatus.CANCELLED), step.result_summary, None
    if t.kind == "medication_review":
        if ep.status in (EpisodeStatus.DRAFT, EpisodeStatus.AWAITING_REVIEW, EpisodeStatus.ACTIVE):
            return TaskStatus.READY, None, None
        return TaskStatus.SKIPPED, "episode is not open", None
    after_window_urgent = False
    if ep.status != EpisodeStatus.ACTIVE:
        # A-11: urgent, individually-approvable actions stay live during the closure review
        if ep.status == EpisodeStatus.AWAITING_CLOSURE_REVIEW and t.kind == "execute_action":
            ua = s.get(ProposedAction, t.action_id) if t.action_id else None
            after_window_urgent = ua is not None and ua.priority == "urgent"
        if not after_window_urgent:
            if ep.status in (EpisodeStatus.AWAITING_CLOSURE_REVIEW, EpisodeStatus.CLOSED, EpisodeStatus.CANCELLED):
                return TaskStatus.SKIPPED, "monitoring window has ended", None
            return TaskStatus.SCHEDULED, "waiting for plan activation", None
    if t.due_at > now:
        return TaskStatus.SCHEDULED, None, None
    if t.expires_at is not None and now > t.expires_at:
        return TaskStatus.SKIPPED, f"skipped: missed its window (due {_fmt(t.due_at)}, stale after {_fmt(t.expires_at)})", None
    pv_steps = {x.step_key: x for x in s.scalars(select(PlanStep).where(PlanStep.plan_version_id == step.plan_version_id))}
    for d in step.depends_on or []:
        ds = pv_steps.get(d)
        if ds is None or ds.status != StepStatus.EXECUTED:
            title = ds.title if ds is not None else d
            return TaskStatus.SCHEDULED, f"blocked: waiting for '{title}'", StepStatus.BLOCKED
    if t.kind == "staff_review":
        if t.status == TaskStatus.AWAITING_APPROVAL:
            return TaskStatus.AWAITING_APPROVAL, t.status_reason, None
        return TaskStatus.READY, None, None
    if t.kind == "execute_action":
        a = s.get(ProposedAction, t.action_id)
        if a is None:
            return TaskStatus.CANCELLED, "action missing", None
        if a.approval_state == ApprovalState.REJECTED:
            return TaskStatus.CANCELLED, "rejected by doctor", None
        if a.approval_state == ApprovalState.INVALIDATED:
            return TaskStatus.SUPERSEDED, a.invalidated_reason or "approval invalidated", None
        if a.execution_state == ExecutionState.SUCCEEDED:
            return TaskStatus.CANCELLED, "action already executed (duplicate task)", None
        if a.execution_state not in (ExecutionState.NOT_STARTED, ExecutionState.RUNNING):
            return t.status, t.status_reason, None
        if a.approval_state not in (ApprovalState.APPROVED, ApprovalState.ROUTINE):
            # A-06 allow-list: anything other than a valid approval or routine coverage waits
            return TaskStatus.AWAITING_APPROVAL, "waiting for doctor approval", None
        from ..agent.plan_store import context_drift, reprepose_after_drift

        drift = context_drift(s, ep, step, a)
        if drift:  # A-05: recipient / live-vs-simulated mode changed since the approval
            reprepose_after_drift(s, ep, step, a, f"context changed: {drift}")
            return TaskStatus.SUPERSEDED, f"approval invalidated: {drift}", None
        if a.approval_state == ApprovalState.APPROVED:
            ok, why = policy.explicit_approval_valid(s, a)
            if not ok:
                return TaskStatus.AWAITING_APPROVAL, f"needs approval: {why}", None
        elif a.approval_state == ApprovalState.ROUTINE:
            if after_window_urgent:
                return TaskStatus.SKIPPED, "monitoring window has ended", None
            from ..agent.plan_store import render_action_fields
            from ..models import RoutineAuthorisation

            expected = render_action_fields(s, ep, step, prior=a)["payload"]
            ra = s.get(RoutineAuthorisation, a.routine_authorisation_id) if a.routine_authorisation_id else None
            ok, why = policy.routine_covers(s, a, ra, now, expected_payload=expected) if ra else (False, "no routine authorisation")
            if not ok:
                a.approval_state = ApprovalState.PENDING
                record_event(
                    s, "action.invalidated", f"No longer covered by routine authorisation: {a.step_key} ({why})",
                    episode_id=a.episode_id, actor_type="worker", actor_label="Executor pre-flight",
                    detail={"action_id": a.id, "reason": why}, scopes=["actions", "approvals", "plan", "overview"],
                )
                return TaskStatus.AWAITING_APPROVAL, f"needs approval: {why}", None
        else:
            return TaskStatus.AWAITING_APPROVAL, "waiting for doctor approval", None
        return TaskStatus.READY, None, None
    return TaskStatus.READY, None, None


def apply_status(s: Session, t: ScheduledTask, status: str, reason: Optional[str], override: Optional[str]) -> bool:
    from ..agent.plan_store import set_task_status

    changed = status != t.status or (reason or None) != (t.status_reason or None)
    st = s.get(PlanStep, t.step_id) if t.step_id else None
    step_changed = bool(override and st is not None and st.status != override)
    if not changed and not step_changed:
        return False
    set_task_status(s, t, status, reason)
    if override and st is not None:
        st.status = override
    changed = True
    if changed and status in (TaskStatus.SKIPPED, TaskStatus.SUPERSEDED, TaskStatus.CANCELLED):
        if t.action_id:
            a = s.get(ProposedAction, t.action_id)
            if a is not None and a.execution_state == ExecutionState.NOT_STARTED and status != TaskStatus.SUPERSEDED:
                a.execution_state = ExecutionState.CANCELLED
        record_event(
            s, f"task.{status}", f"Task {status}: {t.kind} ({reason or ''})"[:255],
            episode_id=t.episode_id, actor_type="worker", actor_label="Ninety-Six worker",
            detail={"task_id": t.id, "kind": t.kind, "reason": reason},
            scopes=["plan", "overview", "actions"],
        )
    return changed


# ----------------------------------------------------------------------------- recovery
def _recover_task(s: Session, t: ScheduledTask) -> None:
    from ..agent.plan_store import set_task_status

    if t.kind == "execute_action":
        ex = s.scalars(
            select(ToolExecution).where(ToolExecution.task_id == t.id, ToolExecution.outcome == "pending")
            .order_by(ToolExecution.id.desc())
        ).first()
        if ex is not None:
            ex.outcome = "unknown"
            ex.safe_error = "The worker stopped during the provider call; the outcome is unknown. Reconcile before retrying."
            ex.finished_at = clock.now()
            a = s.get(ProposedAction, t.action_id)
            if a is not None:
                a.execution_state = ExecutionState.UNKNOWN
                for m in s.scalars(select(Message).where(Message.action_id == a.id, Message.status == MessageStatus.QUEUED)):
                    m.status, m.status_updated_at = MessageStatus.UNKNOWN, clock.now()
            set_task_status(s, t, TaskStatus.UNKNOWN, "outcome unknown after restart; needs reconciliation")
            s.add(ReviewItem(
                episode_id=t.episode_id, kind="unknown_outcome", priority="high",
                title="Action outcome unknown after a restart", summary=ex.safe_error,
                detail={"task_id": t.id, "execution_id": ex.id, "action_id": t.action_id},
                source_type="execution", source_id=ex.id,
            ))
        else:
            set_task_status(s, t, TaskStatus.READY, "recovered after restart (provider was never called)")
    else:
        if t.kind == "process_checkin" and t.checkin_id:
            ci = s.get(CheckIn, t.checkin_id)
            if ci is not None and ci.processing_state == ProcessingState.PROCESSING:
                ci.processing_state = ProcessingState.RECEIVED
        set_task_status(s, t, TaskStatus.READY, "recovered after restart")
    t.claimed_by, t.lease_expires_at = None, None
    record_event(
        s, "task.recovered", f"Recovered interrupted task {t.kind} -> {t.status}",
        episode_id=t.episode_id, actor_type="worker", actor_label="Ninety-Six worker",
        detail={"task_id": t.id, "status": t.status}, scopes=["plan", "overview", "executions", "reviews"],
    )


def recover_on_startup() -> int:
    """Single-worker build: any RUNNING task not owned by this process was interrupted."""
    n = 0
    with db.session_scope() as s:
        for t in s.scalars(select(ScheduledTask).where(ScheduledTask.status == TaskStatus.RUNNING)):
            if t.claimed_by != WORKER_ID or (t.lease_expires_at and t.lease_expires_at < clock.real_now()):
                _recover_task(s, t)
                n += 1
    return n


def _recover_expired(s: Session) -> None:
    for t in s.scalars(
        select(ScheduledTask).where(
            ScheduledTask.status == TaskStatus.RUNNING, ScheduledTask.lease_expires_at < clock.real_now()
        )
    ):
        _recover_task(s, t)


# ----------------------------------------------------------------------------- loop
def claim(task_id: int) -> bool:
    with db.session_scope() as s:
        res = s.execute(
            update(ScheduledTask)
            .where(ScheduledTask.id == task_id, ScheduledTask.status == TaskStatus.READY)
            .values(
                status=TaskStatus.RUNNING, claimed_by=WORKER_ID, claimed_at=clock.now(),
                lease_expires_at=clock.real_now() + timedelta(seconds=settings.task_lease_seconds),
                attempts=ScheduledTask.attempts + 1,
            )
        )
        return res.rowcount == 1


def _run_once() -> dict[str, Any]:
    global _last_tick
    from .handlers import execute_claimed

    stats = {"ran": 0, "changed": 0, "skipped_stale": 0, "awaiting_approval": 0}
    ready: list[int] = []
    with db.session_scope() as s:
        # A-09: no clock.load() per tick (it could overwrite an advance that is not yet committed);
        # the in-process clock is authoritative and is loaded once at startup.
        now = clock.now()
        _recover_expired(s)
        rows = s.scalars(
            select(ScheduledTask)
            .where(ScheduledTask.status.in_(OPEN), ScheduledTask.due_at <= now)
            .order_by(ScheduledTask.due_at, ScheduledTask.id)
        ).all()
        real_now = clock.real_now()
        for t in rows:
            if t.next_attempt_at is not None and t.next_attempt_at > real_now:
                continue
            status, reason, override = preflight(s, t, now)
            if apply_status(s, t, status, reason, override):
                stats["changed"] += 1
                if status == TaskStatus.SKIPPED and reason and "missed its window" in reason:
                    stats["skipped_stale"] += 1
            if status == TaskStatus.READY:
                ready.append(t.id)
            s.flush()
        # Draft-stage medication reviews are not yet due-time bound
        for t in s.scalars(
            select(ScheduledTask).where(ScheduledTask.status == TaskStatus.SCHEDULED, ScheduledTask.kind == "medication_review")
        ):
            if t.id in ready:
                continue
            status, reason, override = preflight(s, t, now)
            if status == TaskStatus.READY and apply_status(s, t, status, reason, override):
                ready.append(t.id)
    for tid in ready:
        if claim(tid):
            try:
                execute_claimed(tid)
            except Exception:  # never kill the loop
                log.exception("Task %s crashed", tid)
            stats["ran"] += 1
    with db.session_scope() as s:
        stats["awaiting_approval"] = len(
            s.scalars(select(ScheduledTask.id).where(ScheduledTask.status == TaskStatus.AWAITING_APPROVAL)).all()
        )
    _last_tick = clock.real_now()
    return stats


def run_once() -> dict[str, Any]:
    with _run_lock:
        return _run_once()


def run_until_idle(max_iterations: int = 60) -> dict[str, Any]:
    total = {"ran": 0, "changed": 0, "skipped_stale": 0, "awaiting_approval": 0, "iterations": 0}
    with _run_lock:
        for _ in range(max_iterations):
            r = _run_once()
            total["iterations"] += 1
            for k in ("ran", "changed", "skipped_stale"):
                total[k] += r[k]
            total["awaiting_approval"] = r["awaiting_approval"]
            if r["ran"] == 0 and r["changed"] == 0:
                break
    return total


def _loop() -> None:
    while not _stop.is_set():
        try:
            run_once()
        except Exception:  # noqa: BLE001
            log.exception("worker tick failed")
        _wake.wait(timeout=settings.tick_seconds)
        _wake.clear()


def start_background_worker() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    try:
        with db.session_scope() as s:
            clock.load(s)
    except Exception:  # noqa: BLE001
        log.exception("clock load failed")
    try:
        recover_on_startup()
    except Exception:  # noqa: BLE001
        log.exception("startup recovery failed")
    _thread = threading.Thread(target=_loop, name="ninety-six-worker", daemon=True)
    _thread.start()


def stop_background_worker() -> None:
    global _thread
    _stop.set()
    _wake.set()
    if _thread is not None:
        _thread.join(timeout=10)
    _thread = None
