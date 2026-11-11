"""Central configuration. LEAD-OWNED. Every integration is optional: without credentials a tool
runs in SIMULATED mode and says so. Demo-only features require APP_MODE=demo."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        env = os.getenv
        self.app_name = "Ninety-Six"
        # demo | production. Demo enables role switching, demo codes, clock fast-forward, reset.
        self.app_mode = env("APP_MODE", "demo").strip().lower()
        self.hospital_name = env("HOSPITAL_NAME", "Sunrise Multispeciality Hospital (fictional)")
        self.hospital_phone = env("HOSPITAL_PHONE", "+91 80 0000 0000 (demo)")
        self.emergency_number = env("EMERGENCY_NUMBER", "108")
        self.display_timezone = env("DISPLAY_TIMEZONE", "Asia/Kolkata")
        self.database_url = env("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'ninety_six.db').as_posix()}")
        self.frontend_origin = env("FRONTEND_ORIGIN", "http://localhost:5173")
        self.public_base_url = env("PUBLIC_BASE_URL", "http://localhost:5173")  # links inside SMS

        # Sessions / auth
        self.cookie_secure = _bool("COOKIE_SECURE", False)  # set true behind HTTPS
        self.staff_session_hours = _float("STAFF_SESSION_HOURS", 12)
        self.patient_session_hours = _float("PATIENT_SESSION_HOURS", 72)
        self.otp_ttl_seconds = int(_float("OTP_TTL_SECONDS", 600))
        self.otp_max_attempts = int(_float("OTP_MAX_ATTEMPTS", 5))
        self.otp_resend_cooldown_seconds = int(_float("OTP_RESEND_COOLDOWN_SECONDS", 60))
        self.invitation_ttl_hours = _float("INVITATION_TTL_HOURS", 120)

        # Worker
        self.worker_enabled = _bool("WORKER_ENABLED", True)  # run the in-process worker loop
        self.tick_seconds = _float("TICK_SECONDS", 2)
        self.task_lease_seconds = _float("TASK_LEASE_SECONDS", 60)

        # LLM: mock | anthropic | gemini   (mock => deterministic rules only)
        self.llm_provider = env("LLM_PROVIDER", "mock").strip().lower()
        self.anthropic_api_key = env("ANTHROPIC_API_KEY", "")
        self.anthropic_model = env("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.gemini_api_key = env("GEMINI_API_KEY", "")
        self.gemini_model = env("GEMINI_MODEL", "gemini-2.5-flash")
        self.llm_timeout = _float("LLM_TIMEOUT", 40)

        # Messaging: Twilio SMS first (WhatsApp sandbox optional)
        self.twilio_sid = env("TWILIO_ACCOUNT_SID", "")
        self.twilio_token = env("TWILIO_AUTH_TOKEN", "")
        self.twilio_from = env("TWILIO_FROM", "")
        self.twilio_channel = env("TWILIO_CHANNEL", "sms").strip().lower()  # sms | whatsapp
        # DEMO_PHONE redirects ALL outbound patient/caregiver messages to the presenter.
        self.demo_phone = env("DEMO_PHONE", "")
        # Safety: refuse live sends to anything except DEMO_PHONE unless explicitly allowed.
        self.allow_live_to_real_recipients = _bool("ALLOW_LIVE_TO_REAL_RECIPIENTS", False)

        # Calendar: Google Calendar via service account
        self.google_sa_file = env("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        self.google_calendar_id = env("GOOGLE_CALENDAR_ID", "")

        # Care-team alert email (SMTP)
        self.smtp_host = env("SMTP_HOST", "")
        self.smtp_port = int(_float("SMTP_PORT", 587))
        self.smtp_user = env("SMTP_USER", "")
        self.smtp_password = env("SMTP_PASSWORD", "")
        self.care_team_email = env("CARE_TEAM_EMAIL", "")

        # Medication information: openFDA public API (no key)
        self.openfda_enabled = _bool("OPENFDA_ENABLED", True)

    # ---- derived
    @property
    def demo_mode(self) -> bool:
        return self.app_mode == "demo"

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_sid and self.twilio_token and self.twilio_from)

    @property
    def calendar_configured(self) -> bool:
        return bool(self.google_sa_file and self.google_calendar_id)

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.care_team_email)

    @property
    def llm_configured(self) -> bool:
        return (self.llm_provider == "anthropic" and bool(self.anthropic_api_key)) or (
            self.llm_provider == "gemini" and bool(self.gemini_api_key)
        )


settings = Settings()
