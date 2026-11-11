from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from ..models import Patient, Step


@dataclass
class ToolContext:
    session: Session
    patient: Patient
    step: Step


class Tool:
    """A real-world action. `irreversible` tools are always gated behind a
    human approval unless the clinician pre-approved the step's group."""

    name: str = ""
    description: str = ""
    args_doc: str = ""
    irreversible: bool = False

    def live(self) -> bool:
        """True when the real external API is configured (vs simulated)."""
        return False

    def preview(self, ctx: ToolContext, args: dict) -> str:
        """Human-readable summary shown on the approval card."""
        return ctx.step.title

    def run(self, ctx: ToolContext, args: dict) -> dict[str, Any]:
        raise NotImplementedError
