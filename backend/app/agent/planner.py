"""Planning (Agent B): a structured 0-96 h plan.

The LLM (if configured) may PROPOSE a plan; it is used only if `validate_plan` accepts it.
Otherwise — or with no LLM — the deterministic rule plan is used, and the fallback is recorded
in plan.validation and as a `plan.fallback_used` audit event.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, ValidationError
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


AI_ADDABLE = {"open_checkin", "staff_review"}  # existing internal tools only; never messages or bookings
MAX_AI_STEPS = 3

PLANNER_SYSTEM = """You help plan the first 96 hours after hospital discharge for a care-coordination prototype.
A deterministic RULE PLAN already exists (medication review, welcome message, follow-up bookings,
check-ins, reminders, closing message, 96-hour summary). Do NOT repeat it. You may only ADD 0-3
condition-specific steps that the rule plan misses for THIS patient's diagnosis, each with a short
clinical rationale. Allowed action types and argument shapes (arguments_json is a JSON object string):
- open_checkin {"template_id": "<the given checkin_template_id>", "title": "...", "available_hours": 1-48}
    an extra guided check-in at a condition-relevant hour (e.g. a day-2 weight/fluid check for heart failure)
- staff_review {"kind": "care_task", "title": "...", "summary": "what the nurse/doctor should do and why",
                "requires_role": "any" or "doctor"}
    a task for the care team (e.g. call to confirm the daily weight log, check a wound photo)
offset_hours is hours after discharge (0-96). depends_on may only name existing step keys (or be empty).
Never add medicines, doses, diagnoses or patient advice; never message, book or alert anyone (those come
from the rule plan and hospital policy). Approval requirements are decided by policy, not by you.
Use lowercase step keys with underscores. Return an empty steps list if nothing is needed."""


def llm_plan(session: Session, episode: DischargeEpisode, base: Optional[PlanSpec] = None,
             result_out: Optional[list] = None) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    p = session.get(Patient, episode.patient_id)
    facts = active_facts(session, episode.id)
    diag = " ".join(fact_text(f) for f in facts if f.category == "diagnosis" and not f.is_missing)
    user = json.dumps({
        "demo_priority": episode.risk_category, "priority_reasons": episode.risk_reasons,
        "caregiver_authorised": caregiver_ok(p), "checkin_template_id": ct.choose_template(diag),
        "facts": [{"category": f.category, "text": fact_text(f), "missing": f.is_missing} for f in facts
                  if f.category in ("diagnosis", "medication", "follow_up", "red_flag", "allergy", "social")],
        "rule_plan": [{"step_key": s.step_key, "action_type": s.action_type, "offset_hours": s.offset_hours,
                       "title": s.title} for s in (base.steps if base else [])],
    }, indent=1)
    res = llm.structured(PLANNER_SYSTEM, user, LLMPlan, purpose="planning")
    if result_out is not None:
        result_out.append(res)
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
                      "rationale": s.rationale})
    return {"rationale": res.data.rationale, "steps": steps}, None


def vet_ai_additions(raw_steps: list[dict[str, Any]], base: PlanSpec, *, template_id: str,
                     caregiver_contact_ok: bool, patient_phone_ok: bool) -> tuple[list[StepSpec], list[str]]:
    """Validate EVERY AI-proposed addition on its own; invalid ones are dropped with a reason.
    Checks: tool exists and is addable, due 0-96 h, arguments valid for the tool, rationale given."""
    from ..policy.approval import ALLOWED_ACTIONS
    from .plan_validation import ARG_SCHEMAS

    keys = {s.step_key for s in base.steps}
    accepted: list[StepSpec] = []
    dropped: list[str] = []
    for raw in raw_steps:
        name = str(raw.get("step_key") or raw.get("title") or "?")[:60]
        at = raw.get("action_type")
        if len(accepted) >= MAX_AI_STEPS:
            dropped.append(f"AI step '{name}': dropped (max {MAX_AI_STEPS} additions)")
            continue
        if at not in ALLOWED_ACTIONS:
            dropped.append(f"AI step '{name}': unknown tool '{at}'")
            continue
        if at not in AI_ADDABLE:
            dropped.append(f"AI step '{name}': tool '{at}' is not allowed for AI additions")
            continue
        try:
            off = float(raw.get("offset_hours"))
        except (TypeError, ValueError):
            off = -1
        if not 0 <= off <= 96:
            dropped.append(f"AI step '{name}': due T+{raw.get('offset_hours')} h is outside 0-96 h")
            continue
        args = dict(raw.get("arguments") or {})
        if at == "open_checkin":
            args.setdefault("template_id", template_id)
            if args.get("template_id") != template_id:
                dropped.append(f"AI step '{name}': check-in template must be {template_id}")
                continue
        if at == "staff_review":
            args["kind"] = "care_task"
        try:
            args = ARG_SCHEMAS[at].model_validate(args).model_dump()
        except ValidationError as e:
            dropped.append(f"AI step '{name}': invalid arguments ({e.errors()[0]['msg']})")
            continue
        rationale = str(raw.get("rationale") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not rationale or len(title) < 2:
            dropped.append(f"AI step '{name}': missing title or rationale")
            continue
        key = "ai_" + slug(str(raw.get("step_key") or title))[:50]
        while key in keys:
            key += "_x"
        try:
            spec = StepSpec(step_key=key, title=title[:160], purpose=str(raw.get("purpose") or "")[:500],
                            action_type=at, arguments=args, offset_hours=round(off, 2),
                            depends_on=[d for d in (raw.get("depends_on") or []) if d in keys],
                            rationale=f"AI-suggested: {rationale}"[:1000], origin="ai")
        except ValidationError as e:
            dropped.append(f"AI step '{name}': {e.errors()[0]['msg']}")
            continue
        # the whole plan must still validate with this step in it
        ok, errs = validate_plan(PlanSpec(rationale="", steps=list(base.steps) + accepted + [spec]),
                                 caregiver_contact_ok=caregiver_contact_ok, patient_phone_ok=patient_phone_ok)
        if ok is None:
            dropped.append(f"AI step '{name}': " + "; ".join(errs)[:200])
            continue
        keys.add(key)
        accepted.append(spec)
    return accepted, dropped


def build_initial_spec(session: Session, episode: DischargeEpisode) -> tuple[PlanSpec, str, dict[str, Any]]:
    """Returns (spec, source, validation). Must be called BEFORE writing (LLM network call).
    Hybrid: the rule plan is always the base; the AI may only ADD validated condition-specific steps."""
    from ..llm import router

    p = session.get(Patient, episode.patient_id)
    validation: dict[str, Any] = {"llm_attempted": False, "llm_valid": None, "fallback_used": False, "errors": [],
                                  "ai_added": [], "ai_dropped": []}
    base = rule_plan(session, episode)
    checked, errors = validate_plan(base, caregiver_contact_ok=caregiver_ok(p), patient_phone_ok=bool(p.phone_e164))
    if checked is None:  # should never happen; surface loudly
        raise RuntimeError(f"rule plan failed validation: {errors}")
    facts = active_facts(session, episode.id, ["diagnosis"])
    diag = " ".join(fact_text(f) for f in facts if not f.is_missing)
    route = router.decide("planning", {"diagnosis": diag})
    validation["route"] = {"use_llm": route.use, "reason": route.reason}
    if not route.use:
        validation["ai_tag"] = router.tag(route)
        return checked, "rules", validation
    validation["llm_attempted"] = True
    session.commit()  # A-08: no write lock is held during the LLM call
    out: list = []
    raw, err = llm_plan(session, episode, checked, result_out=out)
    res = out[0] if out else None
    validation["ai_tag"] = router.tag(route, res)
    validation["ai_ms"] = res.ms if res else 0
    if raw is None:
        validation.update(fallback_used=True, llm_valid=False, errors=[f"LLM unavailable: {err}"])
        return checked, "rules_fallback", validation
    accepted, dropped = vet_ai_additions(raw.get("steps", []), checked, template_id=ct.choose_template(diag),
                                         caregiver_contact_ok=caregiver_ok(p), patient_phone_ok=bool(p.phone_e164))
    validation["ai_added"] = [s.step_key for s in accepted]
    validation["ai_dropped"] = dropped
    validation["errors"] = dropped
    if not accepted:
        validation.update(llm_valid=not dropped, fallback_used=bool(dropped))
        return checked, ("rules_fallback" if dropped else "rules"), validation
    validation["llm_valid"] = True
    spec = PlanSpec(rationale=checked.rationale + f" AI added {len(accepted)} condition-specific step(s): "
                    + "; ".join(s.title for s in accepted) + ".", steps=list(checked.steps) + accepted)
    return spec, f"rules+llm:{res.provider if res else llm.provider_name()}", validation


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
    if validation.get("route"):
        from ..llm import router

        r = validation["route"]
        tagged = validation.get("ai_tag") or {}
        route = router.Route("planning", r["use_llm"], r["reason"])
        res = None
        if route.use:
            ok = tagged.get("by") == "ai"
            res = llm.LLMResult(ok, llm.provider_name(), tagged.get("model") or llm.model_name(),
                                error=None if ok else (tagged.get("reason") or "no usable output"),
                                ms=validation.get("ai_ms") or 0)
        router.trace(session, episode.id, route, res,
                     note=(f"added: {', '.join(validation.get('ai_added') or []) or 'none'}; "
                           f"dropped: {len(validation.get('ai_dropped') or [])}"), scopes=["plan"])
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
