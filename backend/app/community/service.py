"""Business rules for posts, likes, comments, shares and community stats."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile

from app.community.repository import CommunityRepository
from app.community.schemas import ALLOWED_CATEGORIES
from app.config import Settings
from app.storage.service import StorageService


class CommunityService:
    def __init__(
        self,
        repo: CommunityRepository,
        storage: StorageService,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.storage = storage
        self.settings = settings

    async def create_post(
        self,
        *,
        user_id: str,
        content: str,
        category: str,
        files: list[UploadFile],
    ) -> str:
        content = content.strip()
        category = category.strip()
        files = [f for f in files if f is not None and f.filename]

        if not content and not files:
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Bài viết phải có nội dung hoặc ít nhất một ảnh."})
        if len(content) > self.settings.max_content_length:
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Nội dung bài viết quá dài."})
            
        if not category:
            raise HTTPException(422, detail={"error": "CATEGORY_REQUIRED", "message": "Vui lòng chọn một chuyên mục cho bài viết."})
        if category not in ALLOWED_CATEGORIES:
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Chủ đề không hợp lệ."})
            
        if len(files) > self.settings.max_upload_images:
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": f"Chỉ được tải tối đa {self.settings.max_upload_images} ảnh."})

        uploaded_urls: list[str] = []
        try:
            for file in files:
                data = await file.read()
                self._validate_image(file.content_type or "", data, file.filename or "")
                url = await self.storage.upload_image(
                    data, file.content_type or "", file.filename or "image"
                )
                uploaded_urls.append(url)

            return await self.repo.create_post(
                user_id=user_id,
                content=content,
                category=category,
                image_urls=uploaded_urls,
            )
        except HTTPException:
            await self._cleanup(uploaded_urls)
            raise
        except Exception as exc:
            await self._cleanup(uploaded_urls)
            raise HTTPException(
                status_code=500,
                detail={"error": "INTERNAL_SERVER_ERROR", "message": "Không thể tạo bài viết."},
            ) from exc

    async def share_post(self, *, user_id: str, post_id: str, content: str) -> str:
        original = await self.repo.get_post_for_share(post_id)
        if original is None:
            raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Bài viết gốc không tồn tại."})
        return await self.repo.create_post(
            user_id=user_id,
            content=content.strip(),
            category=original["category"],
            image_urls=[],
            shared_post_id=original["id"],
        )

    def _validate_image(self, content_type: str, data: bytes, filename: str) -> None:
        allowed = {"image/jpeg", "image/png", "image/webp"}
        if content_type not in allowed:
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Chỉ chấp nhận JPEG, PNG hoặc WebP."})
        if len(data) == 0 or len(data) > self.settings.max_image_size_bytes:
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": "Ảnh không hợp lệ hoặc vượt quá dung lượng cho phép."})
        if content_type == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": f"File {filename} không phải JPEG hợp lệ."})
        if content_type == "image/png" and data[:8] != b"\x89PNG\r\n\x1a\n":
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": f"File {filename} không phải PNG hợp lệ."})
        if content_type == "image/webp" and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            raise HTTPException(422, detail={"error": "VALIDATION_ERROR", "message": f"File {filename} không phải WebP hợp lệ."})

    async def _cleanup(self, urls: list[str]) -> None:
        for url in urls:
            try:
                await self.storage.delete_image(url)
            except Exception:
                pass
