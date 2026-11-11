"""Step 4 of the agent: watch for patient replies and the passage of time.
Replies are triaged against the patient's memory and can re-plan the episode."""
import re

from sqlmodel import Session, select

from ..config import settings
from ..events import log_event
from ..llm import get_llm
from ..memory import facts_as_text, recall, remember
from ..models import Message, Patient, Plan, Step
from .executor import _lock, add_step, run_step
from .prompts import TRIAGE_SYSTEM

GENERAL_RED = [
    "chest pain", "chest pressure", "can't breathe", "cannot breathe", "breathless", "short of breath", "shortness of breath",
    "faint", "fainted", "unconscious", "confused", "confusion", "bleeding", "blood in", "vomiting blood", "seizure",
    "slurred", "weakness on one side", "high fever", "severe pain", "swelling",
]
GENERAL_CONCERN = [
    "ran out", "run out", "missed", "forgot", "can't afford", "cannot afford", "side effect", "dizzy", "nausea",
    "headache", "not sure", "confused about", "which tablet", "rash", "itch", "constipat", "can't sleep", "tired",
]
OK_WORDS = ["fine", "good", "better", "ok", "okay", "all good", "great", "1"]


def _phrases(red_flags: str) -> list[str]:
    out = []
    for item in red_flags.split(";"):
        for part in re.split(r",| or |/", item.lower()):
            words = [w for w in re.findall(r"[a-z]+", part) if len(w) > 3 and w not in {"more", "than", "when", "with", "above", "rest"}]
            if words:
                out.append(" ".join(words[:2]))
    return out


def _rules_triage(text: str, facts) -> dict:
    t = text.lower()
    rf = next((f.value for f in facts if f.key == "red_flags"), "")
    specific = [ph for ph in _phrases(rf) if all(w in t for w in ph.split())]
    general = [w for w in GENERAL_RED if w in t]
    if specific or general:
        hits = specific or general
        return {"level": "red_flag", "reason": f"Matches warning sign: {', '.join(hits)}", "symptoms": hits,
                "reply": (f"Thank you for telling us. This can be serious — I'm alerting your care team now. "
                          f"If it gets worse or you feel unsafe, call {settings.emergency_number} immediately.")}
    concerns = [w for w in GENERAL_CONCERN if w in t]
    if concerns:
        return {"level": "concern", "reason": f"Needs help: {', '.join(concerns)}", "symptoms": concerns,
                "reply": "Thanks for letting us know. I've noted this and a nurse will review it shortly. "
                         "Please don't stop or double any medicine on your own."}
    return {"level": "ok", "reason": "Recovering as expected", "symptoms": [],
            "reply": "Great to hear — keep taking your medicines as scheduled. I'll check in again soon."}


def triage(session: Session, patient: Patient, text: str) -> tuple[dict, str]:
    facts = recall(session, patient.id, "triage")
    llm = get_llm()
    if llm.live:
        out = llm.complete_json(TRIAGE_SYSTEM, f"Patient memory:\n{facts_as_text(facts)}\n\nPatient reply: “{text}”")
        if out and out.get("level") in ("ok", "concern", "red_flag") and out.get("reply"):
            # Safety net: rules can only escalate the LLM's verdict, never downgrade it
            rules = _rules_triage(text, facts)
            rank = {"ok": 0, "concern": 1, "red_flag": 2}
            if rank[rules["level"]] > rank[out["level"]]:
                return rules, "rules (safety override)"
            return out, llm.name
    return _rules_triage(text, facts), "rules"


def _schedule_active(session: Session, patient_id: int) -> bool:
    plan = session.exec(select(Plan).where(Plan.patient_id == patient_id, Plan.status == "active")).first()
    if not plan:
        return False
    return session.exec(
        select(Step).where(Step.plan_id == plan.id, Step.tool == "activate_schedule", Step.status == "done")
    ).first() is not None


def handle_reply(session: Session, patient_id: int, text: str) -> dict:
    with _lock:
        p = session.get(Patient, patient_id)
        msg = Message(patient_id=p.id, direction="in", recipient="patient", channel="sms", body=text, delivery="live")
        session.add(msg)
        session.commit()
        log_event(session, "message", "Patient replied", p.id, {"body": text})

        verdict, source = triage(session, p, text)
        msg.triage = verdict["level"]
        session.add(msg)
        session.commit()
        log_event(session, "triage", f"Triage: {verdict['level'].upper()} — {verdict['reason']} ({source})", p.id, verdict)
        if verdict["level"] != "ok":
            remember(session, p.id, f"reported_{verdict['level']}", text[:200], "triage")

        reply_spec = {"title": "Reply to patient", "tool": "send_message",
                      "args": {"to": "patient", "channel": "sms", "body": verdict["reply"]}}
        if verdict["level"] == "red_flag":
            esc = add_step(session, p, {
                "title": "URGENT: alert care team", "tool": "escalate_to_care_team",
                "args": {"urgency": "urgent", "reason": verdict["reason"], "details": f"Patient said: “{text}”"},
                "rationale": "Red-flag symptom reported — a clinician must act today."}, origin="replan")
            run_step(session, esc, priority="urgent")
            reply = add_step(session, p, {**reply_spec, "title": "Send safety-net advice",
                                          "rationale": "Tell the patient what to do while the team responds."}, origin="replan")
            run_step(session, reply, priority="urgent")
        elif verdict["level"] == "concern":
            reply = add_step(session, p, {**reply_spec, "rationale": "Agent-drafted reply to a concern — review wording."}, origin="replan")
            run_step(session, reply)
            if p.risk_level == "high" and p.caregiver_phone:
                cg = add_step(session, p, {"title": f"Update caregiver {p.caregiver_name}", "tool": "send_message",
                                           "args": {"to": "caregiver", "channel": "sms",
                                                    "body": f"Hello {p.caregiver_name}, {p.name} reported: “{text[:120]}”. Could you check on them today?"},
                                           "rationale": "High-risk patient with a new concern — involve the caregiver."}, origin="replan")
                run_step(session, cg)
        else:
            reply = add_step(session, p, {**reply_spec, "group": "checkins",
                                          "preapproved": _schedule_active(session, p.id),
                                          "rationale": "Routine acknowledgement covered by the check-in sign-off."}, origin="replan")
            run_step(session, reply)
        return {"triage": verdict, "source": source}
