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


def get_adapter(name: str):  # noqa: ANN201
    return _overrides.get(name) or _DEFAULTS[name]()


def get_messaging() -> MessagingAdapter:
    return get_adapter("messaging")


def llm_status() -> dict[str, Any]:
    return llm.status()


def tool_statuses(session: Optional[Session] = None) -> list[ToolStatus]:
    out = [get_adapter(n).status() for n in ("messaging", "calendar", "medication_info", "care_team_email")]
    s = llm.status()
    out.append(ToolStatus("llm", "Language model", s["mode"], s["configured"], s["detail"]))
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
