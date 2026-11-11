"""Step 3 of the agent: execute due steps, gating irreversible actions behind
human approval, and folding tool results back into the plan."""
import logging
import threading
from datetime import timedelta

from sqlmodel import Session, select

from ..clock import clock, hours_since
from ..db import engine
from ..events import log_event
from ..memory import remember
from ..models import Approval, Medication, Patient, Plan, Step
from ..tools import REGISTRY, ToolContext
from .parser import parse_discharge
from .planner import build_plan

log = logging.getLogger("ninety_six.executor")
_lock = threading.RLock()  # one agent loop at a time (SQLite-friendly)


def add_step(session: Session, patient: Patient, spec: dict, origin: str, due_offset_h: float | None = None) -> Step:
    plan = session.exec(select(Plan).where(Plan.patient_id == patient.id, Plan.status == "active")).first()
    tool = REGISTRY[spec["tool"]]
    count = len(session.exec(select(Step).where(Step.plan_id == plan.id)).all())
    step = Step(
        plan_id=plan.id, patient_id=patient.id, seq=count + 1, title=spec["title"], tool=spec["tool"],
        args=spec.get("args", {}), group=spec.get("group", ""), origin=origin,
        due_offset_h=round(hours_since(patient.discharge_at), 2) if due_offset_h is None else due_offset_h,
        irreversible=tool.irreversible, preapproved=spec.get("preapproved", False), rationale=spec.get("rationale", ""),
    )
    session.add(step)
    session.commit()
    session.refresh(step)
    log_event(session, "plan", f"Re-planned: added “{step.title}”", patient.id, {"origin": origin, "tool": step.tool})
    return step


def run_step(session: Session, step: Step, priority: str = "normal") -> Step:
    patient = session.get(Patient, step.patient_id)
    tool = REGISTRY.get(step.tool)
    if tool is None:
        step.status, step.result = "failed", {"error": f"Unknown tool {step.tool}"}
        session.add(step)
        session.commit()
        return step
    ctx = ToolContext(session=session, patient=patient, step=step)

    # Human-in-the-loop gate
    if tool.irreversible and not step.preapproved and step.status != "approved":
        if step.status != "awaiting_approval":
            step.status = "awaiting_approval"
            session.add(step)
            approval = Approval(
                patient_id=patient.id, step_id=step.id, title=step.title, tool=step.tool,
                summary=tool.preview(ctx, step.args), payload=dict(step.args),
                priority=priority if priority == "urgent" else step.args.get("urgency", "normal"),
            )
            session.add(approval)
            session.commit()
            log_event(session, "approval", f"Waiting for approval: {step.title}", patient.id,
                      {"step_id": step.id, "priority": approval.priority})
        return step

    step.status = "running"
    session.add(step)
    session.commit()
    log_event(session, "tool", f"Calling {step.tool} — {step.title}", patient.id, {"args": step.args})
    try:
        result = tool.run(ctx, dict(step.args))
        step.status, step.result = "done", {k: v for k, v in result.items() if k != "followups"}
        followups = result.get("followups", [])
    except Exception as exc:
        log.exception("Tool %s failed", step.tool)
        step.status, step.result, followups = "failed", {"error": str(exc)}, []
    step.executed_at = clock.now()
    session.add(step)
    session.commit()
    log_event(session, "tool", f"{step.tool} {step.status}: {step.title}", patient.id, {"result": step.result})

    for spec in followups:  # tool results can change the plan
        new = add_step(session, patient, spec, origin="followup")
        run_step(session, new)
    return step


def run_due_steps(patient_id: int | None = None) -> int:
    """Run every step whose time has come. Called by the monitor tick."""
    ran = 0
    with _lock, Session(engine) as session:
        q = select(Step).where(Step.status.in_(["pending", "approved"]))
        if patient_id:
            q = q.where(Step.patient_id == patient_id)
        for step in session.exec(q.order_by(Step.due_offset_h, Step.seq)).all():
            patient = session.get(Patient, step.patient_id)
            if patient.status == "planning":
                continue
            if clock.now() >= patient.discharge_at + timedelta(hours=step.due_offset_h):
                before = step.status
                run_step(session, step)
                ran += int(step.status != before)
    return ran


def kickoff(patient_id: int) -> None:
    """Parse → remember → plan → execute hour-0 steps."""
    with _lock, Session(engine) as session:
        p = session.get(Patient, patient_id)
        log_event(session, "system", "Agent started: reading discharge summary", p.id)
        parsed, source = parse_discharge(p.summary_text, p.age)
        log_event(session, "tool", f"Parsed discharge summary ({source})", p.id,
                  {"medications": len(parsed.get("medications", [])), "follow_up": len(parsed.get("follow_up", []))})

        p.diagnosis = p.diagnosis or parsed.get("diagnosis", "")
        p.allergies = parsed.get("allergies", "") or p.allergies
        p.risk_level, p.risk_reasons = parsed.get("risk_level", "medium"), parsed.get("risk_reasons", [])
        session.add(p)
        for m in parsed.get("medications", []):
            session.add(Medication(patient_id=p.id, name=m.get("name", "?"), dose=m.get("dose", ""),
                                   frequency=m.get("frequency", ""), instructions=m.get("instructions", "")))
        session.commit()

        remember(session, p.id, "diagnosis", p.diagnosis, "parser")
        if p.allergies:
            remember(session, p.id, "allergies", p.allergies, "parser")
        if parsed.get("red_flags"):
            remember(session, p.id, "red_flags", "; ".join(parsed["red_flags"]), "parser")
        if parsed.get("social"):
            remember(session, p.id, "social", parsed["social"], "parser")
        remember(session, p.id, "risk", f"{p.risk_level} — {', '.join(p.risk_reasons) or 'no major factors'}", "parser")
        remember(session, p.id, "language", p.language, "intake")

        build_plan(session, p, parsed)
        p.status = "active"
        session.add(p)
        session.commit()
    run_due_steps(patient_id)


def replan(patient_id: int) -> None:
    with _lock, Session(engine) as session:
        p = session.get(Patient, patient_id)
        parsed, _ = parse_discharge(p.summary_text, p.age)
        build_plan(session, p, parsed)
    run_due_steps(patient_id)


def decide(session: Session, approval: Approval, decision: str, note: str = "", edited: dict | None = None) -> Approval:
    with _lock:
        step = session.get(Step, approval.step_id)
        approval.status = "approved" if decision == "approve" else "rejected"
        approval.note, approval.decided_at = note, clock.now()
        if edited:
            approval.payload = {**approval.payload, **edited}
            step.args = {**step.args, **edited}
        step.status = "approved" if decision == "approve" else "rejected"
        session.add(approval)
        session.add(step)
        session.commit()
        log_event(session, "approval", f"Clinician {approval.status}: {approval.title}", approval.patient_id,
                  {"note": note, "edited": bool(edited)})
        if step.status == "approved":
            run_step(session, step)
        elif step.tool == "activate_schedule":
            remember(session, approval.patient_id, "clinician_preference",
                     "Declined blanket check-in approval — ask before each check-in", "clinician")
        session.refresh(approval)
        return approval
