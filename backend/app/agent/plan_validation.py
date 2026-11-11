"""Plan schema + deterministic validation (Agent B).

An LLM-proposed plan is only used if it passes ALL checks here; otherwise the rule-based plan is
used and the fallback is recorded. Approval requirements are never taken from the LLM: they are
computed by policy/approval.py.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..policy.approval import ALLOWED_ACTIONS
from .checkin_templates import TEMPLATES as CHECKIN_TEMPLATES
from .message_templates import CAREGIVER_TEMPLATES, PATIENT_TEMPLATES, TEAM_TEMPLATES

STEP_KEY_RE = re.compile(r"^[a-z0-9_]{2,64}$")


class SendMessageArgs(BaseModel, extra="forbid"):
    recipient_role: Literal["patient", "caregiver"]
    template_id: str
    channel: Literal["sms", "whatsapp"] = "sms"
    checkin_title: Optional[str] = None
    priority: Literal["urgent", "high", "normal"] = "normal"
    checkin_id: Optional[int] = None
    supporting_fact_ids: list[int] = []

    @field_validator("template_id")
    @classmethod
    def _tpl(cls, v: str) -> str:
        if v not in PATIENT_TEMPLATES | CAREGIVER_TEMPLATES:
            raise ValueError(f"unknown message template {v}")
        return v


class CalendarArgs(BaseModel, extra="forbid"):
    title: str = Field(min_length=2, max_length=160)
    provider_name: Optional[str] = None
    location: Optional[str] = None
    within_days: Optional[int] = Field(None, ge=1, le=30)
    source_fact_id: Optional[int] = None


class CareTeamAlertArgs(BaseModel, extra="forbid"):
    template_id: Literal["care_team_alert_v1"] = "care_team_alert_v1"
    urgency: Literal["urgent", "high", "normal"] = "urgent"
    checkin_id: Optional[int] = None
    reasons: list[str] = []
    supporting_fact_ids: list[int] = []


class OpenCheckinArgs(BaseModel, extra="forbid"):
    template_id: str
    title: str = Field(min_length=2, max_length=120)
    available_hours: float = Field(12, ge=1, le=48)

    @field_validator("template_id")
    @classmethod
    def _tpl(cls, v: str) -> str:
        if v not in CHECKIN_TEMPLATES:
            raise ValueError(f"unknown check-in template {v}")
        return v


class MedicationReviewArgs(BaseModel, extra="forbid"):
    medications: list[str] = []


class StaffReviewArgs(BaseModel, extra="forbid"):
    kind: Literal["medication_review"] = "medication_review"
    title: str
    summary: str = ""
    requires_role: Literal["doctor", "any"] = "doctor"
    finding_fact_ids: list[int] = []


class GenerateSummaryArgs(BaseModel, extra="forbid"):
    pass


ARG_SCHEMAS: dict[str, type[BaseModel]] = {
    "send_message": SendMessageArgs,
    "create_calendar_event": CalendarArgs,
    "care_team_alert": CareTeamAlertArgs,
    "open_checkin": OpenCheckinArgs,
    "medication_review": MedicationReviewArgs,
    "staff_review": StaffReviewArgs,
    "generate_summary": GenerateSummaryArgs,
}


class StepSpec(BaseModel):
    step_key: str
    title: str = Field(min_length=2, max_length=160)
    purpose: str = ""
    action_type: str
    arguments: dict[str, Any] = {}
    offset_hours: float = Field(ge=0, le=96)
    depends_on: list[str] = []
    rationale: str = ""
    freshness_hours: Optional[float] = Field(None, gt=0, le=96)
    origin: str = "initial"


class PlanSpec(BaseModel):
    rationale: str = ""
    steps: list[StepSpec]


def validate_plan(raw: Any, *, caregiver_contact_ok: bool, patient_phone_ok: bool = True) -> tuple[Optional[PlanSpec], list[str]]:
    """Returns (normalised plan, errors). Any error => plan rejected."""
    errors: list[str] = []
    try:
        plan = raw if isinstance(raw, PlanSpec) else PlanSpec.model_validate(raw)
    except ValidationError as e:
        return None, [f"schema: {err['loc']}: {err['msg']}" for err in e.errors()][:20]
    keys: set[str] = set()
    normalised: list[StepSpec] = []
    for s in plan.steps:
        where = f"step '{s.step_key}'"
        if not STEP_KEY_RE.match(s.step_key):
            errors.append(f"{where}: invalid step_key")
        if s.step_key in keys:
            errors.append(f"{where}: duplicate step_key")
        keys.add(s.step_key)
        if s.action_type not in ALLOWED_ACTIONS:
            errors.append(f"{where}: action type '{s.action_type}' is not allowed")
            continue
        try:
            args = ARG_SCHEMAS[s.action_type].model_validate(s.arguments).model_dump()
        except ValidationError as e:
            errors.append(f"{where}: invalid arguments: " + "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()))
            continue
        if s.action_type == "send_message":
            tpl, role = args["template_id"], args["recipient_role"]
            if role == "patient" and tpl not in PATIENT_TEMPLATES:
                errors.append(f"{where}: template {tpl} is not a patient template")
            if role == "caregiver" and tpl not in CAREGIVER_TEMPLATES:
                errors.append(f"{where}: template {tpl} is not a caregiver template")
            if role == "caregiver" and not caregiver_contact_ok:
                errors.append(f"{where}: caregiver is not an authorised contact with a phone number")
            if role == "patient" and not patient_phone_ok:
                errors.append(f"{where}: patient has no phone number")
        if s.action_type == "care_team_alert" and args["template_id"] not in TEAM_TEMPLATES:
            errors.append(f"{where}: invalid care-team template")
        normalised.append(s.model_copy(update={"arguments": args}))
    # dependencies exist and are acyclic
    graph = {s.step_key: list(s.depends_on) for s in plan.steps}
    for k, deps in graph.items():
        for d in deps:
            if d not in graph:
                errors.append(f"step '{k}': depends on unknown step '{d}'")
            elif d == k:
                errors.append(f"step '{k}': depends on itself")
    visiting, done = set(), set()

    def visit(n: str) -> bool:
        if n in done:
            return True
        if n in visiting:
            return False
        visiting.add(n)
        ok = all(visit(d) for d in graph.get(n, []) if d in graph)
        visiting.discard(n)
        done.add(n)
        return ok

    if not all(visit(k) for k in graph):
        errors.append("dependencies contain a cycle")
    # required structure
    by_key = {s.step_key: s for s in plan.steps}
    if not any(s.action_type == "medication_review" for s in plan.steps):
        errors.append("plan must include a medication_review step")
    if not any(s.action_type == "generate_summary" for s in plan.steps):
        errors.append("plan must include a generate_summary step at 96 h")
    for s in plan.steps:
        if s.action_type == "send_message" and s.arguments.get("template_id") == "welcome_v1":
            if not any(by_key.get(d) and by_key[d].action_type == "medication_review" for d in s.depends_on):
                errors.append(f"step '{s.step_key}': welcome message must depend on the medication review")
    if errors:
        return None, errors
    return PlanSpec(rationale=plan.rationale, steps=normalised), []
