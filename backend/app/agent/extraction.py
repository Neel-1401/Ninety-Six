"""Intake understanding (Agent B): discharge summary -> PatientFact rows with provenance.

LLM (when configured) returns structured output validated by Pydantic AND grounded against the
document (every item must cite a line that actually contains it). Anything ungrounded is dropped
and noted. The deterministic regex parser is the fallback and the default demo path.
Absent sections become explicit "not provided" facts; doses/instructions are never invented.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..events import record_event
from ..models import DischargeDocument, DischargeEpisode, FactSourceKind, Patient, PatientFact, User
from .memory import add_fact
from .risk import compute_risk

SECTION_RE = re.compile(
    r"^\s*(diagnosis|allergies|allergy|medications|medicines|follow[- ]?up|red flags|warning signs|social|instructions)\s*:\s*(.*)$",
    re.I,
)
MED_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z\- ]*?)\s+(?P<dose>\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units|iu|tablets?))\b\s*(?P<rest>.*)$",
    re.I,
)
FREQ_START_RE = re.compile(
    r"\b(once|twice|three times|thrice|every|daily|at bedtime|at night|in the morning|as needed|before|after|with)\b", re.I
)
SECTION_KEYS = {
    "diagnosis": "diagnosis", "allergies": "allergy", "allergy": "allergy", "medications": "medication",
    "medicines": "medication", "followup": "follow_up", "redflags": "red_flag", "warningsigns": "red_flag",
    "social": "social", "instructions": "instruction",
}
REQUIRED = {
    "diagnosis": "Diagnosis",
    "allergy": "Allergies",
    "medication": "Medicines",
    "follow_up": "Follow-up instructions",
    "red_flag": "Warning signs (red flags)",
}


# ----------------------------------------------------------------------------- rules parser
def _split_med(item: str) -> dict[str, str]:
    m = MED_RE.match(item)
    if m:
        name, dose, rest = m.group("name").strip(), m.group("dose").strip(), m.group("rest").strip(" ,;-")
    else:
        fm = FREQ_START_RE.search(item)
        name = item[: fm.start()].strip(" ,;-") if fm else item.strip()
        dose, rest = "not provided", item[fm.start():].strip(" ,;-") if fm else ""
    parts = re.split(r"\s+[-–—]\s+|;\s*", rest, maxsplit=1)
    freq = parts[0].strip() if parts and parts[0].strip() else "not provided"
    instr = parts[1].strip() if len(parts) > 1 else ""
    return {"name": name.strip().title() if name else item.strip(), "dose": dose, "frequency": freq, "instructions": instr}


def rules_extract(text: str) -> dict[str, Any]:
    """Returns {"items": [{"category","value","line","excerpt"}], "notes": [...]}."""
    items: list[dict[str, Any]] = []
    notes: list[str] = []
    section: Optional[str] = None
    for idx, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            key = re.sub(r"[- ]", "", m.group(1).lower())
            section = SECTION_KEYS.get(key)
            inline = m.group(2).strip()
            if inline and section in ("diagnosis", "allergy", "social"):
                items.append({"category": section, "value": {"text": inline}, "line": idx, "excerpt": line})
            continue
        if not line.startswith(("-", "*", "•")) or section is None:
            continue
        item = line.lstrip("-*• ").strip()
        if section == "medication":
            med = _split_med(item)
            if med["dose"] == "not provided":
                notes.append(f"Dose not stated for {med['name']} (line {idx}); left as 'not provided', not invented.")
            if med["frequency"] == "not provided":
                notes.append(f"Frequency not stated for {med['name']} (line {idx}).")
            items.append({"category": "medication", "value": med, "line": idx, "excerpt": line})
        elif section == "follow_up":
            days = re.search(r"within\s+(\d+)\s+days?", item, re.I)
            provider = re.search(r"\(([^)]+)\)", item)
            title = re.sub(r"\s*within\s+\d+\s+days?|\s*\([^)]*\)", "", item, flags=re.I).strip(" ,-")
            if not days:
                notes.append(f"Follow-up timing not stated for '{title}' (line {idx}).")
            items.append(
                {
                    "category": "follow_up",
                    "value": {
                        "title": title,
                        "provider": provider.group(1) if provider else None,
                        "within_days": int(days.group(1)) if days else None,
                    },
                    "line": idx,
                    "excerpt": line,
                }
            )
        else:
            items.append({"category": section, "value": {"text": item}, "line": idx, "excerpt": line})
    return {"items": items, "notes": notes}


# ----------------------------------------------------------------------------- LLM schema
class _Item(BaseModel):
    text: str
    line: int = Field(description="1-based line number in the numbered document where this appears")


class _Med(BaseModel):
    name: str
    dose: Optional[str] = Field(None, description="Exactly as written; null if not written")
    frequency: Optional[str] = Field(None, description="Exactly as written; null if not written")
    instructions: Optional[str] = None
    line: int


class _FollowUp(BaseModel):
    title: str
    provider: Optional[str] = None
    within_days: Optional[int] = None
    line: int


class LLMExtraction(BaseModel):
    diagnosis: list[_Item] = []
    allergies: list[_Item] = []
    medications: list[_Med] = []
    follow_up: list[_FollowUp] = []
    red_flags: list[_Item] = []
    instructions: list[_Item] = []
    social: list[_Item] = []
    ambiguities: list[str] = []


SYSTEM = (
    "You extract structured facts from a fictional hospital discharge summary for a care-coordination "
    "prototype. Copy wording exactly as written. Never infer, complete or invent a diagnosis, medicine, "
    "dose, frequency or instruction: if it is not written, use null or leave the list empty and add an "
    "ambiguity note. Every item must cite the 1-based line number it came from."
)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def _grounded(text: str, line_text: str) -> bool:
    w = _words(text)
    return bool(w) and len(w & _words(line_text)) / len(w) >= 0.6


def llm_extract(text: str) -> tuple[Optional[dict[str, Any]], Optional[str], str]:
    """Returns (result|None, error|None, method)."""
    lines = text.splitlines()
    numbered = "\n".join(f"L{i}: {ln}" for i, ln in enumerate(lines, 1))
    res = llm.structured(SYSTEM, numbered, LLMExtraction, purpose="extraction")
    if not res.ok or res.data is None:
        return None, res.error, "rules"
    out: list[dict[str, Any]] = []
    notes = list(res.data.ambiguities)

    def line_of(n: int) -> Optional[str]:
        return lines[n - 1] if 1 <= n <= len(lines) else None

    def add(cat: str, value: dict, n: int, check: str) -> None:
        lt = line_of(n)
        if lt is None or not _grounded(check, lt):
            notes.append(f"Dropped ungrounded {cat} item '{check[:60]}' (cited line {n}).")
            return
        out.append({"category": cat, "value": value, "line": n, "excerpt": lt.strip()})

    for it in res.data.diagnosis:
        add("diagnosis", {"text": it.text}, it.line, it.text)
    for it in res.data.allergies:
        add("allergy", {"text": it.text}, it.line, it.text)
    for m in res.data.medications:
        lt = line_of(m.line) or ""
        dose = m.dose if m.dose and all(d in lt for d in re.findall(r"\d+(?:\.\d+)?", m.dose)) else None
        if m.dose and dose is None:
            notes.append(f"Dropped dose '{m.dose}' for {m.name}: not found on line {m.line}.")
        add(
            "medication",
            {"name": m.name, "dose": dose or "not provided", "frequency": m.frequency or "not provided",
             "instructions": m.instructions or ""},
            m.line,
            m.name,
        )
    for fu in res.data.follow_up:
        add("follow_up", {"title": fu.title, "provider": fu.provider, "within_days": fu.within_days}, fu.line, fu.title)
    for it in res.data.red_flags:
        add("red_flag", {"text": it.text}, it.line, it.text)
    for it in res.data.instructions:
        add("instruction", {"text": it.text}, it.line, it.text)
    for it in res.data.social:
        add("social", {"text": it.text}, it.line, it.text)
    return {"items": out, "notes": notes}, None, f"llm:{res.provider}"


# ----------------------------------------------------------------------------- persistence
def _label(cat: str, value: dict[str, Any]) -> str:
    if cat == "medication":
        return value.get("name") or "Medicine"
    if cat == "follow_up":
        return value.get("title") or "Follow-up"
    return {"diagnosis": "Diagnosis", "allergy": "Allergy", "red_flag": "Warning sign", "social": "Social context",
            "instruction": "Instruction"}.get(cat, cat)


def run_extraction(session: Session, episode: DischargeEpisode, *, actor: User) -> None:
    doc = session.scalars(
        select(DischargeDocument)
        .where(DischargeDocument.episode_id == episode.id)
        .order_by(DischargeDocument.id.desc())
    ).first()
    text = doc.text if doc else ""

    # LLM first (before any write so no write lock is held during the network call).
    result, err, method = (None, None, "rules")
    if llm.is_configured() and text.strip():
        # A-08: release any write lock held by the caller's transaction before the network call.
        session.commit()
        result, err, method = llm_extract(text)
    notes: list[str] = []
    if result is None:
        if llm.is_configured():
            notes.append(f"LLM extraction unavailable ({err}); deterministic parser used.")
            method = "rules_fallback"
        result = rules_extract(text)
    notes.extend(result["notes"])

    # Replace previous agent-extracted facts (staff entries/corrections are kept).
    for f in session.scalars(
        select(PatientFact).where(
            PatientFact.episode_id == episode.id,
            PatientFact.active.is_(True),
            PatientFact.source_kind.in_([FactSourceKind.EXTRACTED, FactSourceKind.INFERRED]),
        )
    ):
        f.active = False

    doc_ref = f"document:{doc.id}" if doc else "document:none"
    found: dict[str, int] = {}
    for it in result["items"]:
        cat = it["category"]
        found[cat] = found.get(cat, 0) + 1
        add_fact(
            session,
            episode.id,
            category=cat,
            label=_label(cat, it["value"]),
            value=it["value"],
            source_kind=FactSourceKind.EXTRACTED,
            source_ref=f"{doc_ref}#L{it['line']}",
            source_excerpt=it["excerpt"],
            extraction_method=method,
        )
    for cat, label in REQUIRED.items():
        if not found.get(cat):
            add_fact(
                session,
                episode.id,
                category=cat,
                label=f"{label}: not provided",
                value={"text": "not provided"},
                source_kind=FactSourceKind.EXTRACTED,
                source_ref=doc_ref,
                source_excerpt=None,
                extraction_method=method,
                is_missing=True,
            )
            notes.append(f"{label} not found in the summary; marked 'not provided'.")

    patient = session.get(Patient, episode.patient_id)
    diag_texts = [it["value"].get("text", "") for it in result["items"] if it["category"] == "diagnosis"]
    risk = compute_risk(
        age=patient.age if patient else None,
        risk_inputs=dict(episode.risk_inputs or {}),
        diagnosis_texts=diag_texts,
        medication_count=found.get("medication", 0),
        summary_text=text,
    )
    episode.risk_category = risk["category"]
    episode.risk_reasons = list(risk["reasons"])
    add_fact(
        session,
        episode.id,
        category="risk_input",
        label="Demo prioritisation category",
        value={"text": risk["category"], "reasons": risk["reasons"], "inputs": risk["inputs"],
               "disclaimer": risk["disclaimer"]},
        source_kind=FactSourceKind.INFERRED,
        source_ref="rules:risk_v1",
        extraction_method="rules",
    )
    notes.append(risk["disclaimer"])
    episode.extraction_method = method
    episode.extraction_notes = notes
    session.flush()
    record_event(
        session,
        "extraction.completed",
        f"Discharge summary read ({method}): {sum(found.values())} facts, "
        f"{sum(1 for c in REQUIRED if not found.get(c))} not provided",
        episode_id=episode.id,
        actor_type="agent",
        actor_label="Ninety-Six agent",
        detail={"method": method, "counts": found, "notes": notes, "risk": risk},
        scopes=["episode", "memory", "episodes", "overview"],
    )
