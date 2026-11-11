"""Step 1 of the agent: turn a free-text discharge summary into structured data."""
import re

from ..llm import get_llm
from .prompts import PARSER_SYSTEM

SECTION_RE = re.compile(r"^\s*(diagnosis|allergies|medications|follow[- ]?up|red flags|warning signs|social)\s*:\s*(.*)$", re.I)
MED_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z\- ]*?)\s+(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units|iu))\b\s*(?P<rest>.*)$", re.I
)
HIGH_RISK_TERMS = ["heart failure", "myocardial", "copd", "stroke", "sepsis", "pneumonia", "dka", "ketoacidosis", "kidney"]


def _rules_parse(text: str, age: int | None) -> dict:
    data = {"diagnosis": "", "allergies": "", "medications": [], "follow_up": [], "red_flags": [], "social": ""}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            key = m.group(1).lower().replace("-", "").replace(" ", "")
            section = {"followup": "follow_up", "redflags": "red_flags", "warningsigns": "red_flags"}.get(key, key)
            inline = m.group(2).strip()
            if inline and section in ("diagnosis", "allergies", "social"):
                data[section] = inline
            continue
        if not line.startswith(("-", "*", "•")):
            continue
        item = line.lstrip("-*• ").strip()
        if section == "medications":
            mm = MED_RE.match(item)
            if mm:
                rest = mm.group("rest").strip(" ,;-")
                parts = re.split(r"\s+[-–—]\s+|;\s*", rest, maxsplit=1)
                freq = parts[0].strip()
                instr = parts[1].strip() if len(parts) > 1 else ""
                data["medications"].append(
                    {"name": mm.group("name").strip().title(), "dose": mm.group("dose"), "frequency": freq, "instructions": instr}
                )
            else:
                data["medications"].append({"name": item.title(), "dose": "", "frequency": "", "instructions": ""})
        elif section == "follow_up":
            days = re.search(r"within\s+(\d+)\s+days?", item, re.I)
            provider = re.search(r"\(([^)]+)\)", item)
            title = re.sub(r"\s*within\s+\d+\s+days?|\s*\([^)]*\)", "", item, flags=re.I).strip()
            data["follow_up"].append(
                {"title": title, "provider": provider.group(1) if provider else "", "within_days": int(days.group(1)) if days else 7}
            )
        elif section == "red_flags":
            data["red_flags"].append(item)

    # Risk heuristic (LACE-inspired, simplified)
    reasons = []
    if age and age >= 65:
        reasons.append(f"Age {age}")
    if len(data["medications"]) >= 5:
        reasons.append(f"Polypharmacy ({len(data['medications'])} medicines)")
    if any(t in data["diagnosis"].lower() for t in HIGH_RISK_TERMS):
        reasons.append("High-readmission diagnosis")
    if re.search(r"lives alone", text, re.I):
        reasons.append("Lives alone")
    if re.search(r"readmi|previous admission", text, re.I):
        reasons.append("Prior admission")
    data["risk_reasons"] = reasons
    data["risk_level"] = "high" if len(reasons) >= 3 else "medium" if reasons else "low"
    return data


def parse_discharge(text: str, age: int | None = None) -> tuple[dict, str]:
    """Returns (parsed, source) where source is the LLM name or 'rules'."""
    rules = _rules_parse(text, age)
    llm = get_llm()
    if llm.live:
        out = llm.complete_json(PARSER_SYSTEM, f"Patient age: {age}\n\nDischarge summary:\n{text}")
        if out and isinstance(out.get("medications"), list) and out.get("diagnosis"):
            for k, v in rules.items():
                out.setdefault(k, v)
            if out.get("risk_level") not in ("low", "medium", "high"):
                out["risk_level"] = rules["risk_level"]
            return out, llm.name
    return rules, "rules"
