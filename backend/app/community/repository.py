"""PostgreSQL (Supabase) data access for the community feature.

Schema note: all ids are bigint identity columns. We insert without supplying an
id (identity auto-generates) and return ``id::text`` so the API keeps string ids.
Incoming string ids are converted to int. Image URLs are stored in
``posts.image_url`` as a JSON-array string.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg_pool import AsyncConnectionPool

from app.community.schemas import from_db_category, to_db_category


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _to_str(value: Any) -> str | None:
    return str(value) if value is not None and str(value).strip() != "" else None


class CommunityRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def list_posts(
        self,
        *,
        current_user_id: str,
        category: str | None,
        sort: str,
        page: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        cu = _to_str(current_user_id)
        offset = (page - 1) * limit
        order_sql = (
            "like_count DESC, p.created_at DESC, p.id DESC"
            if sort == "popular"
            else "p.created_at DESC, p.id DESC"
        )
        filters = "WHERE 1=1"
        params: list[Any] = [cu]
        if category:
            filters += " AND p.category = %s"
            params.append(to_db_category(category))

        query = f"""
            SELECT
                p.id::text AS id,
                p.created_at AS created_at,
                COALESCE(p.content, '') AS content,
                COALESCE(p.category, 'Chung') AS category,
                p.shared_post_id::text AS shared_post_id,
                u.id::text AS author_id,
                u.username AS author_username,
                u.avatar_url AS author_avatar_url,
                (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id = p.id) AS like_count,
                (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count,
                EXISTS(
                    SELECT 1 FROM post_likes my_like
                    WHERE my_like.post_id = p.id AND my_like.user_id = %s
                ) AS liked_by_current_user,
                COALESCE(p.image_url, '') AS image_url
            FROM posts p
            JOIN users u ON u.id = p.user_id
            {filters}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
        """
        params.extend([limit + 1, offset])

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, tuple(params))
                rows = [dict(r) for r in await cur.fetchall()]

                has_next = len(rows) > limit
                rows = rows[:limit]

                original_ids = [r["shared_post_id"] for r in rows if r["shared_post_id"]]
                original_map = await self._fetch_original_posts(cur, original_ids)

        for row in rows:
            row["category"] = from_db_category(row["category"])
            row["image_urls"] = self._decode_image_urls(row.pop("image_url", ""))
            row["author"] = {
                "id": row.pop("author_id"),
                "username": row.pop("author_username") or "Người dùng",
                "avatar_url": row.pop("author_avatar_url"),
            }
            row["like_count"] = int(row["like_count"] or 0)
            row["comment_count"] = int(row["comment_count"] or 0)
            row["liked_by_current_user"] = bool(row["liked_by_current_user"])
            row["original_post"] = original_map.get(row["shared_post_id"])
        return rows, has_next

    async def _fetch_original_posts(self, cur, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        await cur.execute(
            """
            SELECT
                p.id::text AS id,
                p.created_at AS created_at,
                COALESCE(p.content, '') AS content,
                p.user_id::text AS user_id,
                COALESCE(p.image_url, '') AS image_url,
                u.username AS username,
                u.avatar_url AS avatar_url
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.id::text = ANY(%s)
            """,
            (ids,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        return {
            row["id"]: {
                "id": row["id"],
                "created_at": row["created_at"],
                "content": row["content"],
                "image_urls": self._decode_image_urls(row["image_url"]),
                "author": {
                    "id": row["user_id"],
                    "username": row["username"] or "Người dùng",
                    "avatar_url": row["avatar_url"],
                },
            }
            for row in rows
        }

    async def post_exists(self, post_id: str) -> bool:
        pid = _to_str(post_id)
        if pid is None:
            return False
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM posts WHERE id = %s", (pid,))
                return await cur.fetchone() is not None

    async def get_post_for_share(self, post_id: str) -> dict[str, Any] | None:
        pid = _to_str(post_id)
        if pid is None:
            return None
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id::text AS id, category FROM posts WHERE id = %s", (pid,)
                )
                row = await cur.fetchone()
        return dict(row) if row else None

    async def create_post(
        self,
        *,
        user_id: str,
        content: str,
        category: str,
        image_urls: list[str],
        shared_post_id: str | None = None,
    ) -> str:
        image_url = (
            json.dumps(image_urls, ensure_ascii=False, separators=(",", ":"))
            if image_urls
            else ""
        )
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO posts (content, image_url, user_id, category, shared_post_id)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id::text AS id
                    """,
                    (
                        content,
                        image_url,
                        _to_str(user_id),
                        to_db_category(category),
                        _to_str(shared_post_id) if shared_post_id is not None else None,
                    ),
                )
                row = await cur.fetchone()
        return row["id"]

    async def add_comment(self, *, user_id: str, post_id: str, content: str) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO comments (content, user_id, post_id)
                    VALUES (%s, %s, %s)
                    RETURNING id::text AS id, created_at, content
                    """,
                    (content, _to_str(user_id), _to_str(post_id)),
                )
                row = await cur.fetchone()
        return dict(row)

    async def list_comments(self, post_id: str) -> list[dict[str, Any]]:
        pid = _to_str(post_id)
        if pid is None:
            return []
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        c.id::text AS id,
                        c.created_at AS created_at,
                        c.content AS content,
                        u.id::text AS user_id,
                        u.username AS username,
                        u.avatar_url AS avatar_url
                    FROM comments c
                    JOIN users u ON u.id = c.user_id
                    WHERE c.post_id = %s
                    ORDER BY c.created_at ASC, c.id ASC
                    """,
                    (pid,),
                )
                rows = [dict(r) for r in await cur.fetchall()]
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "content": row["content"],
                "user": {
                    "id": row["user_id"],
                    "username": row["username"] or "Người dùng",
                    "avatar_url": row["avatar_url"],
                },
            }
            for row in rows
        ]

    async def toggle_like(self, *, post_id: str, user_id: str) -> tuple[bool, int]:
        pid = _to_str(post_id)
        uid = _to_str(user_id)
        if pid is None or uid is None:
            raise LookupError("POST_NOT_FOUND")
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM posts WHERE id = %s FOR UPDATE", (pid,))
                if await cur.fetchone() is None:
                    raise LookupError("POST_NOT_FOUND")

                await cur.execute(
                    "SELECT 1 FROM post_likes WHERE post_id = %s AND user_id = %s", (pid, uid)
                )
                existing = await cur.fetchone()

                if existing:
                    await cur.execute(
                        "DELETE FROM post_likes WHERE post_id = %s AND user_id = %s", (pid, uid)
                    )
                    liked = False
                else:
                    await cur.execute(
                        """
                        INSERT INTO post_likes (post_id, user_id)
                        VALUES (%s, %s)
                        ON CONFLICT (post_id, user_id) DO NOTHING
                        """,
                        (pid, uid),
                    )
                    liked = True

                await cur.execute(
                    "SELECT COUNT(*) AS c FROM post_likes WHERE post_id = %s", (pid,)
                )
                count = int((await cur.fetchone())["c"])
        return liked, count

    async def topic_stats(self) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT category, COUNT(id) AS post_count
                    FROM posts
                    WHERE category IS NOT NULL AND category <> ''
                    GROUP BY category
                    ORDER BY post_count DESC, category ASC
                    """
                )
                rows = [dict(r) for r in await cur.fetchall()]
        return [
            {"category": from_db_category(r["category"]), "post_count": int(r["post_count"])}
            for r in rows
        ]

    async def toggle_follow(self, follower_id: Any, following_id: Any) -> bool:
        fid = _to_str(follower_id)
        tid = _to_str(following_id)
        if fid is None or tid is None:
            raise LookupError("USER_NOT_FOUND")
            
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # Kiểm tra following_id có tồn tại trong users không
                await cur.execute("SELECT 1 FROM users WHERE id = %s FOR UPDATE", (tid,))
                if await cur.fetchone() is None:
                    raise LookupError("USER_NOT_FOUND")

                await cur.execute(
                    "SELECT 1 FROM follows WHERE follower_id = %s AND following_id = %s",
                    (fid, tid),
                )
                existing = await cur.fetchone()

                if existing:
                    await cur.execute(
                        "DELETE FROM follows WHERE follower_id = %s AND following_id = %s",
                        (fid, tid),
                    )
                    following = False
                else:
                    await cur.execute(
                        """
                        INSERT INTO follows (follower_id, following_id)
                        VALUES (%s, %s)
                        ON CONFLICT (follower_id, following_id) DO NOTHING
                        """,
                        (fid, tid),
                    )
                    following = True

        return following


    @staticmethod
    def _decode_image_urls(value: str | None) -> list[str]:
        if not value:
            return []
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
        return [item.strip() for item in text.split(",") if item.strip()]
