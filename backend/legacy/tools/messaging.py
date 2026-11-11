import logging

import httpx

from ..config import settings
from ..events import log_event
from ..models import Message
from .base import Tool, ToolContext

log = logging.getLogger("ninety_six.tools.messaging")


class SendMessageTool(Tool):
    name = "send_message"
    description = "Send an SMS/WhatsApp message to the patient or caregiver (Twilio)."
    args_doc = '{"to": "patient|caregiver", "body": "text", "channel": "sms|whatsapp"}'
    irreversible = True

    def live(self) -> bool:
        return settings.twilio_live

    def preview(self, ctx: ToolContext, args: dict) -> str:
        to = args.get("to", "patient")
        who = ctx.patient.caregiver_name if to == "caregiver" else ctx.patient.name
        return f"Send {args.get('channel', 'sms').upper()} to {who or to}: “{args.get('body', '')[:160]}”"

    def run(self, ctx: ToolContext, args: dict) -> dict:
        p = ctx.patient
        to = args.get("to", "patient")
        body = (args.get("body") or "").strip()
        if not body:
            raise ValueError("Message body is empty")
        channel = args.get("channel", "sms")
        number = settings.demo_phone or (p.caregiver_phone if to == "caregiver" else p.phone)

        delivery, sid, error = "simulated", "", ""
        if self.live() and number:
            try:
                prefix = "whatsapp:" if channel == "whatsapp" or settings.twilio_from.startswith("whatsapp:") else ""
                from_ = settings.twilio_from
                to_ = number if number.startswith(prefix) else f"{prefix}{number}"
                r = httpx.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json",
                    data={"From": from_, "To": to_, "Body": body},
                    auth=(settings.twilio_sid, settings.twilio_token),
                    timeout=15,
                )
                r.raise_for_status()
                sid, delivery = r.json().get("sid", ""), "live"
            except Exception as exc:  # keep the demo alive; record the failure
                error = str(exc)[:200]
                delivery = "failed"
                log.warning("Twilio send failed, falling back to simulated: %s", exc)

        msg = Message(patient_id=p.id, direction="out", recipient=to, channel=channel, body=body, delivery=delivery)
        ctx.session.add(msg)
        ctx.session.commit()
        log_event(ctx.session, "message", f"Message to {to} ({delivery})", p.id, {"body": body, "sid": sid})
        return {"delivery": delivery, "sid": sid, "to": to, "error": error, "message_id": msg.id}
