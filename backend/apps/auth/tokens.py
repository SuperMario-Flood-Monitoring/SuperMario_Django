from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from django.conf import settings

from .models import User


TokenType = Literal["access", "refresh"]

ACCESS_TOKEN_SECONDS = 30 * 60 * 4
REFRESH_TOKEN_SECONDS = 7 * 24 * 60 * 60


class TokenError(ValueError):
    pass


class TokenExpired(TokenError):
    pass


def _secret() -> bytes:
    key = getattr(settings, "SUPERMARIO_JWT_SECRET_KEY", None) or settings.SECRET_KEY
    return str(key).encode("utf-8")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def _json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def hash_refresh_token(token: str) -> str:
    return hmac.new(_secret(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_token(user: User, token_type: TokenType) -> str:
    now = datetime.now(timezone.utc)
    ttl = ACCESS_TOKEN_SECONDS if token_type == "access" else REFRESH_TOKEN_SECONDS
    payload = {
        "sub": str(user.user_id),
        "userId": user.user_id,
        "username": user.username,
        "role": user.role,
        "tokenType": token_type,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _b64encode(_json_dumps(header))
    encoded_payload = _b64encode(_json_dumps(payload))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(_secret(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64encode(signature)}"


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
    except ValueError as exc:
        raise TokenError("Invalid token format.") from exc

    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature = hmac.new(_secret(), signing_input, hashlib.sha256).digest()
    try:
        signature = _b64decode(encoded_signature)
    except Exception as exc:
        raise TokenError("Invalid token signature.") from exc
    if not hmac.compare_digest(signature, expected_signature):
        raise TokenError("Invalid token signature.")

    try:
        header = json.loads(_b64decode(encoded_header))
        payload = json.loads(_b64decode(encoded_payload))
    except Exception as exc:
        raise TokenError("Invalid token payload.") from exc

    if header.get("alg") != "HS256":
        raise TokenError("Unsupported token algorithm.")
    if expected_type and payload.get("tokenType") != expected_type:
        raise TokenError("Invalid token type.")

    expires_at = payload.get("exp")
    if not isinstance(expires_at, int):
        raise TokenError("Token expiration is required.")
    if expires_at <= int(datetime.now(timezone.utc).timestamp()):
        raise TokenExpired("Token expired.")

    return payload
