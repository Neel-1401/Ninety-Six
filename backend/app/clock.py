"""Shared clock abstraction. LEAD-OWNED CONTRACT.

ALL scheduling and domain timestamps use `clock.now()` (aware UTC). Never call datetime.now()
for domain logic. Simulated time = real UTC time + a persisted offset (SystemState
'sim_clock'), so a restart keeps the simulated date. Advancing is only possible in demo mode.
Security timers (OTP expiry, session expiry, task leases) intentionally use REAL time via
`clock.real_now()` so fast-forwarding cannot expire or extend credentials.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_KEY = "sim_clock"


class SimClock:
    def __init__(self) -> None:
        self._offset = timedelta(0)
        self._lock = threading.Lock()

    # ---- reading
    def real_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def now(self) -> datetime:
        with self._lock:
            return self.real_now() + self._offset

    @property
    def offset_hours(self) -> float:
        with self._lock:
            return self._offset.total_seconds() / 3600

    # ---- persistence (called by db.init_db at startup and by the worker each tick)
    def load(self, session) -> None:  # noqa: ANN001 - avoid importing Session types here
        from .models import SystemState

        row = session.get(SystemState, _KEY)
        seconds = float((row.value or {}).get("offset_seconds", 0)) if row else 0.0
        with self._lock:
            self._offset = timedelta(seconds=seconds)

    def _save(self, session) -> None:  # noqa: ANN001
        from .models import SystemState

        row = session.get(SystemState, _KEY)
        value = {"offset_seconds": self._offset.total_seconds()}
        if row is None:
            session.add(SystemState(key=_KEY, value=value))
        else:
            row.value = value  # reassign JSON
        session.flush()

    # ---- demo controls (caller must check settings.demo_mode and commit the session)
    def advance(self, session, hours: float) -> datetime:  # noqa: ANN001
        if hours <= 0 or hours > 96:
            raise ValueError("hours must be in (0, 96]")
        with self._lock:
            self._offset += timedelta(hours=hours)
        self._save(session)
        return self.now()

    def reset(self, session) -> None:  # noqa: ANN001
        with self._lock:
            self._offset = timedelta(0)
        self._save(session)


clock = SimClock()


def iso(dt: datetime | None) -> str | None:
    """Serialize as ISO-8601 UTC with 'Z'. The frontend converts to Asia/Kolkata for display."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_display_tz(dt: datetime, tz: str = "Asia/Kolkata") -> datetime:
    return dt.astimezone(ZoneInfo(tz))


def hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600
