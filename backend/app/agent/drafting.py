"""AI reply drafting + translation (hybrid, rules first).

Only CONCERN / RED-FLAG replies (concern_reply_v1, urgent_safety_v1) are AI-drafted, and messages
to a non-English patient (welcome_v1 + those replies) are AI-translated. Everything else stays a
fixed template. Every draft is still an ordinary ProposedAction: it goes through the doctor's
approval gate exactly like the template would (priority unchanged), with the English original
kept in the approval card (`body_en`).

How it stays out of transactions: `prewarm()` calls the LLM BEFORE the caller's write transaction
and stores the vetted draft in memory, keyed by a hash of (template, check-in, English template
text, language). `overlay()` is network-free and is used by plan_store.render_action_fields, so a
re-render returns the same payload (same hash). A cache miss (e.g. after a restart) simply yields
the template, which is the safe fallback.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from .. import llm
from ..config import settings
from ..llm import router

_store: dict[str, dict[str, Any]] = {}
MAX_SMS = 900
DOSE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ml|units?|iu|tablets?)\b", re.I)
FORBIDDEN = ("triage", "red flag", "red-flag", "risk level", "risk score")


class DraftOut(BaseModel):
    body_en: str = Field(description="The English message")
    body_translated: Optional[str] = Field(None, description="Faithful translation, or null if English")


DRAFT_SYSTEM = (
    "You write one short SMS from a hospital care team to a patient after their post-discharge check-in, for a "
    "care-coordination prototype (fictional data). Start from the APPROVED TEMPLATE: keep every safety instruction "
    "and every phone number exactly as written. You may make it warmer and briefly acknowledge what the patient wrote, "
    "in plain words. Never give medical advice, diagnoses, doses, or tell them to change medicines beyond what the "
    "template says. Never mention triage, risk levels or red flags. At most 480 characters. "
    "If target_language is not English, also return body_translated: a faithful translation of body_en into that "
    "language, keeping numbers and phone numbers unchanged; otherwise body_translated is null."
)
TRANSLATE_SYSTEM = (
    "You translate one SMS from a hospital care team to a patient (fictional data) into target_language. "
    "Translate faithfully: do not add, remove or soften anything; keep every number, phone number, link and medicine "
    "name unchanged. Return body_en exactly equal to the input text and body_translated as the translation."
)


def clear() -> None:
    _store.clear()


def _key(template_id: str, checkin_id: Optional[int], english: str, language: str) -> str:
    raw = "\x1f".join([template_id, str(checkin_id or ""), english, (language or "English").strip().lower()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def overlay(template_id: Optional[str], payload: dict[str, Any], *, language: Optional[str],
            checkin_id: Optional[int]) -> dict[str, Any]:
    """Network-free: returns the vetted AI draft for exactly this message, else the template payload."""
    if not template_id or "body" not in payload:
        return payload
    hit = _store.get(_key(template_id, checkin_id, payload["body"], language or "English"))
    return dict(hit) if hit else payload


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def vet(text: str, template_body: str, ctx: dict[str, Any]) -> Optional[str]:
    """Deterministic guard on any AI-written patient text. Returns a reason to reject, or None."""
    if not text or not text.strip():
        return "empty"
    if len(text) > MAX_SMS:
        return "too long"
    low = text.lower()
    if any(w in low for w in FORBIDDEN):
        return "mentions triage / risk wording"
    if DOSE_RE.search(text) and not DOSE_RE.search(template_body):
        return "adds a dose"
    digits = _digits(text)
    for must in (ctx.get("emergency"), ctx.get("hospital_phone")):
        if must and _digits(str(must)) and _digits(str(must)) in _digits(template_body) and _digits(str(must)) not in digits:
            return f"drops the number {must}"
    return None


def prewarm(episode_id: int, template_ids: list[str], *, checkin_id: Optional[int] = None,
            patient_words: Optional[str] = None, confirmed_only: bool = True) -> list[tuple[router.Route, Optional[llm.LLMResult]]]:
    """Call BEFORE any write transaction. Returns (route, result) pairs for the activity trace."""
    from .. import db
    from ..models import DischargeEpisode, Patient
    from . import message_templates as mt

    with db.SessionLocal() as s:  # short read-only session; closed before the network call
        ep = s.get(DischargeEpisode, episode_id)
        if ep is None:
            return []
        p = s.get(Patient, ep.patient_id)
        language = (p.preferred_language if p else None) or "English"
        ctx = mt.build_context(s, ep, confirmed_only=confirmed_only, extra={"checkin_title": "check-in"})
        bodies = {t: mt.render(t, ctx)["body"] for t in template_ids}
    out = []
    for tid in template_ids:
        route = router.decide("reply_draft", {"template_id": tid, "language": language})
        if not route.use:
            out.append((route, None))
            continue
        english = bodies[tid]
        k = _key(tid, checkin_id, english, language)
        draft, translate = route.extra.get("draft"), route.extra.get("translate")
        if draft:
            user = json.dumps({"approved_template": english, "patient_wrote": patient_words or "(nothing)",
                               "target_language": language if translate else "English"}, ensure_ascii=False)
            res = llm.structured(DRAFT_SYSTEM, user, DraftOut, purpose="reply_draft")
        else:
            user = json.dumps({"text": english, "target_language": language}, ensure_ascii=False)
            res = llm.structured(TRANSLATE_SYSTEM, user, DraftOut, purpose="translate")
        if res.ok and res.data is not None:
            body_en = res.data.body_en.strip() if draft else english
            final = (res.data.body_translated or "").strip() if translate else body_en
            why = vet(body_en, english, ctx) or (vet(final, english, ctx) if translate else None)
            if translate and not final:
                why = "no translation returned"
            if why:
                res = llm.LLMResult(False, res.provider, res.model, error=f"draft rejected: {why}", ms=res.ms)
            else:
                payload: dict[str, Any] = {"body": final, "drafted_by": "ai", "ai_model": res.model or llm.model_name(),
                                           "ai_task": "draft+translate" if draft and translate else ("draft" if draft else "translate")}
                if translate:
                    payload["body_en"] = body_en
                    payload["language"] = language
                _store[k] = payload
        out.append((route, res))
    return out


def trace_all(session, episode_id: int, pairs: list) -> None:  # noqa: ANN001
    for route, res in pairs:
        router.trace(session, episode_id, route, res, scopes=["approvals"])


__all__ = ["prewarm", "overlay", "vet", "trace_all", "clear", "settings"]
