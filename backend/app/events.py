"""Audit trail + live updates. LEAD-OWNED CONTRACT.

`record_event()` adds an AuditEvent to the CURRENT transaction. Only after that transaction
commits is a lightweight notice published to SSE subscribers (persist first, notify second).
If the transaction rolls back, nothing is published. SSE is a hint to refetch, never the
source of truth: the notice carries ids and query scopes, not clinical content.

SSE wire format (see docs/CONTRACTS.md §7):
    id: <audit_event.id>
    event: update
    data: {"id": 12, "kind": "action.approved", "episode_id": 3, "scopes": ["actions", ...], "at": "...Z"}
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from .clock import iso
from .models import AuditEvent

log = logging.getLogger("ninety_six.events")

# Query scopes the frontend understands (TanStack Query key prefixes).
SCOPES = {
    "overview", "episodes", "episode", "plan", "actions", "approvals", "routine", "checkins",
    "messages", "followups", "memory", "activity", "reviews", "executions", "notes", "settings",
    "clock", "today", "instructions", "summary",
}
# Scopes a patient may receive (scrubbed notices only, for their own episodes).
PATIENT_SCOPES = {"today", "checkins", "messages", "followups", "instructions", "clock"}


class EventBus:
    """Thread-safe fan-out to asyncio queues (one per SSE connection)."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, notice: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                loop.call_soon_threadsafe(_safe_put, q, notice)
            except RuntimeError:
                pass


def _safe_put(q: asyncio.Queue, notice: dict) -> None:
    try:
        q.put_nowait(notice)
    except asyncio.QueueFull:
        # Slow client: it will recover by refetching on reconnect (Last-Event-ID replay).
        pass


bus = EventBus()


def notice_for(ev: AuditEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "kind": ev.kind,
        "episode_id": ev.episode_id,
        "scopes": list(ev.scopes or []),
        "patient_visible": bool(ev.patient_visible),
        "at": iso(ev.created_at),
    }


def record_event(
    session: Session,
    kind: str,
    title: str,
    *,
    episode_id: Optional[int] = None,
    actor_type: str = "system",
    actor_id: Optional[int] = None,
    actor_label: str = "",
    detail: Optional[dict[str, Any]] = None,
    scopes: Optional[list[str]] = None,
    patient_visible: bool = False,
) -> AuditEvent:
    """Persist an audit event in the caller's transaction; publish after commit.

    kind: dotted verb, e.g. "plan.created", "action.approved", "task.failed" (CONTRACTS.md §7).
    actor_type: user | patient | agent | worker | system.
    patient_visible: True only if the patient UI should refetch (never exposes `detail`).
    """
    bad = set(scopes or []) - SCOPES
    if bad:
        raise ValueError(f"Unknown SSE scopes: {sorted(bad)}")
    ev = AuditEvent(
        episode_id=episode_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        kind=kind,
        title=title[:255],
        detail=detail or {},
        scopes=sorted(set(scopes or []) | {"activity"}),
        patient_visible=patient_visible,
    )
    session.add(ev)
    session.flush()  # assigns ev.id inside the transaction
    session.info.setdefault("ns_pending_events", []).append(ev)
    return ev


@sa_event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    pending = session.info.pop("ns_pending_events", [])
    for ev in pending:
        try:
            bus.publish(notice_for(ev))
        except Exception:  # never let notification break a committed transaction
            log.exception("SSE publish failed for event %s", getattr(ev, "id", "?"))


@sa_event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop("ns_pending_events", None)
