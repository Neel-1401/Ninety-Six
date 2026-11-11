"""Central configuration. Every integration is optional: if its keys are missing
the tool runs in simulated mode so the demo never breaks."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.app_name = "Ninety-Six"
        self.hospital_name = os.getenv("HOSPITAL_NAME", "Sunrise Multispeciality Hospital")
        self.database_url = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'ninety_six.db'}")
        self.frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
        self.tick_seconds = float(os.getenv("TICK_SECONDS", "5"))
        self.emergency_number = os.getenv("EMERGENCY_NUMBER", "108")

        # LLM: mock | anthropic | gemini
        self.llm_provider = os.getenv("LLM_PROVIDER", "mock").strip().lower()
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.llm_timeout = float(os.getenv("LLM_TIMEOUT", "40"))

        # Messaging: Twilio SMS / WhatsApp sandbox
        self.twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.twilio_from = os.getenv("TWILIO_FROM", "")  # e.g. whatsapp:+14155238886
        self.demo_phone = os.getenv("DEMO_PHONE", "")  # all outbound goes here during the demo

        # Calendar: Google Calendar via service account
        self.google_sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        self.google_calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "")

        # Escalation: SMTP email to the care team
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.care_team_email = os.getenv("CARE_TEAM_EMAIL", "")

        # Drug info: openFDA public API (no key needed)
        self.openfda_enabled = _bool("OPENFDA_ENABLED", True)

    @property
    def twilio_live(self) -> bool:
        return bool(self.twilio_sid and self.twilio_token and self.twilio_from)

    @property
    def calendar_live(self) -> bool:
        return bool(self.google_sa_file and self.google_calendar_id)

    @property
    def email_live(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.care_team_email)


settings = Settings()
