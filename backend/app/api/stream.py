"""Server-Sent Events (CONTRACTS.md §7). Notices are hints to refetch, never clinical content.

Replay: `Last-Event-ID` header (sent automatically by EventSource on reconnect) or `?last_event_id=`.
`?replay_only=true` returns the replay and closes (used by tests and for debugging)."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .. import db
from ..auth.deps import current_actor, visible_episode_ids
from ..auth.sessions import Actor
from ..events import PATIENT_SCOPES, bus, notice_for
from ..models import AuditEvent

router = APIRouter(prefix="/api", tags=["stream"])

PING_SECONDS = 15
REPLAY_LIMIT = 500
ACCESS_REFRESH_SECONDS = 20


def filter_notice(notice: dict[str, Any], *, is_patient: bool, episode_ids: set[int]) -> Optional[dict[str, Any]]:
    """Return the notice as this user may see it, or None."""
    ep = notice.get("episode_id")
    if is_patient:
        if not notice.get("patient_visible") or ep is None or ep not in episode_ids:
            return None
        scopes = sorted(set(notice.get("scopes") or []) & PATIENT_SCOPES)
        if not scopes:
            return None
        return {"id": notice["id"], "kind": "update", "episode_id": ep, "scopes": scopes, "at": notice.get("at")}
    if ep is not None and ep not in episode_ids:
        return None
    return {k: notice[k] for k in ("id", "kind", "episode_id", "scopes", "at") if k in notice}


def _frame(n: dict[str, Any]) -> str:
    return f"id: {n['id']}\nevent: update\ndata: {json.dumps(n, separators=(',', ':'))}\n\n"


class _Access:
    def __init__(self, actor_user_id: int, is_patient: bool, actor: Actor) -> None:
        self.user_id, self.is_patient, self._actor = actor_user_id, is_patient, actor
        self.ids: set[int] = set()
        self._at = 0.0

    def refresh(self, force: bool = False) -> set[int]:
        if force or time.monotonic() - self._at > ACCESS_REFRESH_SECONDS:
            with db.SessionLocal() as s:
                self.ids = visible_episode_ids(s, self._actor)
            self._at = time.monotonic()
        return self.ids


@router.get("/stream")
async def stream(request: Request, last_event_id: Optional[int] = None, replay_only: bool = False,
                 actor: Actor = Depends(current_actor)):
    header = request.headers.get("last-event-id")
    since = last_event_id
    if header and header.strip().isdigit():
        since = int(header.strip())
    access = _Access(actor.id, actor.is_patient, actor)
    access.refresh(force=True)
    queue = None if replay_only else bus.subscribe()

    async def gen():
        replayed: set[int] = set()
        try:
            yield "retry: 3000\n\n"
            if since is not None:
                with db.SessionLocal() as s:
                    rows = s.scalars(
                        select(AuditEvent).where(AuditEvent.id > since).order_by(AuditEvent.id).limit(REPLAY_LIMIT)
                    ).all()
                    notices = [notice_for(e) for e in rows]
                for n in notices:
                    out = filter_notice(n, is_patient=access.is_patient, episode_ids=access.ids)
                    if out:
                        yield _frame(out)
                    replayed.add(n["id"])
            if replay_only or queue is None:
                return
            while True:
                if await request.is_disconnected():
                    break
                try:
                    n = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if n.get("id") in replayed:
                    replayed.discard(n["id"])  # already delivered by the replay
                    continue
                ids = access.refresh(force=n.get("episode_id") is not None and n.get("episode_id") not in access.ids)
                out = filter_notice(n, is_patient=access.is_patient, episode_ids=ids)
                if out:
                    yield _frame(out)
        finally:
            if queue is not None:
                bus.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
