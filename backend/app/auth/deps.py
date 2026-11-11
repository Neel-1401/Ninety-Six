"""FastAPI dependencies and server-side permission checks (CONTRACTS.md §2).

Order of checks for episode-scoped staff routes:
  1. authenticated (401)  2. staff role (403 for patients)  3. assigned to the episode (404)
  4. role-specific capability, e.g. doctor-only (403)
Patients: authenticated → patient role (403) → own episode (404).
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import errors
from ..db import get_session
from ..models import DischargeEpisode, Role, StaffAssignment
from .sessions import COOKIE_NAME, Actor, resolve


def get_db(session: Session = Depends(get_session)) -> Session:
    return session


def optional_actor(request: Request, session: Session = Depends(get_session)) -> Optional[Actor]:
    return resolve(session, request.cookies.get(COOKIE_NAME))


def current_actor(actor: Optional[Actor] = Depends(optional_actor)) -> Actor:
    if actor is None:
        raise errors.unauthenticated()
    return actor


def require_staff(actor: Actor = Depends(current_actor)) -> Actor:
    if not actor.is_staff:
        raise errors.forbidden("This area is for hospital staff.")
    return actor


def require_patient(actor: Actor = Depends(current_actor)) -> Actor:
    if not actor.is_patient:
        raise errors.forbidden("This area is for patients.")
    return actor


def require_nurse(actor: Actor = Depends(require_staff)) -> Actor:
    if actor.role != Role.NURSE:
        raise errors.forbidden("Only a nurse can do this.")
    return actor


def ensure_doctor(actor: Actor, what: str = "this") -> None:
    if actor.role != Role.DOCTOR:
        raise errors.forbidden(f"Only the assigned doctor can {what}.")


def ensure_nurse(actor: Actor, what: str = "this") -> None:
    if actor.role != Role.NURSE:
        raise errors.forbidden(f"Only a nurse can {what}.")


def assigned_episode_ids(session: Session, actor: Actor) -> list[int]:
    return list(
        session.scalars(
            select(StaffAssignment.episode_id).where(
                StaffAssignment.user_id == actor.id, StaffAssignment.active.is_(True)
            )
        ).all()
    )


def is_assigned(session: Session, actor: Actor, episode_id: int) -> bool:
    return (
        session.scalar(
            select(StaffAssignment.id).where(
                StaffAssignment.episode_id == episode_id,
                StaffAssignment.user_id == actor.id,
                StaffAssignment.active.is_(True),
            )
        )
        is not None
    )


def episode_for_staff(session: Session, actor: Actor, episode_id: int) -> DischargeEpisode:
    """404 unless the staff member has an ACTIVE assignment on the episode."""
    if not actor.is_staff:
        raise errors.forbidden("This area is for hospital staff.")
    ep = session.get(DischargeEpisode, episode_id)
    if ep is None or not is_assigned(session, actor, episode_id):
        raise errors.not_found("Episode not found.")
    return ep


def patient_episode_ids(session: Session, actor: Actor) -> list[int]:
    if not actor.is_patient or actor.user.patient_id is None:
        return []
    return list(
        session.scalars(
            select(DischargeEpisode.id).where(DischargeEpisode.patient_id == actor.user.patient_id)
        ).all()
    )


def episode_for_patient(session: Session, actor: Actor, episode_id: int) -> DischargeEpisode:
    """404 unless the episode belongs to the signed-in patient."""
    if not actor.is_patient:
        raise errors.forbidden("This area is for patients.")
    ep = session.get(DischargeEpisode, episode_id)
    if ep is None or actor.user.patient_id is None or ep.patient_id != actor.user.patient_id:
        raise errors.not_found("Not found.")
    return ep


def visible_episode_ids(session: Session, actor: Actor) -> set[int]:
    return set(patient_episode_ids(session, actor) if actor.is_patient else assigned_episode_ids(session, actor))
