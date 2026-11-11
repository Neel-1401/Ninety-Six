"""Tool registry (Agent B). Tests can swap adapters with `set_adapter(name, fake)`."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import llm
from ..clock import iso
from ..models import ToolExecution
from .base import ToolStatus
from .calendar import CalendarAdapter
from .care_team_email import CareTeamEmailAdapter
from .medication_info import MedicationInfoAdapter
from .messaging import MessagingAdapter

_DEFAULTS = {
    "messaging": MessagingAdapter,
    "calendar": CalendarAdapter,
    "care_team_email": CareTeamEmailAdapter,
    "medication_info": MedicationInfoAdapter,
}
_overrides: dict[str, Any] = {}


def set_adapter(name: str, adapter: Optional[Any]) -> None:
    if adapter is None:
        _overrides.pop(name, None)
    else:
        _overrides[name] = adapter


FAULT_TOOLS = ("messaging", "calendar", "care_team_email", "medication_info", "triage")
FAULT_VALUES = ("none", "fail", "timeout")
FAULTS_KEY = "demo_faults"


def read_demo_faults(session: Optional[Session] = None) -> dict[str, str]:
    """Demo-only fault switch (SystemState 'demo_faults'). Always all-'none' outside demo mode."""
    from ..config import settings
    from ..models import SystemState

    out = {k: "none" for k in FAULT_TOOLS}
    if not settings.demo_mode:
        return out
    if session is None:
        from .. import db

        with db.SessionLocal() as s:
            row = s.get(SystemState, FAULTS_KEY)
            value = dict(row.value or {}) if row else {}
    else:
        row = session.get(SystemState, FAULTS_KEY)
        value = dict(row.value or {}) if row else {}
    for k, v in value.items():
        if k in out and v in FAULT_VALUES:
            out[k] = v
    return out


class _FaultInjected:
    """Wraps an adapter at EXECUTION time only; the provider is never called while a fault is set."""

    def __init__(self, inner, fault: str):  # noqa: ANN001
        self.inner, self.fault = inner, fault

    def __getattr__(self, item):  # noqa: ANN001, ANN204
        return getattr(self.inner, item)

    def execute(self, request: dict[str, Any], *, idempotency_key: str):  # noqa: ANN201
        from .base import ToolResult

        mode = self.inner.mode() if hasattr(self.inner, "mode") else (
            self.inner.mode_for(request.get("to")) if hasattr(self.inner, "mode_for") else "simulated")
        if self.fault == "fail":
            return ToolResult("failure", mode, "demo_fault", delivery_status="failed", retryable=False,
                              safe_error="Injected demo fault: the provider rejected the request (nothing sent)",
                              data={"injected_fault": "fail"})
        return ToolResult("unknown", mode, "demo_fault", delivery_status="unknown",
                          safe_error="Injected demo fault: the provider timed out; outcome unknown",
                          data={"injected_fault": "timeout"})


def get_adapter(name: str, *, for_execution: bool = False):  # noqa: ANN201
    adapter = _overrides.get(name) or _DEFAULTS[name]()
    if for_execution:
        fault = read_demo_faults().get(name, "none")
        if fault != "none":
            return _FaultInjected(adapter, fault)
    return adapter


def get_messaging() -> MessagingAdapter:
    return get_adapter("messaging")


def llm_status() -> dict[str, Any]:
    return llm.status()


def tool_statuses(session: Optional[Session] = None) -> list[ToolStatus]:
    out = [get_adapter(n).status() for n in ("messaging", "calendar", "medication_info", "care_team_email")]
    s = llm.status()
    out.append(ToolStatus("llm", "Language model", s["mode"], s["configured"], s["detail"]))
    faults = read_demo_faults(session)
    for st in out:
        if faults.get(st.name, "none") != "none":
            st.detail += f" | DEMO FAULT INJECTED: {faults[st.name]}"
    if session is not None:
        for st in out:
            last_ok = session.scalar(
                select(func.max(ToolExecution.finished_at)).where(
                    ToolExecution.tool == st.name, ToolExecution.mode == "live", ToolExecution.outcome == "success"
                )
            )
            last_bad = session.scalar(
                select(func.max(ToolExecution.finished_at)).where(
                    ToolExecution.tool == st.name, ToolExecution.outcome.in_(["failure", "unknown"])
                )
            )
            st.last_live_success_at = iso(last_ok) if last_ok else None
            st.last_failure_at = iso(last_bad) if last_bad else None
    return out
