from .base import Tool, ToolContext
from .calendar import BookAppointmentTool
from .care_team import ApproveScheduleTool, EscalateTool, GenerateSummaryTool
from .drug_info import LookupDrugInfoTool
from .messaging import SendMessageTool

REGISTRY: dict[str, Tool] = {
    t.name: t
    for t in [
        LookupDrugInfoTool(),
        SendMessageTool(),
        BookAppointmentTool(),
        EscalateTool(),
        ApproveScheduleTool(),
        GenerateSummaryTool(),
    ]
}


def tool_catalog() -> str:
    return "\n".join(
        f"- {t.name} ({'IRREVERSIBLE, needs approval' if t.irreversible else 'safe'}): {t.description} args={t.args_doc}"
        for t in REGISTRY.values()
    )


__all__ = ["REGISTRY", "Tool", "ToolContext", "tool_catalog"]
