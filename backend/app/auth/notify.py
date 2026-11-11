"""Operational SMS (OTP + invitation). These carry no clinical content and are sent on an explicit
human action, so they bypass the plan approval gate (docs/DECISIONS.md D11). They still go through
Agent B's messaging adapter (live/simulated, DEMO_PHONE redirect) and are recorded as Message rows.

The adapter call happens OUTSIDE any open transaction: callers commit first.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..clock import clock
from ..config import settings
from ..models import Message, MessageStatus, Mode

log = logging.getLogger("ninety_six.auth.notify")


def _adapter():
    try:
        from ..tools.registry import get_messaging  # Agent B

        return get_messaging()
    except Exception:  # not implemented yet / import error => simulated fallback
        return None


def effective_recipient(phone: str) -> str:
    adapter = _adapter()
    if adapter is not None:
        try:
            return adapter.effective_recipient(phone)
        except Exception:
            pass
    return settings.demo_phone or phone


def send_operational_sms(
    session: Session,
    *,
    episode_id: Optional[int],
    phone: str,
    body: str,
    stored_body: str,
    kind: str,
    idempotency_key: str,
) -> Message:
    """Send (or simulate) an SMS, then persist the Message row (flushes; caller commits).

    `stored_body` is what goes in the database (OTP codes are redacted outside demo mode).
    """
    adapter = _adapter()
    mode, status, provider_ref, error = Mode.SIMULATED, MessageStatus.SIMULATED, None, None
    eff = effective_recipient(phone)
    if not settings.demo_mode and eff != phone:
        # Outside demo mode a redirect would turn one phone into a sign-in device for every patient.
        log.error("Refusing operational SMS: DEMO_PHONE redirect is not allowed outside demo mode")
        adapter = None
        status, error = MessageStatus.FAILED, "Not sent: DEMO_PHONE redirect is not allowed outside demo mode"
    if adapter is not None:
        try:
            res = adapter.execute({"to": phone, "body": body, "channel": "sms"}, idempotency_key=idempotency_key)
            mode = res.mode
            provider_ref = res.provider_ref
            error = res.safe_error
            if res.outcome == "success":
                status = res.delivery_status or (
                    MessageStatus.SIMULATED if res.mode == Mode.SIMULATED else MessageStatus.PROVIDER_ACCEPTED
                )
            elif res.outcome == "unknown":
                status = MessageStatus.UNKNOWN
            else:
                status = MessageStatus.FAILED
        except Exception as exc:  # adapter crashed: record honestly, do not pretend it was sent
            log.warning("operational SMS adapter error: %s", exc)
            mode, status, error = Mode.SIMULATED, MessageStatus.FAILED, "Messaging adapter error"
    msg = Message(
        episode_id=episode_id,
        direction="outbound",
        kind=kind,
        audience="patient",
        channel="sms",
        body=stored_body,
        patient_visible=False,
        intended_recipient=phone,
        effective_recipient=eff,
        mode=mode,
        status=status,
        provider_ref=provider_ref,
        error=error,
        created_at=clock.now(),
        sent_at=clock.now() if status not in (MessageStatus.FAILED,) else None,
        status_updated_at=clock.now(),
    )
    session.add(msg)
    session.flush()
    return msg
