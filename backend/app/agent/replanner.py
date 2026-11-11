"""Versioned replanning (Agent B). Every replan creates a new PlanVersion with a change summary
explaining what changed and why; plan_store.apply_new_version enforces the carry-over rules."""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import DischargeEpisode, EpisodeStatus, Patient, PlanStatus, PlanVersion, ProposedAction
from .memory import active_facts, log_usage
from .plan_store import apply_new_version, current_plan, fmt_h, offset_now, steps_of, step_to_spec
from .plan_validation import StepSpec, validate_plan
from .planner import caregiver_ok, rule_plan


def _validated(session: Session, episode: DischargeEpisode, specs: list[StepSpec]) -> list[StepSpec]:
    p = session.get(Patient, episode.patient_id)
    checked, errors = validate_plan({"rationale": "", "steps": [s.model_dump() for s in specs]},
                                    caregiver_contact_ok=caregiver_ok(p), patient_phone_ok=bool(p.phone_e164))
    if checked is None:
        raise RuntimeError(f"replanned steps failed validation: {errors}")
    return list(checked.steps)


def replan(
    session: Session,
    episode: DischargeEpisode,
    *,
    trigger: str,
    reason: str,
    add: Optional[list[StepSpec]] = None,
    modify: Optional[Callable[[list[StepSpec]], list[StepSpec]]] = None,
    recompute_initial: bool = False,
    actor_type: str = "agent",
    actor_id: Optional[int] = None,
    actor_label: str = "Ninety-Six agent",
    created_by: str = "agent",
    rationale: Optional[str] = None,
) -> PlanVersion:
    base = current_plan(session, episode)
    if base is None:
        raise RuntimeError("episode has no plan to replan")
    specs = [step_to_spec(s) for s in steps_of(session, base) if s.status != "superseded"]
    if recompute_initial:
        fresh = rule_plan(session, episode).steps
        fresh_keys = {s.step_key for s in fresh}
        kept_terminal = {s.step_key for s in steps_of(session, base) if s.status in
                         ("executed", "failed", "unknown", "skipped", "cancelled")}
        others = [s for s in specs if s.origin != "initial" and s.step_key not in fresh_keys]
        terminal_initial = [s for s in specs if s.origin == "initial" and s.step_key in kept_terminal and s.step_key not in fresh_keys]
        # keep tool-finding gates that still apply (e.g. medication combination review)
        specs = list(fresh) + others + terminal_initial
        for s in specs:
            if s.step_key == "welcome_message" and any(o.step_key == "med_combination_review" for o in others):
                if "med_combination_review" not in s.depends_on:
                    s.depends_on = list(s.depends_on) + ["med_combination_review"]
    if modify:
        specs = modify(specs)
    existing = {s.step_key for s in specs}
    for s in add or []:
        if s.step_key not in existing:
            specs.append(s)
    specs = _validated(session, episode, specs)
    facts = active_facts(session, episode.id)
    status = PlanStatus.ACTIVE if episode.status == EpisodeStatus.ACTIVE else PlanStatus.DRAFT
    pv = apply_new_version(
        session, episode, specs, status=status, source="rules", trigger=trigger, trigger_ref=reason,
        rationale=rationale or reason, validation={"llm_attempted": False, "llm_valid": None, "fallback_used": False, "errors": []},
        created_by=created_by, facts_used=[f.id for f in facts], base=base,
        actor_type=actor_type, actor_id=actor_id, actor_label=actor_label,
    )
    log_usage(session, episode.id, facts, used_for="replanning", ref_type="plan_version", ref_id=pv.id, note=f"{trigger}: {reason}"[:255])
    return pv


def tool_finding_replan(session: Session, episode: DischargeEpisode, *, findings: list[str], fact_ids: list[int]) -> Optional[PlanVersion]:
    base = current_plan(session, episode)
    if base is None or any(s.step_key == "med_combination_review" for s in steps_of(session, base)):
        return None
    review = StepSpec(
        step_key="med_combination_review", title="Doctor review: flagged medicine combination", action_type="staff_review",
        purpose="A doctor confirms the discharge medicines before medicine details are sent to the patient.",
        arguments={"kind": "medication_review", "title": "Review flagged medicine combination",
                   "summary": " ".join(findings), "requires_role": "doctor", "finding_fact_ids": fact_ids},
        offset_hours=0, depends_on=["med_review"], origin="tool_finding",
        rationale="Medication information review flagged a combination for clinician review: " + " ".join(findings),
    )

    def gate_welcome(specs: list[StepSpec]) -> list[StepSpec]:
        for s in specs:
            if s.step_key == "welcome_message" and "med_combination_review" not in s.depends_on:
                s.depends_on = list(s.depends_on) + ["med_combination_review"]
        return specs

    return replan(session, episode, trigger="tool_finding", reason="Medication information flagged a combination",
                  add=[review], modify=gate_welcome,
                  rationale="Medication review found a flagged combination; the welcome message now waits for a doctor review.")


def patient_response_replan(
    session: Session, episode: DischargeEpisode, *, checkin_id: int, level: str, reasons: list[str], fact_ids: list[int]
) -> Optional[PlanVersion]:
    if episode.status != EpisodeStatus.ACTIVE or level == "ok":
        return None
    p = session.get(Patient, episode.patient_id)
    now_h = offset_now(episode)
    adds: list[StepSpec] = []
    why = "; ".join(reasons) or "patient check-in"
    if level == "urgent":
        adds.append(StepSpec(
            step_key=f"urgent_safety_{checkin_id}", title="URGENT: safety message to patient", action_type="send_message",
            purpose="Tell the patient to contact the hospital now / use the emergency number.",
            arguments={"recipient_role": "patient", "template_id": "urgent_safety_v1", "channel": settings.twilio_channel,
                       "priority": "urgent", "checkin_id": checkin_id, "supporting_fact_ids": fact_ids},
            offset_hours=now_h, origin="patient_response",
            rationale=f"Check-in matched discharge red flags ({why}). Needs doctor approval before sending.",
        ))
        adds.append(StepSpec(
            step_key=f"care_team_alert_{checkin_id}", title="URGENT: email alert to care team", action_type="care_team_alert",
            purpose="Email the care team the flagged answers.",
            arguments={"template_id": "care_team_alert_v1", "urgency": "urgent", "checkin_id": checkin_id,
                       "reasons": reasons, "supporting_fact_ids": fact_ids},
            offset_hours=now_h, origin="patient_response",
            rationale="Escalation message: always individually approved.",
        ))
    else:
        adds.append(StepSpec(
            step_key=f"concern_reply_{checkin_id}", title="Reply to patient's concern", action_type="send_message",
            purpose="Acknowledge the concern and say a nurse will contact them.",
            arguments={"recipient_role": "patient", "template_id": "concern_reply_v1", "channel": settings.twilio_channel,
                       "priority": "high", "checkin_id": checkin_id, "supporting_fact_ids": fact_ids},
            offset_hours=now_h, origin="patient_response",
            rationale=f"Check-in raised a concern ({why}). Agent-drafted reply needs doctor approval.",
        ))
        if episode.risk_category == "high":
            extra_h = round(now_h + 6, 2)
            if extra_h <= 90:
                tag = int(round(extra_h))
                from .checkin_templates import choose_template
                from .memory import fact_text

                diag = " ".join(fact_text(f) for f in active_facts(session, episode.id, ["diagnosis"]) if not f.is_missing)
                adds.append(StepSpec(
                    step_key=f"extra_checkin_{checkin_id}", title=f"Extra check-in ({fmt_h(tag)})", action_type="open_checkin",
                    purpose="Additional check-in after a concern (high demo priority).",
                    arguments={"template_id": choose_template(diag), "title": "Extra check-in", "available_hours": 6.0},
                    offset_hours=extra_h, origin="patient_response",
                    rationale="High-priority patient reported a concern: check again sooner.",
                ))
                adds.append(StepSpec(
                    step_key=f"extra_reminder_{checkin_id}", title="Extra check-in reminder", action_type="send_message",
                    purpose="Reminder for the extra check-in.",
                    arguments={"recipient_role": "patient", "template_id": "checkin_reminder_v1",
                               "channel": settings.twilio_channel, "checkin_title": "extra check-in"},
                    offset_hours=extra_h, depends_on=[f"extra_checkin_{checkin_id}"], freshness_hours=6.0,
                    origin="patient_response",
                    rationale="Outside the routine schedule the doctor authorised, so it needs individual approval.",
                ))
    if caregiver_ok(p) and (level == "urgent" or episode.risk_category == "high"):
        adds.append(StepSpec(
            step_key=f"caregiver_update_{checkin_id}", title=f"Ask authorised contact ({p.caregiver_name}) to check in",
            action_type="send_message", purpose="Ask the authorised caregiver to check on the patient today.",
            arguments={"recipient_role": "caregiver", "template_id": "caregiver_update_v1", "channel": settings.twilio_channel,
                       "priority": "urgent" if level == "urgent" else "high", "checkin_id": checkin_id},
            offset_hours=now_h, origin="patient_response",
            rationale="New caregiver disclosure: always individually approved.",
        ))
    return replan(session, episode, trigger="patient_response", reason=f"Check-in #{checkin_id}: {level} ({why})",
                  add=adds, rationale=f"Patient check-in triaged as {level}: {why}.")


def rejected_action_replan(session: Session, episode: DischargeEpisode, action: ProposedAction, *, reason: str,
                           doctor_id: int, doctor_label: str) -> Optional[PlanVersion]:
    if current_plan(session, episode) is None:
        return None
    pv = replan(session, episode, trigger="rejected_action", reason=f"{action.step_key} rejected: {reason}",
                actor_type="user", actor_id=doctor_id, actor_label=doctor_label, created_by=f"user:{doctor_id}",
                rationale=f"Doctor rejected '{action.step_key}'. It will not be sent.")
    cs = [c for c in pv.change_summary if c.get("step_key") != action.step_key]
    pv.change_summary = [{"change": "removed", "step_key": action.step_key,
                          "description": f"'{action.step_key}' rejected by {doctor_label}: {reason}. It will not be executed."}] + cs
    return pv
