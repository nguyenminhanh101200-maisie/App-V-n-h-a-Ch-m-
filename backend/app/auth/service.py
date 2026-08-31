"""User repository for the ``public.users`` table (Supabase PostgreSQL).

Schema note: ``users.id`` is a bigint identity (NOT the Supabase Auth uuid).
Authentication is handled by Supabase Auth (GoTrue); this table is the app-level
profile, linked to the auth user by **email**. All FKs (posts/comments/likes)
reference this bigint id.
"""

from __future__ import annotations

from typing import Any

from psycopg_pool import AsyncConnectionPool

_SELECT = "SELECT id::text AS id, username, email, avatar_url, password_hash FROM users"


class UserRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"{_SELECT} WHERE lower(email) = lower(%s)", (email.strip(),))
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_by_id(self, user_id: str | int) -> dict[str, Any] | None:
        uid = str(user_id)
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"{_SELECT} WHERE id = %s", (uid,))
                row = await cur.fetchone()
        return dict(row) if row else None

    async def create_profile(
        self, *, email: str, username: str | None = None, avatar_url: str | None = None, password_hash: str | None = None
    ) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO users (email, username, avatar_url, password_hash)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id::text AS id, username, email, avatar_url, password_hash
                    """,
                    (email.strip(), username, avatar_url, password_hash),
                )
                row = await cur.fetchone()
        return dict(row)

    async def get_or_create_by_email(
        self, *, email: str, username: str | None = None, password_hash: str | None = None
    ) -> dict[str, Any]:
        existing = await self.get_by_email(email)
        if existing is not None:
            return existing
        return await self.create_profile(email=email, username=username, password_hash=password_hash)

    async def update_profile(
        self, *, user_id: str | int, username: str | None = None, avatar_url: str | None = None, **kwargs
    ) -> dict[str, Any] | None:
        uid = str(user_id)
        sets: list[str] = []
        params: list[Any] = []
        if username is not None:
            sets.append("username = %s")
            params.append(username)
        if avatar_url is not None:
            sets.append("avatar_url = %s")
            params.append(avatar_url)
        if kwargs.get("password_hash") is not None:
            sets.append("password_hash = %s")
            params.append(kwargs["password_hash"])
        if sets:
            params.append(uid)
            async with self.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"UPDATE users SET {', '.join(sets)} WHERE id = %s", tuple(params)
                    )
        return await self.get_by_id(uid)
