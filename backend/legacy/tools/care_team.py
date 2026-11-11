import logging
import smtplib
from email.message import EmailMessage

from sqlmodel import select

from ..config import settings
from ..events import log_event
from ..memory import recall, remember
from ..models import Appointment, Medication, Message, Step
from .base import Tool, ToolContext

log = logging.getLogger("ninety_six.tools.care_team")


def _send_email(subject: str, body: str) -> bool:
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, settings.smtp_user, settings.care_team_email
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.starttls()
            s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return True
    except Exception as exc:
        log.warning("SMTP send failed: %s", exc)
        return False


class EscalateTool(Tool):
    name = "escalate_to_care_team"
    description = "Alert the discharging doctor / care team by email with the reason and context."
    args_doc = '{"urgency": "normal|urgent", "reason": "short reason", "details": "context"}'
    irreversible = True

    def live(self) -> bool:
        return settings.email_live

    def preview(self, ctx: ToolContext, args: dict) -> str:
        return f"{args.get('urgency', 'normal').upper()} alert to care team: {args.get('reason', '')}"

    def run(self, ctx: ToolContext, args: dict) -> dict:
        p = ctx.patient
        urgency = args.get("urgency", "normal")
        subject = f"[Ninety-Six][{urgency.upper()}] {p.name} — {args.get('reason', 'Follow-up alert')}"
        body = (
            f"Patient: {p.name} ({p.age or '?'}), {p.diagnosis}\nPhone: {p.phone}\n"
            f"Reason: {args.get('reason')}\nDetails: {args.get('details', '')}\n"
        )
        sent = self.live() and _send_email(subject, body)
        if urgency == "urgent":
            p.status, p.risk_level = "escalated", "high"
            ctx.session.add(p)
            ctx.session.commit()
        log_event(ctx.session, "escalation", subject, p.id, {"delivery": "live" if sent else "simulated", "body": body})
        return {"delivery": "live" if sent else "simulated", "urgency": urgency}


class ApproveScheduleTool(Tool):
    name = "activate_schedule"
    description = "Clinician sign-off that pre-approves the routine templated check-in messages in a group."
    args_doc = '{"group": "checkins"}'
    irreversible = True

    def preview(self, ctx: ToolContext, args: dict) -> str:
        steps = ctx.session.exec(
            select(Step).where(Step.plan_id == ctx.step.plan_id, Step.group == args.get("group", "checkins"))
        ).all()
        hours = ", ".join(f"{s.due_offset_h:g}h" for s in steps if s.id != ctx.step.id)
        return f"Pre-approve routine check-ins at {hours}. Anything unusual will still come back to you."

    def run(self, ctx: ToolContext, args: dict) -> dict:
        group = args.get("group", "checkins")
        steps = ctx.session.exec(select(Step).where(Step.plan_id == ctx.step.plan_id, Step.group == group)).all()
        n = 0
        for s in steps:
            if s.id != ctx.step.id and s.status == "pending":
                s.preapproved = True
                ctx.session.add(s)
                n += 1
        ctx.patient.status = "active" if ctx.patient.status == "planning" else ctx.patient.status
        ctx.session.add(ctx.patient)
        ctx.session.commit()
        return {"preapproved_steps": n, "group": group}


class GenerateSummaryTool(Tool):
    name = "generate_summary"
    description = "Write the 96-hour outcome summary for the discharging doctor and close the episode."
    args_doc = "{}"
    irreversible = False

    def run(self, ctx: ToolContext, args: dict) -> dict:
        p, s = ctx.patient, ctx.session
        msgs = s.exec(select(Message).where(Message.patient_id == p.id)).all()
        inbound = [m for m in msgs if m.direction == "in"]
        flags = [m for m in inbound if m.triage in ("concern", "red_flag")]
        appts = s.exec(select(Appointment).where(Appointment.patient_id == p.id)).all()
        meds = s.exec(select(Medication).where(Medication.patient_id == p.id)).all()
        facts = recall(s, p.id, "96-hour summary")
        alerts = [f.value for f in facts if f.key == "medication_alerts"]

        lines = [
            f"96-hour follow-up summary — {p.name}, {p.diagnosis}",
            f"Outbound messages: {sum(1 for m in msgs if m.direction == 'out')}; replies received: {len(inbound)}",
            f"Concerns / red flags reported: {len(flags)}" + (f" ({'; '.join(m.body[:60] for m in flags)})" if flags else ""),
            "Appointments: " + (", ".join(f"{a.title} {a.start_at:%d %b}" for a in appts) or "none booked"),
            f"Medications on record: {len(meds)}" + (f"; alerts: {' '.join(alerts)}" if alerts else ""),
            f"Final status: {'ESCALATED — needs clinician review' if p.status == 'escalated' else 'Completed without escalation'}",
        ]
        summary = "\n".join(lines)
        remember(s, p.id, "96h_summary", summary, "tool:generate_summary")
        if p.status != "escalated":
            p.status = "completed"
            s.add(p)
            s.commit()
        return {"summary": summary}
