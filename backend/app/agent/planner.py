"""Planning (Agent B): a structured 0-96 h plan.

The LLM (if configured) may PROPOSE a plan; it is used only if `validate_plan` accepts it.
Otherwise — or with no LLM — the deterministic rule plan is used, and the fallback is recorded
in plan.validation and as a `plan.fallback_used` audit event.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import llm
from ..config import settings
from ..events import record_event
from ..models import DischargeEpisode, Patient, PlanStatus, PlanVersion, User
from ..tools.registry import get_adapter
from . import checkin_templates as ct
from .memory import active_facts, fact_text, log_usage
from .plan_store import apply_new_version, current_plan, slug
from .plan_validation import PlanSpec, StepSpec, validate_plan

REMINDER_FRESHNESS_H = 6.0


def checkin_hours(risk: Optional[str]) -> list[int]:
    return {"high": [12, 24, 48, 72], "medium": [24, 48, 72]}.get(risk or "", [24, 72])


def caregiver_ok(p: Patient) -> bool:
    return bool(p.caregiver_authorised and p.caregiver_phone_e164)


def rule_plan(session: Session, episode: DischargeEpisode) -> PlanSpec:
    p = session.get(Patient, episode.patient_id)
    facts = active_facts(session, episode.id)
    diag = " ".join(fact_text(f) for f in facts if f.category == "diagnosis" and not f.is_missing)
    meds = [f for f in facts if f.category == "medication" and not f.is_missing]
    flags = [f for f in facts if f.category == "red_flag" and not f.is_missing]
    template_id = ct.choose_template(diag)
    risk = episode.risk_category or "medium"
    steps: list[StepSpec] = [
        StepSpec(step_key="med_review", title="Medication information review", action_type="medication_review",
                 purpose="Look up label warnings (openFDA) and check local interaction rules for the discharge medicines.",
                 arguments={"medications": [(f.value or {}).get("name", f.label) for f in meds]}, offset_hours=0,
                 rationale="Findings are routed to the doctor before medicine details are sent to the patient."),
        StepSpec(step_key="welcome_message", title="Welcome + reviewed medicine list", action_type="send_message",
                 purpose="Give the patient their doctor-reviewed medicines, warning signs and how check-ins work.",
                 arguments={"recipient_role": "patient", "template_id": "welcome_v1", "channel": settings.twilio_channel,
                            "supporting_fact_ids": [f.id for f in meds + flags]},
                 offset_hours=0, depends_on=["med_review"],
                 rationale="Written instructions reduce dosing errors after discharge; content comes only from confirmed facts."),
    ]
    for f in facts:
        if f.category != "follow_up" or f.is_missing:
            continue
        v = f.value or {}
        steps.append(StepSpec(
            step_key=f"book_{slug(v.get('title') or f.label)}", title=f"Calendar event: {v.get('title') or f.label}",
            action_type="create_calendar_event",
            purpose="Create a calendar event for the follow-up written in the discharge summary.",
            arguments={"title": v.get("title") or f.label, "provider_name": v.get("provider"),
                       "location": None, "within_days": v.get("within_days"), "source_fact_id": f.id},
            offset_hours=0,
            rationale=f"Discharge summary asks for this follow-up{' within ' + str(v['within_days']) + ' days' if v.get('within_days') else ''}. "
                      "This creates a calendar event only; the hospital must confirm the appointment.",
        ))
    if risk == "high" and caregiver_ok(p):
        steps.append(StepSpec(
            step_key="caregiver_brief", title=f"Brief authorised contact ({p.caregiver_name})", action_type="send_message",
            purpose="Tell the authorised caregiver how follow-up works and which warning signs to watch for.",
            arguments={"recipient_role": "caregiver", "template_id": "caregiver_brief_v1", "channel": settings.twilio_channel,
                       "supporting_fact_ids": [f.id for f in flags]},
            offset_hours=0, depends_on=["med_review"],
            rationale="High demo-priority patient with an authorised contact: a second person watching for warning signs.",
        ))
    hours = checkin_hours(risk)
    for i, h in enumerate(hours):
        nxt = hours[i + 1] if i + 1 < len(hours) else 96
        steps.append(StepSpec(
            step_key=f"checkin_{h}h", title=f"{h}-hour check-in", action_type="open_checkin",
            purpose="Open the doctor-reviewed guided check-in on the patient website.",
            arguments={"template_id": template_id, "title": f"{h}-hour check-in", "available_hours": float(min(24, nxt - h))},
            offset_hours=h,
            rationale="Most early post-discharge problems appear in the first 72 hours"
                      + (" (high demo priority: more frequent check-ins)." if risk == "high" else "."),
        ))
        steps.append(StepSpec(
            step_key=f"reminder_{h}h", title=f"{h}-hour check-in reminder", action_type="send_message",
            purpose="Non-clinical text telling the patient their check-in is ready.",
            arguments={"recipient_role": "patient", "template_id": "checkin_reminder_v1", "channel": settings.twilio_channel,
                       "checkin_title": f"{h}-hour check-in"},
            offset_hours=h, depends_on=[f"checkin_{h}h"], freshness_hours=REMINDER_FRESHNESS_H,
            rationale="Routine reminder; can be covered by the doctor's routine authorisation. Skipped if stale.",
        ))
    steps.append(StepSpec(
        step_key="closing_message", title="Monitoring-period closing message", action_type="send_message",
        purpose="Tell the patient check-ins have ended and that this is not a medical clearance.",
        arguments={"recipient_role": "patient", "template_id": "closing_v1", "channel": settings.twilio_channel},
        offset_hours=95.5, freshness_hours=REMINDER_FRESHNESS_H,
        rationale="Close the loop without implying clearance.",
    ))
    steps.append(StepSpec(
        step_key="summary_96h", title="96-hour episode summary", action_type="generate_summary",
        purpose="Summarise outcomes, missed tasks and unresolved concerns for the closure review.",
        arguments={}, offset_hours=96, rationale="Hand the episode back to the doctor for closure review.",
    ))
    return PlanSpec(
        rationale=f"Rule-based plan. Demo priority {risk}: check-ins at {', '.join(f'T+{h} h' for h in hours)}.",
        steps=steps,
    )


# ----------------------------------------------------------------------------- LLM proposal
class _LLMStep(BaseModel):
    step_key: str
    title: str
    purpose: str
    action_type: str
    arguments_json: str
    offset_hours: float
    depends_on: list[str]
    rationale: str


class LLMPlan(BaseModel):
    rationale: str
    steps: list[_LLMStep]


PLANNER_SYSTEM = """You plan the first 96 hours after hospital discharge for a care-coordination prototype.
You may ONLY use these action types and argument shapes (arguments_json is a JSON object string):
- medication_review {"medications": [names]}                         (required, T+0)
- send_message {"recipient_role": "patient"|"caregiver", "template_id": one of
  welcome_v1, checkin_reminder_v1, closing_v1 (patient) | caregiver_brief_v1 (caregiver, only if authorised),
  "channel": "sms", "checkin_title": optional}                       (welcome_v1 must depend on the medication_review step)
- create_calendar_event {"title", "provider_name", "within_days"}   (one per follow-up in the summary)
- open_checkin {"template_id": "<given>", "title", "available_hours"}
- generate_summary {}                                                (required, T+96)
Offsets are hours after discharge (0-96). Never add medicines, doses, diagnoses or clinical advice;
message content is filled from doctor-confirmed facts by templates. Approval requirements are decided by
the hospital's policy, not by you. Use lowercase step keys with underscores."""


def llm_plan(session: Session, episode: DischargeEpisode) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    p = session.get(Patient, episode.patient_id)
    facts = active_facts(session, episode.id)
    diag = " ".join(fact_text(f) for f in facts if f.category == "diagnosis" and not f.is_missing)
    user = json.dumps({
        "demo_priority": episode.risk_category, "priority_reasons": episode.risk_reasons,
        "caregiver_authorised": caregiver_ok(p), "checkin_template_id": ct.choose_template(diag),
        "facts": [{"category": f.category, "text": fact_text(f), "missing": f.is_missing} for f in facts
                  if f.category in ("diagnosis", "medication", "follow_up", "red_flag", "allergy", "social")],
    }, indent=1)
    res = llm.structured(PLANNER_SYSTEM, user, LLMPlan, purpose="planning")
    if not res.ok or res.data is None:
        return None, res.error
    steps = []
    for s in res.data.steps:
        try:
            args = json.loads(s.arguments_json or "{}")
        except json.JSONDecodeError:
            args = {"__invalid_json__": s.arguments_json[:80]}
        steps.append({"step_key": s.step_key, "title": s.title, "purpose": s.purpose, "action_type": s.action_type,
                      "arguments": args, "offset_hours": s.offset_hours, "depends_on": s.depends_on,
                      "rationale": s.rationale,
                      "freshness_hours": REMINDER_FRESHNESS_H if args.get("template_id") in ("checkin_reminder_v1", "closing_v1") else None})
    return {"rationale": res.data.rationale, "steps": steps}, None


def build_initial_spec(session: Session, episode: DischargeEpisode) -> tuple[PlanSpec, str, dict[str, Any]]:
    """Returns (spec, source, validation). Must be called BEFORE writing (LLM network call)."""
    p = session.get(Patient, episode.patient_id)
    validation: dict[str, Any] = {"llm_attempted": False, "llm_valid": None, "fallback_used": False, "errors": []}
    if llm.is_configured():
        validation["llm_attempted"] = True
        raw, err = llm_plan(session, episode)
        if raw is not None:
            spec, errors = validate_plan(raw, caregiver_contact_ok=caregiver_ok(p), patient_phone_ok=bool(p.phone_e164))
            if spec is not None:
                validation["llm_valid"] = True
                return spec, f"llm:{llm.provider_name()}", validation
            validation["llm_valid"] = False
            validation["errors"] = errors
        else:
            validation["errors"] = [f"LLM unavailable: {err}"]
        validation["fallback_used"] = True
    spec = rule_plan(session, episode)
    checked, errors = validate_plan(spec, caregiver_contact_ok=caregiver_ok(p), patient_phone_ok=bool(p.phone_e164))
    if checked is None:  # should never happen; surface loudly
        raise RuntimeError(f"rule plan failed validation: {errors}")
    return checked, ("rules_fallback" if validation["fallback_used"] else "rules"), validation


def generate_draft_plan(
    session: Session, episode: DischargeEpisode, *, actor: Optional[User], trigger: str = "initial",
    reason: Optional[str] = None, prebuilt: Optional[tuple[PlanSpec, str, dict]] = None,
) -> PlanVersion:
    spec, source, validation = prebuilt or build_initial_spec(session, episode)
    base = current_plan(session, episode)
    if base is not None and base.status != PlanStatus.DRAFT:
        base = None  # never re-draft over an active plan here
    facts = active_facts(session, episode.id)
    pv = apply_new_version(
        session, episode, list(spec.steps), status=PlanStatus.DRAFT, source=source, trigger=trigger,
        trigger_ref=reason, rationale=spec.rationale, validation=validation,
        created_by=f"user:{actor.id}" if actor else "agent", facts_used=[f.id for f in facts], base=base,
        actor_type="agent", actor_label="Ninety-Six agent",
    )
    log_usage(session, episode.id, facts, used_for="planning", ref_type="plan_version", ref_id=pv.id,
              note=f"plan v{pv.version} drafted")
    if validation.get("fallback_used"):
        record_event(
            session, "plan.fallback_used",
            "LLM plan rejected or unavailable - deterministic rule plan used",
            episode_id=episode.id, actor_type="agent", actor_label="Ninety-Six agent",
            detail={"errors": validation.get("errors", []), "plan_version_id": pv.id},
            scopes=["plan", "activity"],
        )
    return pv


def medication_names(session: Session, episode: DischargeEpisode) -> list[str]:
    return [(f.value or {}).get("name", f.label) for f in active_facts(session, episode.id, ["medication"]) if not f.is_missing]


__all__ = ["rule_plan", "build_initial_spec", "generate_draft_plan", "checkin_hours", "get_adapter"]
