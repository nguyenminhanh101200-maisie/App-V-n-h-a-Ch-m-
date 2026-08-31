"""Session cookie handling and the ``get_current_user`` dependency.

Sessions are Fernet-encrypted, HTTPOnly cookies; the browser never sees the raw
payload and ``localStorage`` is not trusted as an auth source. The Fernet key is
derived deterministically from ``SECRET_KEY`` so no extra secret is required.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, Response

from app.config import Settings, get_settings
from app.extensions import AppResources


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str
    email: str
    avatar_url: str | None


def _fernet(settings: Settings) -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encode_session(settings: Settings, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return _fernet(settings).encrypt(raw).decode()


def _decode_session(settings: Settings, token: str) -> dict[str, Any]:
    try:
        raw = _fernet(settings).decrypt(token.encode())
        return json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid session") from exc


def build_session_payload(user_id: str, email: str) -> dict[str, Any]:
    now = int(time.time())
    return {"user_id": str(user_id), "email": email or "", "issued_at": now}


def set_session_cookie(response: Response, settings: Settings, session_data: dict[str, Any]) -> None:
    token = _encode_session(settings, session_data)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_secure,
        samesite=settings.session_samesite,
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


def raise_auth_required(message: str = "Yêu cầu đăng nhập để sử dụng Community") -> None:
    raise HTTPException(
        status_code=401,
        detail={"error": "AUTHENTICATION_REQUIRED", "message": message},
    )


async def load_user(resources: AppResources, user_id: str) -> dict[str, Any] | None:
    uid = str(user_id)
    async with resources.pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id::text AS id, username, email, avatar_url FROM users WHERE id = %s",
                (uid,),
            )
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_current_user(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise_auth_required()

    try:
        data = _decode_session(settings, token)
    except ValueError:
        clear_session_cookie(response, settings)
        raise_auth_required()
        return  # pragma: no cover

    user_id = str(data.get("user_id", "")).strip()
    issued_at = int(data.get("issued_at", 0) or 0)
    if not user_id or issued_at <= 0 or time.time() - issued_at > settings.session_ttl_seconds:
        clear_session_cookie(response, settings)
        raise_auth_required()

    resources: AppResources = request.app.state.resources
    user = await load_user(resources, user_id)
    if user is None:
        clear_session_cookie(response, settings)
        raise_auth_required("Tài khoản không tồn tại.")

    return CurrentUser(
        id=user["id"],
        username=user["username"] or "Người dùng",
        email=user["email"] or "",
        avatar_url=user["avatar_url"],
    )
