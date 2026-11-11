"""Simulated clock. The 96-hour window is compressed for the demo: the UI can
fast-forward time and the monitor picks up whatever steps fall due."""
from datetime import datetime, timedelta, timezone
import threading


class SimClock:
    def __init__(self) -> None:
        self._offset = timedelta(0)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return datetime.now(timezone.utc).replace(tzinfo=None) + self._offset

    def advance(self, hours: float) -> datetime:
        with self._lock:
            self._offset += timedelta(hours=hours)
        return self.now()

    def reset(self) -> None:
        with self._lock:
            self._offset = timedelta(0)

    @property
    def offset_hours(self) -> float:
        return self._offset.total_seconds() / 3600


clock = SimClock()


def hours_since(dt: datetime) -> float:
    return (clock.now() - dt).total_seconds() / 3600


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None
