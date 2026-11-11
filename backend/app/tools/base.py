"""Tool adapter contract. LEAD-OWNED CONTRACT; adapters are owned by Agent B.

Every adapter returns a ToolResult with a consistent shape. Rules:
- `mode` is "live" only if a real provider was actually called. Simulated output is never
  relabelled as live, and a failed live call stays "failure"/"unknown" (never "simulated success").
- `outcome="unknown"` when the provider MAY have performed the action (timeout, connection
  dropped after sending, 5xx after request body sent). Side-effecting actions with unknown
  outcome are NOT retried blindly; they require reconciliation (CONTRACTS.md §6).
- `delivery_status` for messages: queued | provider_accepted | delivered | failed | unknown |
  simulated | not_applicable. Provider acceptance is not delivery.
- `safe_error` must not contain secrets, tokens, full phone numbers or raw provider bodies.
- Adapters do NOT touch the database. The executor persists ToolExecution + domain rows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional, Protocol

from ..clock import clock, iso

Outcome = Literal["success", "failure", "unknown"]
ToolMode = Literal["live", "simulated"]


@dataclass
class ToolResult:
    outcome: Outcome
    mode: ToolMode
    provider: str  # twilio | google_calendar | openfda | smtp | local
    provider_ref: Optional[str] = None  # e.g. Twilio SID, Google event id
    provider_status: Optional[str] = None  # raw provider status word, e.g. "queued"
    delivery_status: Optional[str] = None
    safe_error: Optional[str] = None
    retryable: bool = False  # True only when the action certainly did NOT happen
    at: datetime = field(default_factory=clock.now)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["at"] = iso(self.at)
        return d


@dataclass
class ToolStatus:
    """Shown on Settings: what mode the tool WILL use and why."""

    name: str  # messaging | calendar | medication_info | care_team_email | llm
    label: str
    mode: ToolMode
    configured: bool
    detail: str  # e.g. "Twilio SMS; DEMO_PHONE redirect active (+91•••••1234)"
    last_live_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None


class ToolAdapter(Protocol):
    name: str

    def status(self) -> ToolStatus: ...

    def execute(self, request: dict[str, Any], *, idempotency_key: str) -> ToolResult: ...
