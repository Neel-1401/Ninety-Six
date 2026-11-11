"""In-process pub/sub that feeds the UI over Server-Sent Events, plus the
helper that writes the agent trace."""
import asyncio
import threading
from typing import Any, Optional

from sqlmodel import Session

from .models import Event


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, data: dict[str, Any]) -> None:
        if not self._loop:
            return
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                self._loop.call_soon_threadsafe(_safe_put, q, data)
            except RuntimeError:
                pass  # loop closed


def _safe_put(q: asyncio.Queue, data: dict) -> None:
    try:
        q.put_nowait(data)
    except asyncio.QueueFull:
        pass


bus = EventBus()


def log_event(
    session: Session,
    kind: str,
    title: str,
    patient_id: Optional[int] = None,
    detail: Optional[dict] = None,
) -> Event:
    ev = Event(patient_id=patient_id, kind=kind, title=title, detail=detail or {})
    session.add(ev)
    session.commit()
    session.refresh(ev)
    bus.publish({"type": kind, "title": title, "patient_id": patient_id, "event_id": ev.id})
    return ev
