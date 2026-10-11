"""TOTP 2FA with stdlib only. Window default 2 (±60s) mirrors
PHPGangsta_GoogleAuthenticator::verifyCode($secret, $code, 2)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time


def generate_secret(length: int = 16) -> str:
    if not 16 <= length <= 128:
        raise ValueError("Secret length must be between 16 and 128.")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _hotp(secret: str, counter: int, digits: int = 6) -> str:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def totp_now(secret: str, interval: int = 30) -> str:
    return _hotp(secret, int(time.time()) // interval)


def verify_totp(secret: str, code: str, window: int = 2, interval: int = 30) -> bool:
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        return False
    now = int(time.time()) // interval
    for i in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret, now + i), code):
            return True
    return False


def otpauth_url(secret: str, account: str, issuer: str = "PipraPay") -> str:
    from urllib.parse import quote

    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}"
