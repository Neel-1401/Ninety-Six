r"""Verify each configured live integration once, against the test recipients in backend/.env.

Run from backend/:  .\.venv\Scripts\python.exe scripts\verify_live.py [openfda] [twilio] [email] [calendar]
With no arguments every *configured* integration is checked; unconfigured ones are reported as skipped.
Messages go only to DEMO_PHONE / CARE_TEAM_EMAIL; nothing is written to the database.
Prints provider references (Twilio SID, Calendar event id) to paste into FEATURES.md as evidence.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.tools.calendar import CalendarAdapter  # noqa: E402
from app.tools.care_team_email import CareTeamEmailAdapter  # noqa: E402
from app.tools.medication_info import MedicationInfoAdapter  # noqa: E402
from app.tools.messaging import MessagingAdapter  # noqa: E402

STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
wanted = set(a.lower() for a in sys.argv[1:]) or {"openfda", "twilio", "email", "calendar"}
results: list[tuple[str, str, str]] = []


def report(name: str, r) -> None:  # noqa: ANN001
    ref = r.provider_ref or "-"
    results.append((name, f"{r.outcome}/{r.mode}", f"ref={ref} {r.safe_error or ''}".strip()))


if "openfda" in wanted:
    if settings.openfda_enabled:
        r = MedicationInfoAdapter().execute({"medications": ["lisinopril", "spironolactone"]}, idempotency_key=f"verify-{uuid.uuid4()}")
        found = [d["label_status"] for d in r.data["drugs"]]
        results.append(("openfda", f"{r.outcome}/{r.mode}", f"labels={found} calls={r.data['openfda_calls']}"))
    else:
        results.append(("openfda", "skipped", "OPENFDA_ENABLED=false"))

if "twilio" in wanted:
    if settings.twilio_configured and settings.demo_phone:
        r = MessagingAdapter().execute({"to": settings.demo_phone, "body": f"Ninety-Six live check {STAMP}. Test message, no action needed."},
                                       idempotency_key=f"verify-{uuid.uuid4()}")
        report("twilio", r)
    else:
        results.append(("twilio", "skipped", "needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM and DEMO_PHONE"))

if "email" in wanted:
    if settings.email_configured:
        r = CareTeamEmailAdapter().execute({"subject": f"[Ninety-Six] Live check {STAMP}",
                                            "body": "Test care-team alert from the Ninety-Six live check. Fictional data; no action needed."},
                                           idempotency_key=f"verify-{uuid.uuid4()}")
        report("email", r)
    else:
        results.append(("email", "skipped", "needs SMTP_HOST, SMTP_USER, SMTP_PASSWORD and CARE_TEAM_EMAIL"))

if "calendar" in wanted:
    if settings.calendar_configured:
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
        r = CalendarAdapter().execute({"title": "Ninety-Six live check (test event)", "description": "Fictional test event.",
                                       "start_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end_at": (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "location": "Test"},
                                      idempotency_key=f"verify-{uuid.uuid4()}")
        report("calendar", r)
    else:
        results.append(("calendar", "skipped", "needs GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_CALENDAR_ID"))

print(f"\nLive integration check - {STAMP}")
for name, status, detail in results:
    print(f"  {name:<9} {status:<18} {detail}")
sys.exit(0 if all(s.startswith(("success", "skipped")) for _, s, _ in results) else 1)
