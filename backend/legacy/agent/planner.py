"""Step 2 of the agent: plan its own steps across the 96-hour window."""
import json

from sqlmodel import Session, select

from ..config import settings
from ..events import log_event
from ..llm import get_llm
from ..memory import facts_as_text, recall
from ..models import Medication, Patient, Plan, Step
from ..tools import REGISTRY, tool_catalog
from .prompts import PLANNER_SYSTEM


def _first(name: str) -> str:
    return name.split()[0] if name else "there"


def checkin_body(p: Patient, hour: float, meds: list[str], red_flags: list[str]) -> str:
    flags = ", ".join(f.lower() for f in red_flags[:3]) or "anything unusual"
    med_txt = ", ".join(meds[:4]) if meds else "your medicines"
    return (
        f"Hi {_first(p.name)}, Ninety-Six here from {settings.hospital_name}. It's been {hour:g} hours since you went home. "
        f"Have you taken {med_txt} as prescribed? Please reply with how you feel — "
        f"tell us straight away if you notice {flags}."
    )


def _rules_plan(p: Patient, parsed: dict, meds: list[Medication]) -> dict:
    med_names = [m.name for m in meds]
    sched = "; ".join(f"{m.name} {m.dose} {m.frequency}".strip() for m in meds)
    hours = [12, 24, 48, 72] if p.risk_level == "high" else [24, 48, 72]
    steps = [
        {"title": "Medication safety review", "tool": "lookup_drug_info", "args": {"medications": med_names},
         "due_offset_h": 0, "rationale": "Catch label warnings and interactions before the patient's first dose at home."},
        {"title": "Send welcome & medication schedule", "tool": "send_message",
         "args": {"to": "patient", "channel": "sms", "body": (
             f"Hi {_first(p.name)}, welcome home from {settings.hospital_name}. I'm Ninety-Six and I'll check on you "
             f"for the next 4 days. Your medicines: {sched}. Reply any time with questions.")},
         "due_offset_h": 0, "rationale": "Patients forget verbal discharge instructions; a written schedule reduces dosing errors."},
    ]
    for fu in parsed.get("follow_up", []):
        steps.append({"title": f"Book {fu.get('title', 'follow-up')}", "tool": "book_appointment",
                      "args": {"title": fu.get("title"), "provider": fu.get("provider", ""), "within_days": fu.get("within_days", 7)},
                      "due_offset_h": 0, "rationale": "A visit within 7 days of discharge lowers 30-day readmission."})
    if p.risk_level == "high" and p.caregiver_phone:
        steps.append({"title": f"Brief caregiver {p.caregiver_name}", "tool": "send_message",
                      "args": {"to": "caregiver", "channel": "sms", "body": (
                          f"Hello {p.caregiver_name}, {p.name} was discharged today after {p.diagnosis}. "
                          f"Please watch for: {', '.join(parsed.get('red_flags', [])[:4])}. Ninety-Six will check in regularly.")},
                      "due_offset_h": 0, "rationale": "High-risk patient — a caregiver doubles the chance a warning sign is noticed."})
    steps.append({"title": "Activate routine check-in schedule", "tool": "activate_schedule", "args": {"group": "checkins"},
                  "due_offset_h": 0, "rationale": "One sign-off for templated check-ins; anything unusual still needs approval."})
    for h in hours:
        steps.append({"title": f"{h}-hour check-in", "tool": "send_message", "group": "checkins",
                      "args": {"to": "patient", "channel": "sms", "body": checkin_body(p, h, med_names, parsed.get("red_flags", []))},
                      "due_offset_h": h, "rationale": "Most post-discharge problems appear in the first 72 hours."})
    steps.append({"title": "Closing message", "tool": "send_message", "group": "checkins",
                  "args": {"to": "patient", "channel": "sms", "body": (
                      f"Hi {_first(p.name)}, your 96-hour check-ins are complete. Keep your follow-up appointment and "
                      f"call {settings.emergency_number} in an emergency. Wishing you a smooth recovery!")},
                  "due_offset_h": 95.5, "rationale": "Close the loop and remind about the appointment."})
    steps.append({"title": "96-hour summary for the doctor", "tool": "generate_summary", "args": {},
                  "due_offset_h": 96, "rationale": "Hand the outcome back to the discharging team."})
    goal = f"Get {p.name} safely through the first 96 hours after {p.diagnosis or 'discharge'}."
    return {"goal": goal, "rationale": f"Risk {p.risk_level}: {', '.join(p.risk_reasons) or 'no major factors'}.", "steps": steps}


def _valid(plan: dict) -> bool:
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return False
    for s in steps:
        if s.get("tool") not in REGISTRY or not isinstance(s.get("args", {}), dict):
            return False
        try:
            if not 0 <= float(s.get("due_offset_h", 0)) <= 96:
                return False
        except (TypeError, ValueError):
            return False
        if s["tool"] == "send_message" and not s.get("args", {}).get("body"):
            return False
    return True


def build_plan(session: Session, p: Patient, parsed: dict) -> Plan:
    meds = session.exec(select(Medication).where(Medication.patient_id == p.id)).all()
    facts = recall(session, p.id, "planning")
    llm = get_llm()
    plan_data, source = None, "rules"
    if llm.live:
        user = (
            f"Patient: {p.name}, {p.age}y {p.sex}, language {p.language}, risk {p.risk_level}\n"
            f"Caregiver: {p.caregiver_name or 'none'}\nMemory:\n{facts_as_text(facts)}\n"
            f"Parsed discharge data:\n{json.dumps(parsed, indent=1)}"
        )
        out = llm.complete_json(PLANNER_SYSTEM.format(tools=tool_catalog()), user)
        if out and _valid(out):
            plan_data, source = out, llm.name
        else:
            log_event(session, "llm", "LLM plan invalid or unavailable — using rule-based planner", p.id)
    if plan_data is None:
        plan_data = _rules_plan(p, parsed, list(meds))
    if not any(s["tool"] == "generate_summary" for s in plan_data["steps"]):
        plan_data["steps"].append({"title": "96-hour summary for the doctor", "tool": "generate_summary", "args": {}, "due_offset_h": 96})

    # Supersede any previous plan's unfinished steps
    for old in session.exec(select(Plan).where(Plan.patient_id == p.id, Plan.status == "active")).all():
        old.status = "superseded"
        session.add(old)
        for st in session.exec(select(Step).where(Step.plan_id == old.id, Step.status.in_(["pending", "awaiting_approval"]))).all():
            st.status = "skipped"
            session.add(st)
    version = len(session.exec(select(Plan).where(Plan.patient_id == p.id)).all()) + 1

    plan = Plan(patient_id=p.id, version=version, goal=plan_data.get("goal", ""), rationale=plan_data.get("rationale", ""), source=source)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    ordered = sorted(plan_data["steps"], key=lambda s: float(s.get("due_offset_h", 0)))
    for i, s in enumerate(ordered, 1):
        tool = REGISTRY[s["tool"]]
        session.add(Step(
            plan_id=plan.id, patient_id=p.id, seq=i, title=s.get("title", s["tool"]), tool=s["tool"],
            args=s.get("args", {}), due_offset_h=float(s.get("due_offset_h", 0)), group=s.get("group", ""),
            irreversible=tool.irreversible, rationale=s.get("rationale", ""),
        ))
    session.commit()
    log_event(session, "plan", f"Plan v{version} created with {len(ordered)} steps ({source})", p.id,
              {"goal": plan.goal, "steps": [s.get("title") for s in ordered]})
    return plan
