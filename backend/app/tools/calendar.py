"""Calendar adapter (Agent B): Google Calendar event creation via a service account.

- A calendar event is NOT a confirmed hospital appointment; availability is never checked here.
- Scheduling rule: next weekday 10:30 AM Asia/Kolkata, (within_days - 1) days from now, minimum 1.
- Idempotency: the Google event id is derived from the idempotency key, so a retry after an
  uncertain outcome cannot double-book (HTTP 409 => already created => success).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..clock import clock, iso
from ..config import settings
from .base import ToolResult, ToolStatus

log = logging.getLogger("ninety_six.tools.calendar")


def propose_slot(within_days: Optional[int], *, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.display_timezone)
    local_now = (now or clock.now()).astimezone(tz)
    days = max(1, min(int(within_days or 7), 14) - 1)
    day = (local_now + timedelta(days=days)).date()
    while day.weekday() >= 5:  # Saturday/Sunday -> next Monday
        day += timedelta(days=1)
    start = datetime(day.year, day.month, day.day, 10, 30, tzinfo=tz).astimezone(timezone.utc)
    return start, start + timedelta(minutes=30)


def event_id_for(idempotency_key: str) -> str:
    # Google allows base32hex chars [a-v0-9]; hex digits are a subset.
    return "ns" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:30]


class CalendarAdapter:
    name = "calendar"

    def mode(self) -> str:
        return "live" if settings.calendar_configured else "simulated"

    def status(self) -> ToolStatus:
        if settings.calendar_configured:
            return ToolStatus("calendar", "Google Calendar", "live", True,
                              "Service account configured; events are created on the configured calendar. "
                              "Availability is NOT checked.")
        return ToolStatus("calendar", "Google Calendar", "simulated", False,
                          "Google Calendar not configured: events are recorded locally only")

    def execute(self, request: dict[str, Any], *, idempotency_key: str) -> ToolResult:
        start = request["start_at"]
        end = request["end_at"]
        event_id = event_id_for(idempotency_key)
        if self.mode() == "simulated":
            return ToolResult("success", "simulated", "local", provider_ref=f"sim-{event_id}",
                              provider_status="simulated", delivery_status="not_applicable",
                              data={"availability_checked": False, "start_at": start, "end_at": end})
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except ImportError:
            return ToolResult("failure", "live", "google_calendar", safe_error="Google client libraries missing",
                              retryable=False)
        try:
            creds = service_account.Credentials.from_service_account_file(
                settings.google_sa_file, scopes=["https://www.googleapis.com/auth/calendar"])
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            body = {
                "id": event_id,
                "summary": request.get("title", "Follow-up"),
                "description": request.get("description", ""),
                "location": request.get("location", ""),
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            }
            try:
                created = service.events().insert(calendarId=settings.google_calendar_id, body=body).execute()
            except HttpError as e:
                status = getattr(e, "status_code", None) or getattr(getattr(e, "resp", None), "status", None)
                if int(status or 0) == 409:  # already created by an earlier attempt
                    created = service.events().get(calendarId=settings.google_calendar_id, eventId=event_id).execute()
                elif int(status or 0) >= 500:
                    return ToolResult("unknown", "live", "google_calendar",
                                      safe_error=f"Google Calendar server error {status}; outcome uncertain",
                                      data={"idempotent_retry_safe": True})
                else:
                    return ToolResult("failure", "live", "google_calendar",
                                      safe_error=f"Google Calendar rejected the event (HTTP {status})", retryable=False)
            return ToolResult("success", "live", "google_calendar", provider_ref=created.get("id"),
                              provider_status=created.get("status"), delivery_status="not_applicable",
                              data={"html_link": created.get("htmlLink"), "availability_checked": False,
                                    "start_at": start, "end_at": end})
        except (TimeoutError, OSError) as e:
            return ToolResult("unknown", "live", "google_calendar",
                              safe_error=f"network problem talking to Google ({type(e).__name__}); outcome uncertain",
                              data={"idempotent_retry_safe": True})
        except Exception as e:  # noqa: BLE001
            log.warning("Calendar call failed: %s", type(e).__name__)
            return ToolResult("failure", "live", "google_calendar", safe_error=f"calendar error ({type(e).__name__})",
                              retryable=False)


__all__ = ["CalendarAdapter", "propose_slot", "event_id_for", "iso"]
