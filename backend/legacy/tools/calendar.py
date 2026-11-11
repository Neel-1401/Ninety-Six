import logging
from datetime import timedelta

from ..clock import clock
from ..config import settings
from ..events import log_event
from ..models import Appointment
from .base import Tool, ToolContext

log = logging.getLogger("ninety_six.tools.calendar")


def _next_slot(within_days: int):
    """Pick a 10:30 AM IST (05:00 UTC) slot about one day before the deadline."""
    days = max(1, min(int(within_days or 7), 14) - 1)
    day = clock.now() + timedelta(days=days)
    if day.weekday() == 6:  # avoid Sunday
        day += timedelta(days=1)
    return day.replace(hour=5, minute=0, second=0, microsecond=0)


class BookAppointmentTool(Tool):
    name = "book_appointment"
    description = "Book a follow-up appointment on the hospital calendar (Google Calendar)."
    args_doc = '{"title": "Cardiology review", "provider": "Dr. Mehta", "within_days": 7, "location": "OPD 2"}'
    irreversible = True

    def live(self) -> bool:
        return settings.calendar_live

    def preview(self, ctx: ToolContext, args: dict) -> str:
        start = _next_slot(args.get("within_days", 7))
        return (
            f"Book “{args.get('title', 'Follow-up')}” with {args.get('provider') or 'the clinic'} "
            f"on {start.strftime('%a %d %b')} (within {args.get('within_days', 7)} days)"
        )

    def run(self, ctx: ToolContext, args: dict) -> dict:
        p = ctx.patient
        start = _next_slot(args.get("within_days", 7))
        title = args.get("title") or "Post-discharge follow-up"
        provider = args.get("provider", "")
        location = args.get("location", settings.hospital_name)
        external_id, cal = "", "simulated"

        if self.live():
            try:
                from google.oauth2 import service_account  # optional dependency
                from googleapiclient.discovery import build

                creds = service_account.Credentials.from_service_account_file(
                    settings.google_sa_file, scopes=["https://www.googleapis.com/auth/calendar"]
                )
                service = build("calendar", "v3", credentials=creds, cache_discovery=False)
                event = {
                    "summary": f"{title} — {p.name}",
                    "description": f"Booked by Ninety-Six agent. Provider: {provider}. Diagnosis: {p.diagnosis}",
                    "location": location,
                    "start": {"dateTime": start.isoformat() + "Z"},
                    "end": {"dateTime": (start + timedelta(minutes=30)).isoformat() + "Z"},
                }
                created = service.events().insert(calendarId=settings.google_calendar_id, body=event).execute()
                external_id, cal = created.get("id", ""), "google"
            except Exception as exc:
                log.warning("Google Calendar failed, falling back to simulated: %s", exc)

        appt = Appointment(
            patient_id=p.id, title=title, provider=provider, start_at=start,
            location=location, external_id=external_id, calendar=cal,
        )
        ctx.session.add(appt)
        ctx.session.commit()
        log_event(ctx.session, "tool", f"Booked {title} ({cal})", p.id, {"start": start.isoformat()})
        return {"appointment_id": appt.id, "start": start.isoformat() + "Z", "calendar": cal, "external_id": external_id}
