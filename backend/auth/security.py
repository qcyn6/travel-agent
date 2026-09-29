"""Small, dependency-free authentication primitives for the MVP."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time


def normalize_username(username: str) -> str:
    return username.strip().lower()


def hash_password(password: str) -> str:
    if len(password) < 6:
        raise ValueError("密码至少需要 6 位")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )
    return "scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(*, user_id: str, role: str, secret: str, expires_minutes: int) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": int(time.time()) + max(5, expires_minutes) * 60,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"ta1.{encoded}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"


def decode_access_token(token: str, *, secret: str) -> dict[str, object] | None:
    try:
        version, encoded, signature = token.split(".", 2)
        if version != "ta1":
            return None
        expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(actual, expected):
            return None
        payload = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        )
        if not isinstance(payload, dict) or int(payload.get("exp", 0)) <= int(time.time()):
            return None
        if not isinstance(payload.get("sub"), str) or not payload["sub"]:
            return None
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


__all__ = [
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "normalize_username",
    "verify_password",
]
