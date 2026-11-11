"""Episode memory helpers (Agent B): facts with provenance + a log of which facts informed which
decision (FactUsage). No hidden chain-of-thought is stored, only decision summaries."""
from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FactReviewState, FactUsage, PatientFact

PATIENT_VISIBLE_CATEGORIES = {"diagnosis", "medication", "allergy", "follow_up", "red_flag", "instruction"}
CLINICAL_CATEGORIES = PATIENT_VISIBLE_CATEGORIES | {"social"}


def active_facts(session: Session, episode_id: int, categories: Optional[Iterable[str]] = None) -> list[PatientFact]:
    q = select(PatientFact).where(PatientFact.episode_id == episode_id, PatientFact.active.is_(True))
    if categories is not None:
        q = q.where(PatientFact.category.in_(list(categories)))
    return list(session.scalars(q.order_by(PatientFact.id)))


def confirmed_facts(session: Session, episode_id: int, category: str) -> list[PatientFact]:
    return [
        f
        for f in active_facts(session, episode_id, [category])
        if f.review_state == FactReviewState.CONFIRMED and not f.is_missing
    ]


def fact_text(f: PatientFact) -> str:
    v = f.value or {}
    if f.category == "medication":
        parts = [v.get("name", f.label)]
        for k in ("dose", "frequency"):
            if v.get(k) and v.get(k) != "not provided":
                parts.append(v[k])
        if v.get("instructions"):
            parts.append(f"({v['instructions']})")
        return " ".join(parts)
    if f.category == "follow_up":
        t = v.get("title", f.label)
        if v.get("within_days"):
            t += f" within {v['within_days']} days"
        if v.get("provider"):
            t += f" ({v['provider']})"
        return t
    return str(v.get("text", f.label))


def add_fact(
    session: Session,
    episode_id: int,
    *,
    category: str,
    label: str,
    value: dict[str, Any],
    source_kind: str,
    source_ref: Optional[str] = None,
    source_excerpt: Optional[str] = None,
    extraction_method: Optional[str] = None,
    is_missing: bool = False,
    created_by: str = "agent",
    supersedes_id: Optional[int] = None,
) -> PatientFact:
    f = PatientFact(
        episode_id=episode_id,
        category=category,
        label=label[:160],
        value=dict(value),
        source_kind=source_kind,
        source_ref=source_ref,
        source_excerpt=source_excerpt,
        extraction_method=extraction_method,
        is_missing=is_missing,
        created_by=created_by,
        supersedes_id=supersedes_id,
    )
    session.add(f)
    session.flush()
    return f


def log_usage(
    session: Session,
    episode_id: int,
    facts: Iterable[PatientFact | int],
    *,
    used_for: str,
    ref_type: str,
    ref_id: Optional[int],
    note: Optional[str] = None,
) -> int:
    n = 0
    seen: set[int] = set()
    for f in facts:
        fid = f if isinstance(f, int) else f.id
        if fid is None or fid in seen:
            continue
        seen.add(fid)
        session.add(
            FactUsage(episode_id=episode_id, fact_id=fid, used_for=used_for, ref_type=ref_type, ref_id=ref_id, note=note)
        )
        n += 1
    session.flush()
    return n
