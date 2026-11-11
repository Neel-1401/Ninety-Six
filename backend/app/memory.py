"""Patient memory: durable facts the agent writes and re-reads across steps.
Reads are logged so the UI can show memory being used."""
from sqlmodel import Session, select

from .events import log_event
from .models import MemoryFact


def remember(session: Session, patient_id: int, key: str, value: str, source: str) -> MemoryFact:
    fact = MemoryFact(patient_id=patient_id, key=key, value=value, source=source)
    session.add(fact)
    session.commit()
    session.refresh(fact)
    log_event(session, "memory", f"Remembered {key}", patient_id, {"key": key, "value": value, "source": source})
    return fact


def recall(session: Session, patient_id: int, purpose: str, log: bool = True) -> list[MemoryFact]:
    facts = session.exec(
        select(MemoryFact).where(MemoryFact.patient_id == patient_id).order_by(MemoryFact.id)
    ).all()
    if log and facts:
        log_event(
            session,
            "memory",
            f"Recalled {len(facts)} facts for {purpose}",
            patient_id,
            {"purpose": purpose, "keys": sorted({f.key for f in facts})},
        )
    return list(facts)


def facts_as_text(facts: list[MemoryFact]) -> str:
    return "\n".join(f"- {f.key}: {f.value}" for f in facts) or "- (no facts yet)"
