"""Durable sliding-window rate limits backed by RateLimitHit (REAL time, not the simulated clock)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import errors
from ..clock import clock
from ..models import RateLimitHit

# bucket -> (max hits, window seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "otp_request_phone": (5, 3600),
    "otp_request_ip": (20, 600),
    "otp_verify_phone": (10, 600),
    "otp_verify_ip": (30, 600),
    "staff_login_user": (10, 600),
    "staff_login_ip": (30, 600),
}


def check_and_hit(session: Session, bucket: str, key: str) -> None:
    """Raise 429 if `key` exceeded the bucket limit; otherwise record a hit (flushes, no commit)."""
    limit, window = LIMITS[bucket]
    now = clock.real_now()
    since = now - timedelta(seconds=window)
    count = session.scalar(
        select(func.count(RateLimitHit.id)).where(
            RateLimitHit.bucket == bucket, RateLimitHit.key == key, RateLimitHit.at >= since
        )
    ) or 0
    if count >= limit:
        oldest = session.scalar(
            select(func.min(RateLimitHit.at)).where(
                RateLimitHit.bucket == bucket, RateLimitHit.key == key, RateLimitHit.at >= since
            )
        )
        retry = int(((oldest + timedelta(seconds=window)) - now).total_seconds()) + 1 if oldest else window
        raise errors.rate_limited(retry_after=max(1, retry))
    session.add(RateLimitHit(bucket=bucket, key=key, at=now))
    session.flush()


def prune(session: Session) -> None:
    session.execute(delete(RateLimitHit).where(RateLimitHit.at < clock.real_now() - timedelta(days=1)))
