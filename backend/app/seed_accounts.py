"""Provisioned staff accounts (CONTRACTS.md §11). Staff cannot self-register or pick roles.

Demo mode: seeded with the published demo password `demo1234`.
Production mode: seeded only if SEED_STAFF_PASSWORD is set; otherwise nothing is created.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth.security import hash_secret
from .config import settings
from .models import Role, User

log = logging.getLogger("ninety_six.seed")

DEMO_PASSWORD = "demo1234"

STAFF = [
    {"username": "dr.mehta", "display_name": "Dr. Arjun Mehta", "role": Role.DOCTOR, "specialty": "Cardiology"},
    {"username": "dr.iyer", "display_name": "Dr. Kavitha Iyer", "role": Role.DOCTOR, "specialty": "General Surgery"},
    {"username": "nurse.asha", "display_name": "Asha Nair", "role": Role.NURSE, "specialty": "Discharge coordinator"},
    {"username": "nurse.ravi", "display_name": "Ravi Kumar", "role": Role.NURSE, "specialty": "Ward nurse"},
]


def seed_staff(session: Session) -> int:
    """Idempotent. Returns the number of accounts created (flushes; caller commits)."""
    if settings.demo_mode:
        password = DEMO_PASSWORD
    else:
        password = os.getenv("SEED_STAFF_PASSWORD", "")
        if not password:
            log.warning("Production mode without SEED_STAFF_PASSWORD: no staff accounts seeded.")
            return 0
    created = 0
    pw_hash = None
    for spec in STAFF:
        if session.scalar(select(User).where(User.username == spec["username"])) is not None:
            continue
        pw_hash = pw_hash or hash_secret(password)
        session.add(
            User(
                username=spec["username"],
                display_name=spec["display_name"],
                role=spec["role"],
                specialty=spec["specialty"],
                password_hash=pw_hash,
                is_active=True,
                is_demo_account=settings.demo_mode,
            )
        )
        created += 1
    session.flush()
    return created
