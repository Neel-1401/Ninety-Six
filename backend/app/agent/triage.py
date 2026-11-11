"""Check-in triage (Agent B).

Deterministic rules decide from the reviewed template's answer mapping, the episode's discharge
red flags (memory) and keyword sets on free text. An LLM (if configured) may only RAISE the level,
never lower it. Triage output is staff-only; patients never see levels or reasons.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel

from .. import llm
from .checkin_templates import rules_for

RANK = {"ok": 0, "concern": 1, "urgent": 2}

GENERAL_URGENT = [
    "chest pain", "chest pressure", "can't breathe", "cannot breathe", "breathless", "short of breath",
    "shortness of breath", "fainted", "faint", "passed out", "unconscious", "confused", "confusion",
    "vomiting blood", "blood in", "seizure", "fit ", "slurred", "weakness on one side", "high fever",
    "severe pain", "throwing up", "keep vomiting",
]
GENERAL_CONCERN = [
    "ran out", "run out", "missed", "forgot", "can't afford", "cannot afford", "side effect", "dizzy", "nausea",
    "headache", "not sure", "which tablet", "rash", "itch", "constipat", "can't sleep", "tired", "swelling",
    "swollen", "puffy", "worried", "worse", "pain",
]
STOP = {"more", "than", "when", "with", "above", "rest", "from", "your", "have", "that", "this"}


def _phrases(text: str) -> list[str]:
    out = []
    for part in re.split(r",| or |/|;", text.lower()):
        words = [w for w in re.findall(r"[a-z]+", part) if len(w) > 3 and w not in STOP]
        if words:
            out.append(" ".join(words[:2]))
    return out


def rules_triage(
    template_id: Optional[str],
    answers: dict[str, Any],
    free_text: Optional[str],
    red_flag_facts: list[tuple[int, str]],
) -> dict[str, Any]:
    level, reasons, matched = "ok", [], []
    rules = rules_for(template_id) if template_id else {}
    for (qid, val), (lvl, why) in rules.items():
        if answers.get(qid) == val:
            reasons.append(why)
            if RANK[lvl] > RANK[level]:
                level = lvl
    t = (free_text or "").lower()
    if t.strip():
        for fid, text in red_flag_facts:
            if any(all(w in t for w in ph.split()) for ph in _phrases(text)):
                matched.append(fid)
                reasons.append(f"Free text matches discharge warning sign: {text}")
                level = "urgent"
        hits = [w.strip() for w in GENERAL_URGENT if w in t]
        if hits:
            reasons.append("Free text mentions: " + ", ".join(sorted(set(hits))))
            level = "urgent"
        elif level == "ok":
            c = [w for w in GENERAL_CONCERN if w in t]
            if c:
                reasons.append("Free text mentions: " + ", ".join(sorted(set(c))))
                level = "concern"
    # answers that correspond to red-flag facts: link the facts for memory traceability
    if level == "urgent" and not matched:
        for fid, text in red_flag_facts:
            ph = _phrases(text)
            if any(any(w in r.lower() for w in p.split()) for r in reasons for p in ph):
                matched.append(fid)
    return {"level": level, "reasons": reasons, "matched_fact_ids": sorted(set(matched))}


class LLMTriage(BaseModel):
    level: Literal["ok", "concern", "urgent"]
    reasons: list[str]


TRIAGE_SYSTEM = (
    "You review a patient's post-discharge check-in for a care-coordination prototype. Classify it: "
    "'urgent' if it matches one of the discharge warning signs or needs a clinician today, 'concern' if a "
    "nurse should follow up, 'ok' otherwise. Do not diagnose or give advice. Give short factual reasons."
)


def llm_triage(context: str, result_out: Optional[list] = None) -> Optional[dict[str, Any]]:
    if not llm.is_configured():
        return None
    res = llm.structured(TRIAGE_SYSTEM, context, LLMTriage, purpose="triage")
    if result_out is not None:
        result_out.append(res)
    if not res.ok or res.data is None:
        return {"error": res.error, "provider": res.provider}
    return {"level": res.data.level, "reasons": list(res.data.reasons), "provider": res.provider}


def route_context(rules: dict[str, Any], free_text: Optional[str]) -> dict[str, Any]:
    return {"rules_level": rules["level"], "free_text": free_text,
            "keyword_lists": [GENERAL_URGENT, GENERAL_CONCERN]}


def combine(rules: dict[str, Any], llm_out: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Final level = max(rules, LLM). The LLM can raise the rules' level, never lower it."""
    out = dict(rules)
    out["source"] = "rules"
    if llm_out and "level" in llm_out:
        out["source"] = f"llm:{llm_out['provider']}+rules"
        if RANK[llm_out["level"]] > RANK[rules["level"]]:
            out["level"] = llm_out["level"]
            out["reasons"] = list(rules["reasons"]) + [f"Language model raised level: {r}" for r in llm_out["reasons"]]
    elif llm_out and llm_out.get("error"):
        out["source"] = "rules (LLM unavailable)"
    return out
