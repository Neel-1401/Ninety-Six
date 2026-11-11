"""Database tables. SQLite is the agent's long-term memory: everything the agent
learns or does is stored here and re-read on the next step."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .clock import clock


class Patient(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    age: Optional[int] = None
    sex: str = ""
    phone: str = ""
    language: str = "English"
    caregiver_name: str = ""
    caregiver_phone: str = ""
    diagnosis: str = ""
    allergies: str = ""
    risk_level: str = "medium"  # low | medium | high
    risk_reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "planning"  # planning | active | escalated | completed
    discharge_at: datetime = Field(default_factory=clock.now)
    summary_text: str = ""
    created_at: datetime = Field(default_factory=clock.now)


class Medication(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    name: str
    dose: str = ""
    frequency: str = ""
    instructions: str = ""
    warnings: str = ""


class Plan(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    version: int = 1
    goal: str = ""
    rationale: str = ""
    source: str = "rules"  # llm | rules
    status: str = "active"  # active | superseded
    created_at: datetime = Field(default_factory=clock.now)


class Step(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="plan.id", index=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    seq: int = 0
    title: str
    tool: str
    args: dict = Field(default_factory=dict, sa_column=Column(JSON))
    due_offset_h: float = 0.0  # hours after discharge
    group: str = ""  # steps in a group can be pre-approved together
    origin: str = "plan"  # plan | replan | followup
    irreversible: bool = False
    preapproved: bool = False
    rationale: str = ""
    # pending | awaiting_approval | approved | running | done | failed | rejected | skipped
    status: str = "pending"
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=clock.now)
    executed_at: Optional[datetime] = None


class Approval(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    step_id: int = Field(foreign_key="step.id", index=True)
    title: str
    summary: str = ""
    tool: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    priority: str = "normal"  # normal | urgent
    status: str = "pending"  # pending | approved | rejected
    note: str = ""
    created_at: datetime = Field(default_factory=clock.now)
    decided_at: Optional[datetime] = None


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    direction: str  # out | in
    recipient: str = "patient"  # patient | caregiver
    channel: str = "sms"
    body: str
    delivery: str = "simulated"  # simulated | live | failed
    triage: str = ""  # ok | concern | red_flag (inbound only)
    created_at: datetime = Field(default_factory=clock.now)


class Appointment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    title: str
    provider: str = ""
    start_at: datetime
    location: str = ""
    external_id: str = ""
    calendar: str = "simulated"  # simulated | google
    status: str = "booked"


class MemoryFact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patient.id", index=True)
    key: str
    value: str
    source: str = ""  # parser | triage | tool:<name> | clinician
    created_at: datetime = Field(default_factory=clock.now)


class Event(SQLModel, table=True):
    """Agent trace: every plan, tool call, approval and memory read/write."""

    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: Optional[int] = Field(default=None, index=True)
    kind: str  # plan | tool | approval | message | triage | memory | escalation | system | llm
    title: str
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=clock.now)
