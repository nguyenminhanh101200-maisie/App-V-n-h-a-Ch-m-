from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi import Form

from app.community.repository import CommunityRepository
from app.community.schemas import (
    ALLOWED_CATEGORIES,
    CommentCreate,
    CommentOut,
    CommentsResponse,
    PostOut,
    PostsResponse,
    ShareCreate,
    TopicStatsResponse,
)
from app.community.service import CommunityService
from app.config import Settings, get_settings
from app.middleware.auth import CurrentUser, get_current_user
from app.rate_limit import limiter
from app.storage.service import StorageService

router = APIRouter(prefix="/api/community", tags=["Community"])


def get_service(request: Request, settings: Settings = Depends(get_settings)) -> CommunityService:
    resources = request.app.state.resources
    repo = CommunityRepository(resources.pool)
    storage = StorageService(resources.supa)
    return CommunityService(repo, storage, settings)


@router.get("/posts", response_model=PostsResponse)
async def get_posts(
    current_user: CurrentUser = Depends(get_current_user),
    service: CommunityService = Depends(get_service),
    category: str | None = Query(default=None),
    sort: str = Query(default="latest"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    if sort not in {"latest", "popular"}:
        raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "sort chỉ hỗ trợ latest hoặc popular."})
    if category == "":
        category = None
    elif category is not None and category not in ALLOWED_CATEGORIES:
        raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "category không hợp lệ."})

    rows, has_next = await service.repo.list_posts(
        current_user_id=current_user.id,
        category=category,
        sort=sort,
        page=page,
        limit=limit,
    )
    return {
        "items": [PostOut.model_validate(row) for row in rows],
        "pagination": {"page": page, "limit": limit, "has_next": has_next},
    }


@router.post("/posts")
@limiter.limit(get_settings().rate_limit_post)
async def create_post(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    service: CommunityService = Depends(get_service),
    content: str = Form(default=""),
    category: str = Form(default=""),
    images: list[UploadFile] = File(default_factory=list),
) -> dict[str, Any]:
    post_id = await service.create_post(
        user_id=current_user.id,
        content=content,
        category=category,
        files=images,
    )
    return {"id": post_id, "message": "Đăng bài thành công."}


@router.post("/posts/{post_id}/like")
@limiter.limit(get_settings().rate_limit_like)
async def toggle_like(
    request: Request,
    post_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: CommunityService = Depends(get_service),
) -> dict[str, Any]:
    try:
        liked, count = await service.repo.toggle_like(post_id=post_id, user_id=current_user.id)
    except LookupError:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Bài viết không tồn tại."})
    return {"liked": liked, "like_count": count}


@router.get("/posts/{post_id}/comments", response_model=CommentsResponse)
async def get_comments(
    post_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: CommunityService = Depends(get_service),
) -> dict[str, Any]:
    if not await service.repo.post_exists(post_id):
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Bài viết không tồn tại."})
    return {"items": await service.repo.list_comments(post_id)}


@router.post("/posts/{post_id}/comments")
@limiter.limit(get_settings().rate_limit_comment)
async def add_comment(
    request: Request,
    post_id: str,
    payload: CommentCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: CommunityService = Depends(get_service),
) -> dict[str, Any]:
    if not await service.repo.post_exists(post_id):
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Bài viết không tồn tại."})
    created = await service.repo.add_comment(
        user_id=current_user.id,
        post_id=post_id,
        content=payload.content,
    )
    comment = CommentOut.model_validate(
        {
            **created,
            "user": {
                "id": current_user.id,
                "username": current_user.username,
                "avatar_url": current_user.avatar_url,
            },
        }
    )
    return comment.model_dump(mode="json")


@router.post("/posts/{post_id}/share")
async def share_post(
    post_id: str,
    payload: ShareCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: CommunityService = Depends(get_service),
) -> dict[str, Any]:
    new_post_id = await service.share_post(
        user_id=current_user.id,
        post_id=post_id,
        content=payload.content,
    )
    return {"id": new_post_id, "message": "Đã chia sẻ bài viết."}


@router.get("/stats/topics", response_model=TopicStatsResponse)
async def topic_stats(
    current_user: CurrentUser = Depends(get_current_user),
    service: CommunityService = Depends(get_service),
) -> dict[str, Any]:
    return {"items": await service.repo.topic_stats()}



