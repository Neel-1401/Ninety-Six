"""Pydantic request bodies (validation). Shapes follow docs/CONTRACTS.md §4."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


# Original documents are stored exactly as pasted (no whitespace stripping).
RawText = Annotated[str, StringConstraints(strip_whitespace=False, min_length=20, max_length=20000)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("Include a timezone (e.g. 2026-11-11T08:30:00Z or +05:30)")
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- auth
class StaffLoginIn(_In):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class RequestCodeIn(_In):
    phone: str = Field(min_length=6, max_length=32)
    invitation_token: Optional[str] = Field(default=None, max_length=200)


class VerifyCodeIn(_In):
    phone: str = Field(min_length=6, max_length=32)
    code: str = Field(min_length=1, max_length=12)


# --------------------------------------------------------------------------- episodes
class PatientIn(_In):
    full_name: str = Field(min_length=1, max_length=120)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: Optional[str] = Field(default=None, max_length=16)
    phone_e164: str = Field(min_length=6, max_length=32)
    preferred_language: str = Field(default="English", min_length=1, max_length=32)
    caregiver_name: Optional[str] = Field(default=None, max_length=120)
    caregiver_relation: Optional[str] = Field(default=None, max_length=60)
    caregiver_phone_e164: Optional[str] = Field(default=None, max_length=32)
    caregiver_authorised: bool = False
    hospital_number: Optional[str] = Field(default=None, max_length=40)


class PatientPatchIn(_In):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: Optional[str] = Field(default=None, max_length=16)
    phone_e164: Optional[str] = Field(default=None, min_length=6, max_length=32)
    preferred_language: Optional[str] = Field(default=None, min_length=1, max_length=32)
    caregiver_name: Optional[str] = Field(default=None, max_length=120)
    caregiver_relation: Optional[str] = Field(default=None, max_length=60)
    caregiver_phone_e164: Optional[str] = Field(default=None, max_length=32)
    caregiver_authorised: Optional[bool] = None
    hospital_number: Optional[str] = Field(default=None, max_length=40)


class RiskInputsIn(_In):
    lives_alone: Optional[bool] = None
    prior_admission_6m: Optional[bool] = None
    caregiver_available: Optional[bool] = None


class EpisodeCreateIn(_In):
    patient: PatientIn
    discharge_at: datetime
    doctor_id: int
    nurse_id: Optional[int] = None
    risk_inputs: RiskInputsIn = Field(default_factory=RiskInputsIn)
    summary_text: RawText
    sample_key: Optional[str] = Field(default=None, max_length=40)

    @field_validator("discharge_at")
    @classmethod
    def _check_tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _aware(v)


class EpisodePatchIn(_In):
    row_version: int
    patient: Optional[PatientPatchIn] = None
    discharge_at: Optional[datetime] = None
    risk_inputs: Optional[RiskInputsIn] = None

    @field_validator("discharge_at")
    @classmethod
    def _check_tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _aware(v)


class TeamIn(_In):
    doctor_id: int
    nurse_id: int


class FactCreateIn(_In):
    category: Literal[
        "diagnosis", "allergy", "medication", "follow_up", "red_flag", "social", "instruction", "note"
    ]
    label: str = Field(min_length=1, max_length=160)
    value: dict[str, Any]


class FactPatchIn(_In):
    value: dict[str, Any]
    label: Optional[str] = Field(default=None, min_length=1, max_length=160)
    reason: Optional[str] = Field(default=None, max_length=500)


class ReasonIn(_In):
    reason: str = Field(min_length=1, max_length=1000)


class OptionalReasonIn(_In):
    reason: Optional[str] = Field(default=None, max_length=1000)


class NoteIn(_In):
    body: str = Field(min_length=1, max_length=4000)
    review_item_id: Optional[int] = None


class NoteOnlyIn(_In):
    note: str = Field(min_length=1, max_length=2000)


class OptionalNoteIn(_In):
    note: Optional[str] = Field(default=None, max_length=2000)


class RoutineScopeIn(_In):
    template_ids: list[str] = Field(min_length=1, max_length=10)
    recipient_roles: list[Literal["patient", "caregiver"]] = Field(min_length=1, max_length=2)
    offsets_hours: list[float] = Field(min_length=1, max_length=24)
    max_per_day: int = Field(default=3, ge=1, le=12)

    @field_validator("offsets_hours")
    @classmethod
    def _bounds(cls, v: list[float]) -> list[float]:
        for h in v:
            if not 0 <= h <= 96:
                raise ValueError("offsets must be between 0 and 96 hours")
        return v


class ActivateIn(_In):
    plan_version_id: int
    confirm_fact_ids: list[int]
    routine: Optional[RoutineScopeIn] = None


# --------------------------------------------------------------------------- actions
class ApproveIn(_In):
    payload_hash: str = Field(min_length=8, max_length=64)


class EditApproveIn(_In):
    base_payload_hash: str = Field(min_length=8, max_length=64)
    payload: dict[str, Any]


class RejectIn(_In):
    base_payload_hash: str = Field(min_length=8, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


class ReconcileIn(_In):
    outcome: Literal["success", "failure"]
    note: str = Field(min_length=1, max_length=1000)


class AssignIn(_In):
    user_id: int


# --------------------------------------------------------------------------- patient
class AnswerIn(_In):
    question_id: str = Field(min_length=1, max_length=64)
    value: Any


class CheckInSubmitIn(_In):
    submission_id: str = Field(min_length=8, max_length=64)
    answers: list[AnswerIn] = Field(default_factory=list, max_length=40)
    free_text: Optional[str] = Field(default=None, max_length=4000)


class CheckInDraftIn(_In):
    answers: dict[str, Any] = Field(default_factory=dict)
    free_text: Optional[str] = Field(default=None, max_length=4000)


class ConcernIn(_In):
    submission_id: str = Field(min_length=8, max_length=64)
    text: str = Field(min_length=1, max_length=4000)


# --------------------------------------------------------------------------- demo
class LoginAsIn(_In):
    user_id: int


class AdvanceIn(_In):
    hours: Literal[6, 24]


class PatientResponseIn(_In):
    preset: Literal["all_good", "concern", "red_flag"]


class ResetIn(_In):
    confirm: str
