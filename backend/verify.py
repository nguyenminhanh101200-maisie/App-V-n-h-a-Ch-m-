"""End-to-end smoke test against a running server (http://127.0.0.1:8000)
backed by the real Supabase project.

Creates a throwaway account + a few posts, verifies the whole contract, then
cleans up the data it created (posts + test auth user) so the project stays tidy.
"""

import io
import os
import sys
import uuid

import httpx
import psycopg
from dotenv import load_dotenv

load_dotenv()

BASE = "http://127.0.0.1:8000"
DEMO_EMAIL = "minhanh@gmail.com"
DEMO_PASSWORD = "123Aa2026"

passed = 0
failed = 0
created_post_ids: list[str] = []
reg_email: str | None = None


def check(name: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"[{mark}] {name}" + (f" -- {extra}" if extra else ""))


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
)


def run_checks() -> None:
    global reg_email
    origin = {"Origin": BASE}
    with httpx.Client(base_url=BASE, timeout=20.0, follow_redirects=True) as c:
        r = c.get("/health")
        check("GET /health", r.status_code == 200 and r.json().get("status") == "ok", str(r.status_code))

        r = c.get("/api/community/posts")
        check("guest GET posts -> 401", r.status_code == 401, str(r.status_code))

        # Register throwaway user
        reg_email = f"verify_{uuid.uuid4().hex[:10]}@example.com"
        r = c.post("/register", json={"email": reg_email, "password": "supersecret"}, headers=origin)
        ok = r.status_code == 200 and "userId" in r.json()
        user_id = r.json().get("userId") if ok else None
        check("POST /register", ok, str(r.status_code))

        r = c.post("/register", json={"email": reg_email, "password": "supersecret"}, headers=origin)
        check("POST /register duplicate -> 409", r.status_code == 409, str(r.status_code))

        # update_profile (multipart; userId from register, no session yet)
        files = {"avatar": ("a.png", io.BytesIO(PNG_1PX), "image/png")}
        r = c.post("/update_profile", data={"username": "Người Kiểm Thử", "userId": user_id or ""},
                   files=files, headers=origin)
        ok = r.status_code == 200 and r.json().get("user", {}).get("username") == "Người Kiểm Thử"
        avatar_url = r.json().get("user", {}).get("avatar_url") if r.status_code == 200 else None
        check("POST /update_profile", ok, str(r.status_code))

        r = c.get("/api/auth/me")
        check("GET /api/auth/me after update_profile", r.status_code == 200 and r.json().get("authenticated"), str(r.status_code))

        if avatar_url:
            rr = c.get(avatar_url)
            check("GET uploaded avatar (Supabase Storage)",
                  rr.status_code == 200 and rr.headers.get("content-type", "").startswith("image"),
                  f"{rr.status_code}")

        r = c.post("/api/auth/logout", headers=origin)
        check("POST /api/auth/logout", r.status_code == 200, str(r.status_code))
        r = c.get("/api/auth/me")
        check("me after logout -> 401", r.status_code == 401, str(r.status_code))

        # Login flows
        r = c.post("/login", json={"email": "nobody_" + uuid.uuid4().hex + "@x.vn", "password": "whatever12"}, headers=origin)
        check("POST /login unknown -> 401", r.status_code == 401, str(r.status_code))
        r = c.post("/login", json={"email": reg_email, "password": "supersecret"}, headers=origin)
        ok = r.status_code == 200 and r.json().get("user", {}).get("user_id")
        me_id = r.json().get("user", {}).get("user_id") if ok else None
        check("POST /login reg_email", ok, str(r.status_code))
        r = c.post("/login", json={"email": reg_email, "password": "wrongpass1"}, headers=origin)
        check("POST /login wrong pw -> 401", r.status_code == 401, str(r.status_code))
        c.post("/login", json={"email": reg_email, "password": "supersecret"}, headers=origin)

        # Feed
        r = c.get("/api/community/posts?sort=latest&page=1&limit=20")
        ok = r.status_code == 200
        check("GET posts (seeded feed)", ok, f"{r.status_code} items={len(r.json().get('items', []))}")
        r = c.get("/api/community/posts?sort=popular")
        check("GET posts sort=popular", r.status_code == 200, str(r.status_code))
        r = c.get("/api/community/posts?category=Lễ hội")
        check("GET posts category=Lễ hội", r.status_code == 200, str(r.status_code))
        r = c.get("/api/community/posts?category=Văn hóa Chăm")
        ok = r.status_code == 200 and all(p["category"] == "Văn hóa Chăm" for p in r.json().get("items", []))
        check("GET posts category=Văn hóa Chăm (mapped)", ok, f"{r.status_code}")
        r = c.get("/api/community/posts?category=Invalid")
        check("GET posts invalid category -> 422", r.status_code == 422, str(r.status_code))

        # Create post
        r = c.post("/api/community/posts", data={"content": "Bài viết kiểm thử tự động.", "category": "Daily"}, headers=origin)
        ok = r.status_code == 200 and r.json().get("id")
        post_id = r.json().get("id") if ok else None
        if post_id:
            created_post_ids.append(post_id)
        check("POST /api/community/posts", ok, str(r.status_code))

        # Create with image (uploads to Supabase Storage)
        files = [("images", ("p.png", PNG_1PX, "image/png"))]
        r = c.post("/api/community/posts", data={"content": "Có ảnh", "category": "Văn hóa Chăm"}, files=files, headers=origin)
        img_post_id = r.json().get("id") if r.status_code == 200 else None
        if img_post_id:
            created_post_ids.append(img_post_id)
        check("POST post with image", r.status_code == 200, str(r.status_code))
        r = c.get("/api/community/posts?category=Văn hóa Chăm")
        found = any(p["id"] == img_post_id and p.get("image_urls") for p in r.json().get("items", []))
        check("created post has image_urls", found)

        # Like toggle
        r = c.post(f"/api/community/posts/{post_id}/like", headers=origin)
        ok = r.status_code == 200 and r.json().get("liked") is True and r.json().get("like_count") == 1
        check("POST like (on)", ok, str(r.status_code))
        r = c.post(f"/api/community/posts/{post_id}/like", headers=origin)
        ok = r.status_code == 200 and r.json().get("liked") is False and r.json().get("like_count") == 0
        check("POST like (off)", ok, str(r.status_code))
        r = c.post("/api/community/posts/00000000-0000-0000-0000-000000000000/like", headers=origin)
        check("POST like missing post -> 404", r.status_code == 404, str(r.status_code))

        # Comment
        r = c.post(f"/api/community/posts/{post_id}/comments", json={"content": "Bình luận thử"}, headers=origin)
        check("POST comment", r.status_code == 200 and r.json().get("content") == "Bình luận thử", str(r.status_code))
        r = c.get(f"/api/community/posts/{post_id}/comments")
        check("GET comments", r.status_code == 200 and len(r.json().get("items", [])) == 1, str(r.status_code))
        r = c.post(f"/api/community/posts/{post_id}/comments", json={"content": "   "}, headers=origin)
        check("POST empty comment -> 422", r.status_code == 422, str(r.status_code))

        # Share
        r = c.post(f"/api/community/posts/{post_id}/share", json={"content": "Chia sẻ thử"}, headers=origin)
        ok = r.status_code == 200 and r.json().get("id")
        share_id = r.json().get("id") if ok else None
        if share_id:
            created_post_ids.append(share_id)
        check("POST share", ok, str(r.status_code))
        r = c.get("/api/community/posts?category=Daily")
        shared = next((p for p in r.json().get("items", []) if p["id"] == share_id), None)
        check("shared post has original_post", bool(shared and shared.get("original_post")))

        # Stats
        r = c.get("/api/community/stats/topics")
        check("GET stats/topics", r.status_code == 200 and len(r.json().get("items", [])) > 0, str(r.status_code))
        # Profile
        r = c.get(f"/api/community/profiles/{me_id}")
        ok = r.status_code == 200 and r.json().get("is_current_user") and "email" in r.json().get("user", {})
        check("GET own profile (email present)", ok, str(r.status_code))
        posts = c.get("/api/community/posts").json().get("items", [])
        other_id = next((p["author"]["id"] for p in posts if p.get("author") and p["author"]["id"] != me_id), None)
        if other_id:
            r = c.get(f"/api/community/profiles/{other_id}")
            ok = r.status_code == 200 and not r.json().get("is_current_user") and "email" not in r.json().get("user", {})
            check("GET other profile (email hidden)", ok, str(r.status_code))
        r = c.get("/api/community/profiles/00000000-0000-0000-0000-000000000000")
        check("GET profile missing -> 404", r.status_code == 404, str(r.status_code))

        # CSRF guard
        r = c.post(f"/api/community/posts/{post_id}/like", headers={"Origin": "http://evil.example"})
        check("cross-origin POST -> 403", r.status_code == 403, str(r.status_code))

        # Static pages
        for path, needle in [
            ("/", "Đăng nhập"),
            ("/dashboard/index.html", "DÂN TỘC CHĂM"),
            ("/community/index.html", "community.js"),
            ("/community/community.js", "api/community"),
            ("/profile.html", "profileForm"),
        ]:
            r = c.get(path)
            check(f"GET {path}", r.status_code == 200 and needle in r.text, str(r.status_code))


def cleanup() -> None:
    print("\n-- cleanup --")
    conninfo = os.environ.get("DATABASE_URL", "")
    # Delete test posts (cascades comments/likes)
    try:
        with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
            for pid in created_post_ids:
                try:
                    cur.execute("DELETE FROM posts WHERE id = %s", (int(pid),))
                except Exception:
                    pass
            if reg_email:
                cur.execute("DELETE FROM users WHERE lower(email) = lower(%s)", (reg_email,))
        print(f"  deleted {len(created_post_ids)} test posts + test profile")
    except Exception as e:
        print(f"  db cleanup warning: {e}")
    # Delete throwaway auth user
    if reg_email:
        try:
            url = os.environ["SUPABASE_URL"].rstrip("/")
            key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
            h = {"apikey": key, "Authorization": f"Bearer {key}"}
            r = httpx.get(f"{url}/auth/v1/admin/users", headers=h, params={"page": 1, "per_page": 200}, timeout=20)
            users = r.json().get("users", []) if r.status_code == 200 else []
            uid = next((u["id"] for u in users if u.get("email", "").lower() == reg_email.lower()), None)
            if uid:
                httpx.request("DELETE", f"{url}/auth/v1/admin/users/{uid}", headers=h, timeout=20)
                print("  deleted throwaway auth user")
        except Exception as e:
            print(f"  auth cleanup warning: {e}")


if __name__ == "__main__":
    try:
        run_checks()
    finally:
        cleanup()
    print(f"\n==== {passed} passed, {failed} failed ====")
    sys.exit(1 if failed else 0)
