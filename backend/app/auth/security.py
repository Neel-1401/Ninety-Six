"""Hashing helpers (stdlib only): scrypt for passwords and one-time codes, sha256 for tokens."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def _scrypt(secret: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(secret, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_secret(secret: str, stored: str | None) -> bool:
    if not stored:
        # Spend comparable time so missing accounts are not distinguishable by timing.
        _scrypt(secret or "x", b"0" * 16, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
        return False
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = _scrypt(secret, bytes.fromhex(salt_hex), int(n), int(r), int(p))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_otp_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


# --------------------------------------------------------------------------- phone numbers
_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw: str | None) -> str | None:
    """Return E.164 (+<digits>) or None if the input cannot be a phone number.
    Bare 10-digit Indian mobile numbers get +91 (display timezone/hospital are in India)."""
    if not raw:
        return None
    s = re.sub(r"[\s\-().]", "", raw.strip())
    if s.startswith("whatsapp:"):
        s = s[len("whatsapp:"):]
    if s.startswith("00"):
        s = "+" + s[2:]
    if re.fullmatch(r"\d{10}", s):
        s = "+91" + s
    elif re.fullmatch(r"91\d{10}", s):
        s = "+" + s
    return s if _PHONE_RE.match(s) else None


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    p = phone.replace("whatsapp:", "")
    if len(p) <= 6:
        return "•" * len(p)
    return p[:3] + "•" * (len(p) - 7) + p[-4:]
