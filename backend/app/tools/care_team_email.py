"""Care-team alert email adapter (Agent B): SMTP with an honest simulated mode."""
from __future__ import annotations

import hashlib
import smtplib
import socket
from email.message import EmailMessage
from typing import Any

from ..config import settings
from .base import ToolResult, ToolStatus


class CareTeamEmailAdapter:
    name = "care_team_email"

    def mode(self) -> str:
        return "live" if settings.email_configured else "simulated"

    def recipient(self) -> str:
        return settings.care_team_email or "care-team (not configured)"

    def status(self) -> ToolStatus:
        if settings.email_configured:
            return ToolStatus("care_team_email", "Care-team email (SMTP)", "live", True,
                              f"SMTP {settings.smtp_host}:{settings.smtp_port} configured")
        return ToolStatus("care_team_email", "Care-team email (SMTP)", "simulated", False,
                          "SMTP not configured: alerts are recorded locally and NOT emailed")

    def execute(self, request: dict[str, Any], *, idempotency_key: str) -> ToolResult:
        if self.mode() == "simulated":
            return ToolResult("success", "simulated", "local",
                              provider_ref="sim-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16],
                              provider_status="simulated", delivery_status="simulated")
        msg = EmailMessage()
        msg["Subject"] = request.get("subject", "[Ninety-Six] Care-team alert")
        msg["From"] = settings.smtp_user
        msg["To"] = settings.care_team_email
        msg["Message-ID"] = f"<{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}@ninety-six.local>"
        msg.set_content(request.get("body", ""))
        try:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
        except (OSError, socket.timeout):
            return ToolResult("failure", "live", "smtp", safe_error="could not connect to the SMTP server (nothing sent)",
                              retryable=True)
        try:
            with server:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
                refused = server.send_message(msg)
            if refused:
                return ToolResult("failure", "live", "smtp", safe_error="recipient refused by SMTP server", retryable=False)
            return ToolResult("success", "live", "smtp", provider_ref=msg["Message-ID"], provider_status="accepted",
                              delivery_status="provider_accepted")
        except smtplib.SMTPAuthenticationError:
            return ToolResult("failure", "live", "smtp", safe_error="SMTP authentication failed", retryable=False)
        except smtplib.SMTPRecipientsRefused:
            return ToolResult("failure", "live", "smtp", safe_error="recipient refused by SMTP server", retryable=False)
        except (socket.timeout, smtplib.SMTPServerDisconnected, OSError) as e:
            return ToolResult("unknown", "live", "smtp", delivery_status="unknown",
                              safe_error=f"SMTP connection dropped ({type(e).__name__}); email may or may not have been sent")
        except smtplib.SMTPException as e:
            return ToolResult("failure", "live", "smtp", safe_error=f"SMTP error ({type(e).__name__})", retryable=False)
