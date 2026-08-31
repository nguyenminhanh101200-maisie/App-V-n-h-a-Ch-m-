"""Authentication endpoints (Supabase Auth / GoTrue).

Two families share one session cookie:
  * ``/api/auth/*`` — clean JSON API consumed by community.js.
  * ``/login``, ``/register``, ``/update_profile`` — compatibility routes matching
    the exact shapes the existing frontend SPA expects.

Passwords are never stored by us: registration creates a confirmed Supabase Auth
user (admin API), login verifies via GoTrue password grant. ``public.users``
mirrors the auth user (same id) with profile fields.
"""

from typing import Any
from uuid import uuid4

import bcrypt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field

from app.auth.service import UserRepository
from app.config import Settings, get_settings
from app.middleware.auth import (
    CurrentUser,
    build_session_payload,
    clear_session_cookie,
    get_current_user,
    set_session_cookie,
)
from app.rate_limit import limiter
from app.supabase_client import AuthConflictError, SupabaseGateway

router = APIRouter(tags=["Authentication"])
api = APIRouter(prefix="/api/auth")


class CredsRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


def _repo(request: Request) -> UserRepository:
    return UserRepository(request.app.state.resources.pool)


def _gateway(request: Request) -> SupabaseGateway:
    return request.app.state.resources.supa


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row.get("username") or "Người dùng",
        "email": row.get("email") or "",
        "avatar_url": row.get("avatar_url"),
    }


async def _extension(upload: UploadFile) -> str:
    name = (upload.filename or "").lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if name.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
        upload.content_type or "", ".png"
    )


# --------------------------------------------------------------------------- #
# Clean API
# --------------------------------------------------------------------------- #
@api.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "avatar_url": user.avatar_url,
        },
    }


@api.patch("/me")
async def update_me(
    request: Request,
    response: Response,
    username: str | None = Form(default=None),
    current_password: str | None = Form(default=None),
    new_password: str | None = Form(default=None),
    avatar: UploadFile | None = File(default=None),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    repo = _repo(request)
    
    clean_username = username.strip() if username else None
    if username is not None and not clean_username:
        raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Tên người dùng không được để trống."})

    password_hash = None
    if new_password:
        if len(new_password) < 8:
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Mật khẩu mới phải có ít nhất 8 ký tự."})
        if not current_password:
            raise HTTPException(400, detail={"error": "BAD_REQUEST", "message": "Yêu cầu mật khẩu hiện tại để đổi mật khẩu mới."})
        
        # Verify current password
        db_user = await repo.get_by_id(user.id)
        if not db_user or not db_user.get("password_hash") or not bcrypt.checkpw(current_password.encode(), db_user["password_hash"].encode()):
            raise HTTPException(401, detail={"error": "UNAUTHORIZED", "message": "Mật khẩu hiện tại không đúng."})
        
        password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    avatar_url = None
    if avatar is not None and avatar.filename:
        data = await avatar.read()
        if data:
            if len(data) > settings.max_image_size_bytes:
                raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Ảnh vượt quá dung lượng cho phép."})
            path = f"avatars/{uuid4().hex}{await _extension(avatar)}"
            avatar_url = await _gateway(request).upload_object(
                path, data, avatar.content_type or "image/png"
            )

    updated = await repo.update_profile(
        user_id=user.id, 
        username=clean_username, 
        avatar_url=avatar_url, 
        password_hash=password_hash
    )
    assert updated is not None
    set_session_cookie(response, settings, build_session_payload(updated["id"], updated["email"]))
    
    return {
        "authenticated": True,
        "user": _public_user(updated)
    }


@api.post("/login")
@limiter.limit(get_settings().rate_limit_login)
async def api_login(
    payload: CredsRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    repo = _repo(request)
    row = await repo.get_by_email(payload.email)
    if not row or not row.get("password_hash"):
        raise HTTPException(401, detail={"error": "AUTHENTICATION_REQUIRED", "message": "Email hoặc mật khẩu không đúng."})
    
    if not bcrypt.checkpw(payload.password.encode(), row["password_hash"].encode()):
        raise HTTPException(401, detail={"error": "AUTHENTICATION_REQUIRED", "message": "Email hoặc mật khẩu không đúng."})
        
    set_session_cookie(response, settings, build_session_payload(row["id"], row["email"]))
    return {"authenticated": True, "user": _public_user(row)}


@api.post("/logout")
async def api_logout(response: Response, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    clear_session_cookie(response, settings)
    return {"authenticated": False}


router.include_router(api)


# --------------------------------------------------------------------------- #
# Compatibility routes for the existing frontend SPA
# --------------------------------------------------------------------------- #
@router.post("/register")
@limiter.limit(get_settings().rate_limit_login)
async def compat_register(payload: CredsRequest, request: Request) -> dict[str, Any]:
    if len(payload.password) < 8:
        raise HTTPException(422, detail={"message": "Mật khẩu phải có ít nhất 8 ký tự."})
    repo = _repo(request)
    existing = await repo.get_by_email(payload.email)
    if existing:
        raise HTTPException(409, detail={"message": "Email đã được đăng ký. Vui lòng đăng nhập."})
        
    hashed_pw = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()
    row = await repo.create_profile(email=payload.email, password_hash=hashed_pw)
    return {"message": "Đăng ký thành công. Hãy hoàn tất hồ sơ.", "userId": row["id"]}


@router.post("/login")
@limiter.limit(get_settings().rate_limit_login)
async def compat_login(
    payload: CredsRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    repo = _repo(request)
    row = await repo.get_by_email(payload.email)
    if row is None or not row.get("password_hash") or not bcrypt.checkpw(payload.password.encode(), row["password_hash"].encode()):
        raise HTTPException(401, detail={"message": "Email hoặc mật khẩu không đúng."})
        
    set_session_cookie(response, settings, build_session_payload(row["id"], row["email"]))
    return {
        "message": "Đăng nhập thành công.",
        "user": {
            "user_id": row["id"],
            "username": row.get("username") or "Người dùng",
            "avatar_url": row.get("avatar_url"),
        },
    }


@router.post("/update_profile")
async def compat_update_profile(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    username: str = Form(...),
    userId: str = Form(default=""),
    avatar: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    repo = _repo(request)
    resolved_id = (userId or "").strip()
    if not resolved_id:
        token = request.cookies.get(settings.session_cookie_name)
        if token:
            try:
                from app.middleware.auth import _decode_session

                resolved_id = str(_decode_session(settings, token).get("user_id", "")).strip()
            except ValueError:
                resolved_id = ""
    if not resolved_id:
        raise HTTPException(400, detail={"error": "BAD_REQUEST", "message": "Thiếu userId."})

    user = await repo.get_by_id(resolved_id)
    if user is None:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Người dùng không tồn tại."})

    clean_username = username.strip()
    if not clean_username:
        raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Tên người dùng không được để trống."})

    avatar_url: str | None = None
    if avatar is not None and avatar.filename:
        data = await avatar.read()
        if data:
            if len(data) > settings.max_image_size_bytes:
                raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Ảnh vượt quá dung lượng cho phép."})
            path = f"avatars/{uuid4().hex}{await _extension(avatar)}"
            avatar_url = await _gateway(request).upload_object(
                path, data, avatar.content_type or "image/png"
            )

    updated = await repo.update_profile(
        user_id=resolved_id, username=clean_username, avatar_url=avatar_url
    )
    assert updated is not None
    set_session_cookie(response, settings, build_session_payload(updated["id"], updated["email"]))
    return {
        "message": "Lưu thông tin thành công.",
        "user": {
            "user_id": updated["id"],
            "username": updated.get("username") or "Người dùng",
            "avatar_url": updated.get("avatar_url"),
        },
    }
