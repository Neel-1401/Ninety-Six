"""Message templates (Agent B). Clinical content comes ONLY from doctor-confirmed facts; templates
never add advice, doses or diagnoses. Rendering is deterministic so payload hashes are stable.

Template ids and their approval class (policy/approval.py decides the requirement):
  welcome_v1            patient  explicit  reviewed medicines + warning signs + how check-ins work
  checkin_reminder_v1   patient  routine   non-clinical "your check-in is ready" + link
  closing_v1            patient  routine   monitoring ended; NOT medical clearance
  concern_reply_v1      patient  explicit  acknowledgement after a concern (sent only after approval)
  urgent_safety_v1      patient  explicit  contact the hospital now / emergency number
  caregiver_brief_v1    caregiver explicit  authorised-contact briefing
  caregiver_update_v1   caregiver explicit  ask caregiver to check on patient after a concern
  care_team_alert_v1    care_team explicit  email to the care team
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import DischargeEpisode, Patient
from .memory import active_facts, fact_text

TEMPLATE_IDS = {
    "welcome_v1", "checkin_reminder_v1", "closing_v1", "concern_reply_v1", "urgent_safety_v1",
    "caregiver_brief_v1", "caregiver_update_v1", "care_team_alert_v1",
}
PATIENT_TEMPLATES = {"welcome_v1", "checkin_reminder_v1", "closing_v1", "concern_reply_v1", "urgent_safety_v1"}
CAREGIVER_TEMPLATES = {"caregiver_brief_v1", "caregiver_update_v1"}
TEAM_TEMPLATES = {"care_team_alert_v1"}
MESSAGE_KIND = {
    "welcome_v1": "welcome", "checkin_reminder_v1": "reminder", "closing_v1": "reminder",
    "concern_reply_v1": "reply", "urgent_safety_v1": "safety", "caregiver_brief_v1": "caregiver",
    "caregiver_update_v1": "caregiver", "care_team_alert_v1": "care_team_alert",
}


def _first(name: str) -> str:
    return name.split()[0] if name else "there"


def patient_link() -> str:
    return f"{settings.public_base_url.rstrip('/')}/p"


def build_context(
    session: Session, episode: DischargeEpisode, *, confirmed_only: bool = True, extra: Optional[dict] = None
) -> dict[str, Any]:
    p = session.get(Patient, episode.patient_id)
    facts = active_facts(session, episode.id)

    def pick(cat: str) -> list[str]:
        return [
            fact_text(f)
            for f in facts
            if f.category == cat and not f.is_missing and (not confirmed_only or f.review_state == "confirmed")
        ]

    meds = []
    for f in facts:
        if f.category != "medication" or f.is_missing or (confirmed_only and f.review_state != "confirmed"):
            continue
        v = f.value or {}
        dose = v.get("dose") if v.get("dose") not in (None, "", "not provided") else "dose as on your discharge summary"
        freq = v.get("frequency") if v.get("frequency") not in (None, "", "not provided") else ""
        meds.append(" ".join(x for x in [v.get("name", f.label), dose, freq] if x))
    ctx = {
        "first_name": _first(p.full_name if p else ""),
        "patient_name": p.full_name if p else "",
        "hospital_number": (p.hospital_number if p else None) or "",
        "caregiver_name": (p.caregiver_name if p else None) or "",
        "hospital": settings.hospital_name,
        "hospital_phone": settings.hospital_phone,
        "emergency": settings.emergency_number,
        "link": patient_link(),
        "medicines": meds,
        "warning_signs": pick("red_flag"),
        "diagnosis": pick("diagnosis"),
    }
    ctx.update(extra or {})
    return ctx


def render(template_id: str, ctx: dict[str, Any]) -> dict[str, str]:
    """Returns the exact payload ({"body"} or {"subject","body"})."""
    c = ctx
    if template_id == "welcome_v1":
        meds = "; ".join(c["medicines"]) or "see your discharge summary"
        signs = "; ".join(s.lower() for s in c["warning_signs"][:5]) or "any new or worsening symptoms"
        return {"body": (
            f"Hello {c['first_name']}, this is {c['hospital']}. Your doctor reviewed your discharge instructions. "
            f"Your medicines: {meds}. Contact us if you notice: {signs}. "
            f"For the next 4 days we will send short check-ins on {c['link']} (sign in with this phone number). "
            f"Questions: {c['hospital_phone']}. Emergency: call {c['emergency']}."
        )}
    if template_id == "checkin_reminder_v1":
        title = c.get("checkin_title", "check-in")
        return {"body": (
            f"Hello {c['first_name']}, your {title} from {c['hospital']} is ready at {c['link']}. "
            f"It takes about 2 minutes. If you feel very unwell, call {c['emergency']}."
        )}
    if template_id == "closing_v1":
        return {"body": (
            f"Hello {c['first_name']}, the 96-hour follow-up check-ins from {c['hospital']} have ended. "
            f"This does not mean you have been medically cleared. Keep your follow-up appointments and keep following "
            f"your discharge instructions. Questions: {c['hospital_phone']}. Emergency: call {c['emergency']}."
        )}
    if template_id == "concern_reply_v1":
        return {"body": (
            f"Hello {c['first_name']}, thank you for your check-in. Your care team at {c['hospital']} has looked at "
            f"your answers and a nurse will contact you today. Please do not stop or change any medicine on your own. "
            f"If you feel worse, call {c['hospital_phone']}, or {c['emergency']} in an emergency."
        )}
    if template_id == "urgent_safety_v1":
        return {"body": (
            f"Hello {c['first_name']}, this is {c['hospital']}. Your care team has seen your check-in. "
            f"Your discharge instructions say these symptoms need prompt attention: please call {c['hospital_phone']} "
            f"now or go to the nearest emergency department. If you feel very unwell, call {c['emergency']} immediately."
        )}
    if template_id == "caregiver_brief_v1":
        signs = "; ".join(s.lower() for s in c["warning_signs"][:4]) or "any new or worsening symptoms"
        return {"body": (
            f"Hello {c['caregiver_name'] or 'there'}, you are listed as an authorised contact for {c['patient_name']}, "
            f"who was discharged from {c['hospital']}. Our team will check in with {c['first_name']} over the next 4 days. "
            f"If you notice {signs}, please call {c['hospital_phone']} (emergency: {c['emergency']})."
        )}
    if template_id == "caregiver_update_v1":
        return {"body": (
            f"Hello {c['caregiver_name'] or 'there'}, {c['first_name']} reported a concern in today's check-in with "
            f"{c['hospital']}. Our care team is following up. Could you check on {c['first_name']} today? "
            f"Hospital: {c['hospital_phone']}. Emergency: {c['emergency']}."
        )}
    if template_id == "care_team_alert_v1":
        level = c.get("urgency", "urgent").upper()
        reasons = "; ".join(c.get("reasons", [])) or "see the check-in"
        answers = "\n".join(f"- {a}" for a in c.get("answer_lines", [])) or "- (no structured answers)"
        return {
            "subject": f"[Ninety-Six][{level}] {c['patient_name']} ({c['hospital_number'] or 'no MRN'}) - check-in needs review",
            "body": (
                f"Patient: {c['patient_name']} ({c['hospital_number'] or 'no MRN'})\n"
                f"Flagged by deterministic check-in rules: {reasons}\n"
                f"Patient answers:\n{answers}\n"
                f"Free text: {c.get('free_text') or '(none)'}\n\n"
                f"This alert was prepared by the Ninety-Six agent and approved by a doctor before sending. "
                f"It is not a diagnosis. Open the episode in Ninety-Six to review."
            ),
        }
    raise ValueError(f"Unknown template {template_id}")
