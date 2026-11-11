"""Hybrid routing: rules first, the LLM only where it adds value (one place decides).

`should_use_llm(task, context) -> bool` is the single gate every caller goes through. The reason
for every decision is logged, and `trace()` writes it to the episode's activity trace as
"AI used: <task> (<model>, <ms> ms)" / "Rules used: <reason>" / "AI failed -> rules fallback".

Rule-only tasks never reach the LLM: scheduling/timing, templated check-ins and reminders, the
risk score, approval gates, the interaction table + openFDA, and red-flag keyword matches.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import LLMResult, is_configured, model_name, provider_name

log = logging.getLogger("ninety_six.llm.router")

RULE_ONLY = {
    "scheduling": "timing comes from the deterministic schedule",
    "checkin_template": "templated check-ins are reviewed, fixed text",
    "reminder": "templated reminders are pre-approved, fixed text",
    "risk": "risk score is a transparent rule table",
    "approval": "approval gates are hospital policy, never model output",
    "interaction_check": "interaction table + openFDA are the source of truth",
    "red_flag_keywords": "red-flag keyword match is deterministic",
}
AI_TASKS = {"extraction", "planning", "triage", "reply_draft", "translate", "summary"}
LABELS = {"extraction": "discharge-summary parsing", "planning": "condition-specific plan steps",
          "triage": "check-in triage", "reply_draft": "reply draft", "translate": "translation",
          "summary": "96-hour clinical note"}

# Short replies that rules understand without help ("All good", "fine thanks", ...)
KNOWN_OK = ["all good", "all fine", "fine", "good", "ok", "okay", "no problem", "no problems", "no issues",
            "nothing", "none", "better", "feeling better", "feeling good", "doing well", "well", "thanks", "thank you"]
FEW_WORDS = 4
REPLY_TEMPLATES = {"concern_reply_v1", "urgent_safety_v1"}
TRANSLATABLE = {"welcome_v1", "concern_reply_v1", "urgent_safety_v1"}


@dataclass
class Route:
    task: str
    use: bool
    reason: str
    extra: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:  # `if decide(...):` reads naturally
        return self.use


def is_english(language: Optional[str]) -> bool:
    return (language or "English").strip().lower() in {"english", "en", "en-in", "en-gb", "en-us"}


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text or "", flags=re.UNICODE)


def known_pattern(text: str, keyword_lists: list[list[str]]) -> bool:
    t = " ".join(_words(text.lower()))
    if not t:
        return True
    if any(t == k or t.startswith(k + " ") or t.endswith(" " + k) for k in KNOWN_OK):
        return True
    return any(k.strip() and k.strip() in t for kws in keyword_lists for k in kws)


def decide(task: str, context: Optional[dict[str, Any]] = None) -> Route:
    ctx = context or {}
    if task in RULE_ONLY:
        r = Route(task, False, f"rule-only task: {RULE_ONLY[task]}")
    elif task not in AI_TASKS:
        r = Route(task, False, f"unknown task '{task}': rules")
    elif not is_configured():
        r = Route(task, False, "LLM off (LLM_PROVIDER=mock, no key, or switched off in Settings)")
    else:
        r = _decide_ai(task, ctx)
    log.info("route task=%s use_llm=%s reason=%s", task, r.use, r.reason)
    return r


def should_use_llm(task: str, context: Optional[dict[str, Any]] = None) -> bool:
    return decide(task, context).use


def _decide_ai(task: str, ctx: dict[str, Any]) -> Route:
    if task == "extraction":
        items = (ctx.get("rules") or {}).get("items", [])
        cats = {i["category"] for i in items}
        weak = [label for cat, label in (("medication", "no medicines"), ("diagnosis", "no diagnosis"),
                                         ("red_flag", "no red flags")) if cat not in cats]
        if not ctx.get("sections_found", True):
            weak.insert(0, "summary is not in the expected section format")
        if weak:
            return Route(task, True, "regex result is weak: " + ", ".join(weak))
        return Route(task, False, f"regex parser found medicines, diagnosis and red flags ({len(items)} items)")
    if task == "planning":
        diag = (ctx.get("diagnosis") or "").strip()
        if not diag:
            return Route(task, False, "no diagnosis to tailor steps to: rule plan only")
        return Route(task, True, f"rule plan is the base; AI may add steps specific to '{diag[:60]}'")
    if task == "triage":
        if ctx.get("rules_level") == "urgent":
            return Route(task, False, "rules found a red flag: kept as RED FLAG, AI never consulted")
        text = (ctx.get("free_text") or "").strip()
        if not text:
            return Route(task, False, "structured answers only (simple check-in)")
        n = len(_words(text))
        if n > FEW_WORDS:
            return Route(task, True, f"free-text reply is {n} words: AI reads it (can only raise the level)")
        if not known_pattern(text, ctx.get("keyword_lists") or []):
            return Route(task, True, "short reply matches no known pattern: AI reads it (can only raise the level)")
        return Route(task, False, "short reply matches a known pattern")
    if task in ("reply_draft", "translate"):
        tpl, lang = ctx.get("template_id"), ctx.get("language")
        drafting = tpl in REPLY_TEMPLATES
        translating = not is_english(lang) and tpl in TRANSLATABLE
        if drafting or translating:
            what = " + ".join(x for x in (["draft"] if drafting else []) + ([f"translate to {lang}"] if translating else []))
            return Route(task, True, f"{tpl}: {what} (still needs doctor approval)",
                         {"draft": drafting, "translate": translating})
        return Route(task, False, f"{tpl}: templated message")
    if task == "summary":
        return Route(task, True, "AI writes the clinical note; template summary kept as fallback")
    return Route(task, False, "rules")


# ----------------------------------------------------------------------------- activity trace
def trace_title(route: Route, result: Optional[LLMResult] = None) -> tuple[str, str]:
    label = LABELS.get(route.task, route.task)
    if not route.use:
        return "ai.rules", f"Rules used: {label} - {route.reason}"
    if result is not None and result.ok:
        extra = ", cached" if result.cached else ""
        return "ai.used", f"AI used: {label} ({result.model or model_name()}, {result.ms} ms{extra})"
    err = result.error if result is not None else "not called"
    return "ai.fallback", f"AI failed -> rules fallback: {label} ({err})"


def trace(session, episode_id: Optional[int], route: Route, result: Optional[LLMResult] = None,  # noqa: ANN001
          *, note: Optional[str] = None, scopes: Optional[list[str]] = None) -> None:
    """Record the routing decision in the caller's (write) transaction."""
    from ..events import record_event

    kind, title = trace_title(route, result)
    detail = {"task": route.task, "use_llm": route.use, "reason": route.reason,
              "provider": provider_name(), "model": (result.model if result else None) or model_name(),
              "ok": bool(result and result.ok), "ms": result.ms if result else 0,
              "cached": bool(result and result.cached), "error": result.error if result and not result.ok else None,
              "attempts": result.attempts if result else 0}
    if note:
        detail["note"] = note
    record_event(session, kind, title, episode_id=episode_id, actor_type="agent", actor_label="AI router",
                 detail=detail, scopes=scopes or [])


def tag(route: Route, result: Optional[LLMResult] = None) -> dict[str, Any]:
    """Compact provenance stored next to an output (for the UI's AI / Rules tag)."""
    used = bool(route.use and result is not None and result.ok)
    return {"by": "ai" if used else "rules", "task": route.task,
            "model": (result.model if used else None), "ms": (result.ms if used else 0),
            "reason": route.reason if not route.use else (None if used else f"AI failed: {result.error if result else '-'}")}
