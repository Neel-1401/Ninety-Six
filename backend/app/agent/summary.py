"""96-hour episode summary (Agent B). Factual record of what happened; never a clearance."""
from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import clock, iso
from ..models import (
    CheckIn,
    DischargeEpisode,
    FollowUpEvent,
    Message,
    PlanStep,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
)
from .memory import active_facts, log_usage
from .plan_store import current_plan, steps_of

STATEMENT = "The 96-hour monitoring window has ended. This is not a medical clearance."


def build_summary(session: Session, episode: DischargeEpisode) -> dict[str, Any]:
    pv = current_plan(session, episode)
    steps: list[PlanStep] = steps_of(session, pv) if pv else []
    completed = [{"step_key": s.step_key, "title": s.title, "executed_at": iso(s.executed_at)}
                 for s in steps if s.status == "executed"]
    missed = [{"step_key": s.step_key, "title": s.title, "status": s.status, "reason": s.result_summary}
              for s in steps if s.status in ("skipped", "failed", "unknown", "cancelled", "awaiting_approval", "planned", "blocked", "ready")]
    checkins = session.scalars(select(CheckIn).where(CheckIn.episode_id == episode.id).order_by(CheckIn.due_at)).all()
    reviews = session.scalars(select(ReviewItem).where(ReviewItem.episode_id == episode.id)).all()
    msgs = session.scalars(select(Message).where(Message.episode_id == episode.id, Message.kind != "otp")).all()
    fus = session.scalars(select(FollowUpEvent).where(FollowUpEvent.episode_id == episode.id)).all()
    actions = session.scalars(select(ProposedAction).where(ProposedAction.episode_id == episode.id)).all()
    facts = active_facts(session, episode.id)
    return {
        "generated_at": iso(clock.now()),
        "window": {"start": iso(episode.discharge_at), "end": iso(episode.window_ends_at)},
        "completed_steps": completed,
        "missed_or_skipped": missed,
        "checkins": [
            {"id": c.id, "title": c.title, "due_at": iso(c.due_at), "status": c.status,
             "submitted_at": iso(c.submitted_at), "triage_level": c.triage_level, "reasons": list(c.triage_reasons or []),
             "staff_review_state": c.staff_review_state}
            for c in checkins
        ],
        "escalations": [
            {"id": r.id, "title": r.title, "priority": r.priority, "status": r.status, "created_at": iso(r.created_at)}
            for r in reviews if r.kind in ("urgent_concern", "concern")
        ],
        "unresolved_review_items": [
            {"id": r.id, "kind": r.kind, "title": r.title, "priority": r.priority, "status": r.status}
            for r in reviews if r.status != ReviewStatus.RESOLVED and r.kind != "closure_review"
        ],
        "messages": {"by_status": dict(Counter(m.status for m in msgs)), "total": len(msgs)},
        "actions": {"by_approval_state": dict(Counter(a.approval_state for a in actions)),
                    "by_execution_state": dict(Counter(a.execution_state for a in actions))},
        "followups": [{"title": f.title, "status": f.status, "start_at": iso(f.start_at)} for f in fus],
        "facts_used": [f.id for f in facts],
        "statement": STATEMENT,
    }


def write_summary(session: Session, episode: DischargeEpisode) -> dict[str, Any]:
    s = build_summary(session, episode)
    from ..models import Patient

    p = session.get(Patient, episode.patient_id)
    s["clinical_note"] = {"text": template_note(s, p.full_name if p else "The patient"), "open_issues": [],
                          "by": "rules", "model": None, "ms": 0}
    episode.summary = s
    log_usage(session, episode.id, s["facts_used"], used_for="summary", ref_type="summary", ref_id=episode.id,
              note="96-hour summary")
    return s


# ----------------------------------------------------------------------------- clinical note
def template_note(s: dict[str, Any], patient_label: str = "The patient") -> str:
    cis = s.get("checkins", [])
    done = [c for c in cis if c.get("submitted_at")]
    levels = Counter(c.get("triage_level") or "unprocessed" for c in done)
    lines = [
        f"96-hour post-discharge summary for {patient_label}.",
        f"Check-ins: {len(done)} of {len(cis)} completed"
        + (f" ({', '.join(f'{v} {k}' for k, v in levels.items())})." if done else "."),
        f"Escalations: {len(s.get('escalations', []))}; unresolved review items: {len(s.get('unresolved_review_items', []))}.",
        f"Plan steps completed: {len(s.get('completed_steps', []))}; missed or skipped: {len(s.get('missed_or_skipped', []))}.",
        f"Follow-ups: {', '.join(f['title'] + ' (' + f['status'] + ')' for f in s.get('followups', [])) or 'none recorded'}.",
        STATEMENT,
    ]
    return " ".join(lines)


class ClinicalNote(BaseModel):
    note: str = Field(description="Concise clinical handover note, 80-180 words, plain text")
    open_issues: list[str] = Field(default_factory=list, description="Unresolved items the doctor should act on")


NOTE_SYSTEM = (
    "You write a concise clinical handover note (80-180 words, plain text) for the discharging doctor at the end of a "
    "96-hour post-discharge monitoring window, from the structured record provided (fictional data). Use only facts in "
    "the record; do not invent symptoms, results, diagnoses or doses; do not give treatment advice. Mention check-in "
    "outcomes, escalations and how they were handled, follow-up bookings, and anything still unresolved. Do not say the "
    "patient is cleared."
)


def ai_clinical_note(episode_id: int) -> None:
    """Runs OUTSIDE any transaction after the summary is saved. Upgrades the template note to an
    AI-written note if the router allows and the call succeeds; otherwise the template stays."""
    import json

    from .. import db, llm
    from ..llm import router

    route = router.decide("summary")
    res = None
    if route.use:
        with db.SessionLocal() as s:
            ep = s.get(DischargeEpisode, episode_id)
            record = dict(ep.summary or {}) if ep else {}
            facts = [{"category": f.category, "text": (f.value or {}).get("text") or f.label}
                     for f in active_facts(s, episode_id) if f.category in ("diagnosis", "medication", "red_flag", "symptom", "adherence")]
        record.pop("clinical_note", None)
        res = llm.structured(NOTE_SYSTEM, json.dumps({"record": record, "facts": facts}, default=str)[:12000],
                             ClinicalNote, purpose="summary")
        if res.ok and res.data is not None and not res.data.note.strip():
            res = llm.LLMResult(False, res.provider, res.model, error="empty note", ms=res.ms)
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, episode_id)
        if ep is None or not ep.summary:
            return
        if res is not None and res.ok:
            text = res.data.note.strip()[:2500]
            if "clearance" not in text.lower():
                text += " " + STATEMENT
            ep.summary = {**dict(ep.summary), "clinical_note": {
                "text": text, "open_issues": [x[:200] for x in res.data.open_issues[:8]],
                "by": "ai", "model": res.model, "ms": res.ms, "fallback": dict(ep.summary).get("clinical_note")}}
        router.trace(s, episode_id, route, res, scopes=["summary"])
