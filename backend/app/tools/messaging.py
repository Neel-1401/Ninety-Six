"""Messaging adapter (Agent B): Twilio SMS (WhatsApp sandbox optional) with an honest simulated mode.

Mode rules (decided BEFORE approval and shown on the card as expected_mode):
- live only if Twilio is configured AND the effective recipient is DEMO_PHONE (or
  ALLOW_LIVE_TO_REAL_RECIPIENTS=true). Otherwise simulated, with the reason in the result.
- Twilio 201 -> outcome success, delivery_status "provider_accepted" (never "delivered").
- Read timeout / 5xx after sending -> outcome "unknown" (the SMS may have gone out); no blind retry.
- Connect error / connect timeout / 429 -> failure, retryable (nothing was sent).
- Other 4xx -> failure, not retryable.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from ..clock import iso
from ..config import settings
from .base import ToolResult, ToolStatus

log = logging.getLogger("ninety_six.tools.messaging")


def mask_phone(number: str | None) -> str:
    if not number:
        return ""
    digits = number.replace("whatsapp:", "")
    return digits[:3] + "•" * max(0, len(digits) - 7) + digits[-4:] if len(digits) > 7 else "•••"


class MessagingAdapter:
    name = "messaging"

    def effective_recipient(self, phone: str | None) -> str:
        return settings.demo_phone or (phone or "")

    def redirected(self, phone: str | None) -> bool:
        return bool(settings.demo_phone) and (phone or "") != settings.demo_phone

    def mode_for(self, effective: str | None) -> str:
        if not settings.twilio_configured or not effective:
            return "simulated"
        if settings.demo_phone and effective == settings.demo_phone:
            return "live"
        return "live" if settings.allow_live_to_real_recipients else "simulated"

    def _simulated_reason(self, effective: str | None) -> str:
        if not settings.twilio_configured:
            return "Twilio not configured"
        if not effective:
            return "no recipient number"
        return "live sending to non-demo recipients is disabled (set DEMO_PHONE)"

    def status(self) -> ToolStatus:
        if settings.twilio_configured:
            redirect = f"DEMO_PHONE redirect active ({mask_phone(settings.demo_phone)})" if settings.demo_phone else (
                "no DEMO_PHONE: live sends refused unless ALLOW_LIVE_TO_REAL_RECIPIENTS=true")
            mode = "live" if settings.demo_phone or settings.allow_live_to_real_recipients else "simulated"
            return ToolStatus("messaging", "Twilio SMS", mode, True,
                              f"Twilio {settings.twilio_channel.upper()} configured; {redirect}")
        return ToolStatus("messaging", "Twilio SMS", "simulated", False,
                          "Twilio not configured: messages are recorded locally and NOT sent")

    def execute(self, request: dict[str, Any], *, idempotency_key: str) -> ToolResult:
        to = request.get("to") or ""
        body = (request.get("body") or "").strip()
        channel = request.get("channel") or settings.twilio_channel or "sms"
        if not body:
            return ToolResult("failure", "simulated", "local", safe_error="empty message body", retryable=False)
        mode = self.mode_for(to)
        if mode == "simulated":
            ref = "sim-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
            return ToolResult("success", "simulated", "local", provider_ref=ref, provider_status="simulated",
                              delivery_status="simulated", data={"reason": self._simulated_reason(to)})
        from_ = settings.twilio_from
        if channel == "whatsapp" or from_.startswith("whatsapp:"):
            from_ = from_ if from_.startswith("whatsapp:") else f"whatsapp:{from_}"
            to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json"
        try:
            r = httpx.post(url, data={"From": from_, "To": to, "Body": body},
                           auth=(settings.twilio_sid, settings.twilio_token),
                           timeout=httpx.Timeout(20.0, connect=5.0))
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return ToolResult("failure", "live", "twilio", safe_error="could not connect to Twilio (nothing sent)",
                              retryable=True)
        except httpx.TimeoutException:
            return ToolResult("unknown", "live", "twilio", delivery_status="unknown",
                              safe_error="Twilio did not answer in time; the message may or may not have been sent")
        except httpx.HTTPError as e:
            return ToolResult("unknown", "live", "twilio", delivery_status="unknown",
                              safe_error=f"network error after sending ({type(e).__name__})")
        if r.status_code in (200, 201):
            j = r.json()
            return ToolResult("success", "live", "twilio", provider_ref=j.get("sid"),
                              provider_status=j.get("status"), delivery_status="provider_accepted",
                              data={"to_masked": mask_phone(to)})
        code, msg = "", ""
        try:
            j = r.json()
            code, msg = str(j.get("code", "")), str(j.get("message", ""))[:120]
        except Exception:  # noqa: BLE001
            pass
        msg = msg.replace(to, mask_phone(to)) if to else msg
        if r.status_code == 429:
            return ToolResult("failure", "live", "twilio", safe_error="Twilio rate limit (nothing sent)", retryable=True)
        if r.status_code >= 500:
            return ToolResult("unknown", "live", "twilio", delivery_status="unknown",
                              safe_error=f"Twilio server error {r.status_code}; outcome uncertain")
        return ToolResult("failure", "live", "twilio", delivery_status="failed",
                          safe_error=f"Twilio rejected the message (HTTP {r.status_code}, code {code}): {msg}",
                          retryable=False)
