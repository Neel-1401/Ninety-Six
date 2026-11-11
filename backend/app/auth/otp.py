"""Patient invitation + one-time-code sign-in (CONTRACTS.md §3).

Security timers use REAL time. Responses never reveal whether a phone is registered: unregistered
phones get a challenge row too (so attempts/cooldown behave identically) but no SMS is sent.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import errors
from ..clock import clock
from ..config import settings
from ..events import record_event
from ..models import (
    DischargeEpisode,
    EpisodeStatus,
    Invitation,
    Message,
    OtpChallenge,
    Patient,
    Role,
    User,
)
from . import ratelimit
from .notify import send_operational_sms
from .security import hash_secret, new_otp_code, new_token, normalize_phone, sha256_hex, verify_secret

GENERIC_SENT = "If this number is registered with us, we've sent a code."
INVALID_CODE = "That code didn't work. Check it, or request a new code."
_CLOSED = (EpisodeStatus.CANCELLED,)


def invalid_code() -> errors.ApiError:
    return errors.ApiError(400, "invalid_code", INVALID_CODE)


def _active_invitation(session: Session, phone: str, token: Optional[str]) -> Optional[Invitation]:
    now = clock.real_now()
    q = (
        select(Invitation)
        .join(DischargeEpisode, DischargeEpisode.id == Invitation.episode_id)
        .where(
            Invitation.phone_e164 == phone,
            Invitation.status.in_(("pending", "accepted")),
            Invitation.expires_at > now,
            DischargeEpisode.status.notin_(_CLOSED),
        )
        .order_by(Invitation.created_at.desc())
    )
    if token:
        q = q.where(Invitation.token_hash == sha256_hex(token))
    return session.scalars(q).first()


def ensure_patient_user(session: Session, patient: Patient) -> User:
    user = session.scalar(select(User).where(User.role == Role.PATIENT, User.patient_id == patient.id))
    if user is None:
        user = User(
            username=None,
            display_name=patient.full_name,
            role=Role.PATIENT,
            phone_e164=patient.phone_e164,
            patient_id=patient.id,
            is_active=True,
            is_demo_account=settings.demo_mode,
        )
        session.add(user)
        session.flush()
    elif user.phone_e164 != patient.phone_e164:
        user.phone_e164 = patient.phone_e164
    return user


# --------------------------------------------------------------------------- invitation
def create_invitation(session: Session, episode: DischargeEpisode, *, actor) -> tuple[Invitation, str, Message]:  # noqa: ANN001
    """Revoke older pending invitations, create a new one and send the invitation SMS.
    COMMITS (the SMS is sent between two commits, never inside an open transaction)."""
    patient = session.get(Patient, episode.patient_id)
    if episode.status in (EpisodeStatus.CANCELLED, EpisodeStatus.CLOSED):
        raise errors.invalid_state("This episode is closed; invitations can't be sent.")
    phone = normalize_phone(patient.phone_e164)
    if not phone:
        raise errors.validation("The patient's phone number is not valid.")
    now = clock.real_now()
    session.execute(
        update(Invitation)
        .where(Invitation.episode_id == episode.id, Invitation.status == "pending")
        .values(status="revoked")
    )
    token = new_token(24)
    inv = Invitation(
        episode_id=episode.id,
        patient_id=patient.id,
        phone_e164=phone,
        token_hash=sha256_hex(token),
        status="pending",
        created_by_id=actor.id,
        created_at=now,
        expires_at=now + timedelta(hours=settings.invitation_ttl_hours),
    )
    session.add(inv)
    ensure_patient_user(session, patient)
    session.flush()
    record_event(
        session,
        "invite.sent",
        f"Invitation sent to {patient.full_name}",
        episode_id=episode.id,
        actor_type="user",
        actor_id=actor.id,
        actor_label=actor.label,
        detail={"invitation_id": inv.id},
        scopes=["episode", "messages"],
    )
    session.commit()

    link = f"{settings.public_base_url.rstrip('/')}/p/invite/{token}"
    body = (
        f"{settings.hospital_name}: your after-discharge follow-up page is ready. "
        f"Open {link} and sign in with this phone number. We will never ask for a password."
    )
    msg = send_operational_sms(
        session,
        episode_id=episode.id,
        phone=phone,
        body=body,
        stored_body=body,
        kind="invitation",
        idempotency_key=f"invite-{inv.id}",
    )
    session.commit()
    return inv, link, msg


# --------------------------------------------------------------------------- request code
def request_code(session: Session, *, phone_raw: str, invitation_token: Optional[str], ip: str) -> dict:
    phone = normalize_phone(phone_raw)
    if not phone:
        raise errors.validation(
            "Enter your mobile number with country code, for example +91 98765 43210.",
            {"fields": [{"field": "phone", "message": "Not a valid phone number"}]},
        )
    ratelimit.check_and_hit(session, "otp_request_ip", ip)
    now = clock.real_now()
    cooldown = settings.otp_resend_cooldown_seconds
    last = session.scalars(
        select(OtpChallenge).where(OtpChallenge.phone_e164 == phone).order_by(OtpChallenge.created_at.desc())
    ).first()
    if last is not None and (now - last.created_at).total_seconds() < cooldown:
        session.commit()
        wait = int(cooldown - (now - last.created_at).total_seconds()) + 1
        return {"message": f"A code was sent recently. You can request a new one in {wait} seconds.", "resend_after_seconds": wait}
    ratelimit.check_and_hit(session, "otp_request_phone", phone)

    # Invalidate any outstanding codes for this phone (only the newest code works).
    session.execute(
        update(OtpChallenge)
        .where(OtpChallenge.phone_e164 == phone, OtpChallenge.consumed_at.is_(None), OtpChallenge.invalidated_at.is_(None))
        .values(invalidated_at=now)
    )
    inv = _active_invitation(session, phone, invitation_token)
    code = new_otp_code()
    ch = OtpChallenge(
        phone_e164=phone,
        user_id=None,
        invitation_id=inv.id if inv else None,
        code_hash=hash_secret(code),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
        attempts=0,
        max_attempts=settings.otp_max_attempts,
        delivery_mode="pending" if inv else "none",
    )
    session.add(ch)
    session.flush()
    record_event(
        session,
        "auth.otp_requested",
        "Sign-in code requested",
        episode_id=inv.episode_id if inv else None,
        actor_type="patient",
        detail={"challenge_id": ch.id, "registered": bool(inv)},  # staff-only detail
        scopes=[],
    )
    session.commit()

    if inv is not None:
        minutes = settings.otp_ttl_seconds // 60
        body = f"{code} is your Ninety-Six sign-in code for {settings.hospital_name}. It expires in {minutes} minutes. Never share it."
        stored = body if settings.demo_mode else body.replace(code, "••••••")
        msg = send_operational_sms(
            session,
            episode_id=inv.episode_id,
            phone=phone,
            body=body,
            stored_body=stored,
            kind="otp",
            idempotency_key=f"otp-{ch.id}",
        )
        ch.delivery_mode = msg.mode
        session.commit()
    return {"message": GENERIC_SENT, "resend_after_seconds": cooldown}


# --------------------------------------------------------------------------- verify code
def verify_code(session: Session, *, phone_raw: str, code: str, ip: str) -> User:
    phone = normalize_phone(phone_raw)
    code = (code or "").strip().replace(" ", "")
    if not phone or not code.isdigit() or len(code) != 6:
        raise invalid_code()
    ratelimit.check_and_hit(session, "otp_verify_ip", ip)
    ratelimit.check_and_hit(session, "otp_verify_phone", phone)
    now = clock.real_now()
    ch = session.scalars(
        select(OtpChallenge)
        .where(OtpChallenge.phone_e164 == phone, OtpChallenge.consumed_at.is_(None), OtpChallenge.invalidated_at.is_(None))
        .order_by(OtpChallenge.created_at.desc())
    ).first()
    if ch is None or ch.expires_at <= now or ch.attempts >= ch.max_attempts:
        if ch is not None and ch.invalidated_at is None:
            ch.invalidated_at = now
        session.commit()
        raise invalid_code()
    ch.attempts += 1
    ok = verify_secret(code, ch.code_hash)
    if not ok:
        if ch.attempts >= ch.max_attempts:
            ch.invalidated_at = now
        record_event(session, "auth.otp_failed", "Sign-in code rejected", actor_type="patient",
                     detail={"challenge_id": ch.id, "attempts": ch.attempts}, scopes=[])
        session.commit()
        raise invalid_code()
    if ch.invitation_id is None:
        # Correct code for an unregistered phone is impossible (no code was sent); stay generic.
        ch.invalidated_at = now
        session.commit()
        raise invalid_code()
    inv = session.get(Invitation, ch.invitation_id)
    ep = session.get(DischargeEpisode, inv.episode_id) if inv else None
    if inv is None or inv.status not in ("pending", "accepted") or inv.expires_at <= now or ep is None or ep.status in _CLOSED:
        ch.invalidated_at = now
        session.commit()
        raise invalid_code()
    # Atomic single use: only one request can consume the challenge.
    res = session.execute(
        update(OtpChallenge)
        .where(OtpChallenge.id == ch.id, OtpChallenge.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    if res.rowcount != 1:
        session.rollback()
        raise invalid_code()
    patient = session.get(Patient, inv.patient_id)
    user = ensure_patient_user(session, patient)
    ch.user_id = user.id
    if inv.status == "pending":
        inv.status = "accepted"
        inv.accepted_at = now
    session.flush()
    return user
