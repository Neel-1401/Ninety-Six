"""Shared route helpers."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from ..auth.sessions import Actor
from ..events import record_event

log = logging.getLogger("ninety_six.api")


def kick_worker() -> None:
    """Wake Agent B's worker after committing something that may be runnable now."""
    try:
        from ..execution import worker  # Agent B

        worker.kick()
    except Exception:  # worker not available yet / disabled
        pass


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(
    session: Session,
    actor: Optional[Actor],
    kind: str,
    title: str,
    *,
    episode_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
    scopes: Optional[list[str]] = None,
    patient_visible: bool = False,
) -> None:
    if actor is None:
        actor_type, actor_id, label = "system", None, "system"
    else:
        actor_type = "patient" if actor.is_patient else "user"
        actor_id, label = actor.id, actor.label
    record_event(
        session,
        kind,
        title,
        episode_id=episode_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=label,
        detail=detail,
        scopes=scopes,
        patient_visible=patient_visible,
    )
