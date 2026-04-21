"""Hub 技能路由 — 搜索 / 详情 / 上传 / 下载 / 版本管理."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from aiohttp import web

from hub.db import HubDB

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}[a-z0-9]$")


def _make_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    return slug or "skill"


def setup_skill_routes(app: web.Application, db: HubDB) -> None:

    async def search(request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        category = request.query.get("category", "").strip()
        tag = request.query.get("tag", "").strip()
        sort = request.query.get("sort", "downloads")
        try:
            page = max(1, int(request.query.get("page", "1")))
            per_page = min(50, max(1, int(request.query.get("per_page", "20"))))
        except (ValueError, TypeError):
            page, per_page = 1, 20

        conditions = ["status = 'approved'"]
        params: list[Any] = []

        if query:
            conditions.append("(name LIKE ? OR description LIKE ? OR tags LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if category:
            conditions.append("category = ?")
            params.append(category)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')

        where = " AND ".join(conditions)
        _SORT_MAP = {"downloads": "downloads DESC", "rating": "rating_avg DESC",
                     "newest": "created_at DESC", "name": "name ASC"}
        sort_col = _SORT_MAP.get(sort)
        if not sort_col:
            sort_col = "downloads DESC"

        offset = (page - 1) * per_page
        rows = await db.fetchall(
            f"SELECT skill_id, name, slug, description, author_id, version, "
            f"category, tags, tools, price, downloads, rating_avg, rating_count, created_at "
            f"FROM hub_skills WHERE {where} ORDER BY {sort_col} LIMIT ? OFFSET ?",
            tuple(params + [per_page, offset]),
        )

        for r in rows:
            for k in ("tags", "tools"):
                if isinstance(r.get(k), str):
                    try:
                        r[k] = json.loads(r[k])
                    except (json.JSONDecodeError, TypeError):
                        r[k] = []

        count_row = await db.fetchone(
            f"SELECT COUNT(*) as total FROM hub_skills WHERE {where}",
            tuple(params),
        )
        total = count_row["total"] if count_row else 0

        return web.json_response({"skills": rows, "total": total, "page": page})

    async def detail(request: web.Request) -> web.Response:
        slug = request.match_info["slug"]
        row = await db.fetchone(
            "SELECT * FROM hub_skills WHERE slug = ?", (slug,),
        )
        if not row:
            return web.json_response({"error": "Not found"}, status=404)
        for k in ("tags", "tools"):
            if isinstance(row.get(k), str):
                try:
                    row[k] = json.loads(row[k])
                except (json.JSONDecodeError, TypeError):
                    row[k] = []
        row.pop("content", None)
        return web.json_response({"skill": row})

    async def download(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        slug = request.match_info["slug"]
        row = await db.fetchone(
            "SELECT * FROM hub_skills WHERE slug = ? AND status = 'approved'", (slug,),
        )
        if not row:
            return web.json_response({"error": "Not found"}, status=404)

        if row["price"] > 0:
            purchase = await db.fetchone(
                "SELECT license_key FROM hub_purchases WHERE user_id = ? AND skill_id = ?",
                (user["user_id"], row["skill_id"]),
            )
            if not purchase and row["author_id"] != user["user_id"]:
                return web.json_response({"error": "License required", "price": row["price"]}, status=402)
            if purchase:
                from hub.payment.license import LicenseManager
                lm = LicenseManager(request.app["hub_jwt"])
                if not lm.verify_for_skill(purchase["license_key"], slug):
                    return web.json_response({"error": "License invalid or expired"}, status=403)

        await db.execute(
            "UPDATE hub_skills SET downloads = downloads + 1 WHERE skill_id = ?",
            (row["skill_id"],),
        )
        await db.commit()

        author = await db.fetchone(
            "SELECT public_key, username FROM hub_users WHERE user_id = ?",
            (row["author_id"],),
        )

        return web.json_response({
            "content": row["content"],
            "content_hash": row["content_hash"],
            "signature": row["signature"],
            "author_pubkey": author["public_key"] if author else "",
            "author_name": author["username"] if author else "",
            "version": row["version"],
        })

    async def publish(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        content = data.get("content", "").strip()
        name = data.get("name", "").strip()
        if not content or not name:
            return web.json_response({"error": "name and content required"}, status=400)
        if len(name) > 200:
            return web.json_response({"error": "name too long (max 200)"}, status=400)
        if len(content) > 10 * 1024 * 1024:
            return web.json_response({"error": "content too large (max 10MB)"}, status=400)
        description = data.get("description", "")[:5000]

        slug = data.get("slug") or _make_slug(name)
        if not _SLUG_RE.match(slug):
            slug = _make_slug(name)

        existing = await db.fetchone("SELECT skill_id FROM hub_skills WHERE slug = ?", (slug,))
        if existing:
            return web.json_response({"error": f"Slug '{slug}' already taken"}, status=409)

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        from agent.skills.sandbox_policy import assess_tools_risk
        tools = data.get("tools", [])
        tools_risk = assess_tools_risk(tools)

        import uuid
        skill_id = uuid.uuid4().hex[:16]
        now = time.time()

        await db.execute(
            """INSERT INTO hub_skills
               (skill_id, name, slug, description, author_id, version, category,
                tags, tools, price, status, content, content_hash, signature,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, ?, ?, ?, ?)""",
            (skill_id, name, slug, description, user["user_id"],
             data.get("version", "1.0.0"), data.get("category", "general"),
             json.dumps(data.get("tags", []), ensure_ascii=False),
             json.dumps(tools, ensure_ascii=False),
             max(0.0, float(data.get("price", 0))),
             content, content_hash, data.get("signature", ""),
             now, now),
        )

        await db.execute(
            """INSERT INTO hub_skill_versions (skill_id, version, content, content_hash, signature, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (skill_id, data.get("version", "1.0.0"), content, content_hash,
             data.get("signature", ""), now),
        )

        await db.execute(
            """INSERT INTO hub_reviews (skill_id, status, tools_risk, created_at)
               VALUES (?, 'pending', ?, ?)""",
            (skill_id, tools_risk, now),
        )
        await db.commit()

        return web.json_response({
            "skill_id": skill_id, "slug": slug, "status": "pending_review",
            "tools_risk": tools_risk,
        }, status=201)

    async def push_version(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        slug = request.match_info["slug"]
        skill = await db.fetchone(
            "SELECT * FROM hub_skills WHERE slug = ? AND author_id = ?",
            (slug, user["user_id"]),
        )
        if not skill:
            return web.json_response({"error": "Not found or not owner"}, status=404)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        content = data.get("content", "").strip()
        version = data.get("version", "").strip()
        if not content or not version:
            return web.json_response({"error": "content and version required"}, status=400)

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        now = time.time()

        await db.execute(
            """UPDATE hub_skills SET version = ?, content = ?, content_hash = ?,
               signature = ?, status = 'pending_review', updated_at = ?
               WHERE skill_id = ?""",
            (version, content, content_hash, data.get("signature", ""), now, skill["skill_id"]),
        )
        await db.execute(
            """INSERT INTO hub_skill_versions (skill_id, version, content, content_hash, signature, changelog, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (skill["skill_id"], version, content, content_hash,
             data.get("signature", ""), data.get("changelog", ""), now),
        )
        await db.commit()

        return web.json_response({"status": "pending_review", "version": version})

    async def rate(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        slug = request.match_info["slug"]
        skill = await db.fetchone("SELECT skill_id FROM hub_skills WHERE slug = ?", (slug,))
        if not skill:
            return web.json_response({"error": "Not found"}, status=404)

        try:
            data = await request.json()
            score = int(data.get("score", 0))
        except (json.JSONDecodeError, ValueError, TypeError):
            return web.json_response({"error": "Invalid score"}, status=400)

        if not 1 <= score <= 5:
            return web.json_response({"error": "Score must be 1-5"}, status=400)

        now = time.time()
        await db.execute(
            """INSERT INTO hub_ratings (user_id, skill_id, score, comment, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, skill_id) DO UPDATE SET score = ?, comment = ?, created_at = ?""",
            (user["user_id"], skill["skill_id"], score, data.get("comment", ""), now,
             score, data.get("comment", ""), now),
        )

        avg_row = await db.fetchone(
            "SELECT AVG(score) as avg, COUNT(*) as cnt FROM hub_ratings WHERE skill_id = ?",
            (skill["skill_id"],),
        )
        if avg_row:
            await db.execute(
                "UPDATE hub_skills SET rating_avg = ?, rating_count = ? WHERE skill_id = ?",
                (avg_row["avg"] or 0, avg_row["cnt"] or 0, skill["skill_id"]),
            )
        await db.commit()
        return web.json_response({"status": "ok"})

    async def purchase(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        slug = request.match_info["slug"]
        skill = await db.fetchone(
            "SELECT * FROM hub_skills WHERE slug = ? AND status = 'approved'", (slug,),
        )
        if not skill:
            return web.json_response({"error": "Not found"}, status=404)

        if skill["price"] <= 0:
            return web.json_response({"error": "Skill is free, no purchase needed"}, status=400)

        existing = await db.fetchone(
            "SELECT license_key FROM hub_purchases WHERE user_id = ? AND skill_id = ?",
            (user["user_id"], skill["skill_id"]),
        )
        if existing:
            return web.json_response({
                "license_key": existing["license_key"],
                "message": "Already purchased",
            })

        from hub.payment.credits import CreditManager
        from hub.payment.license import LicenseManager
        credits = CreditManager(db)
        license_mgr = LicenseManager(request.app["hub_jwt"])

        try:
            author_id = skill["author_id"]
            await credits.transfer(user["user_id"], author_id, skill["price"])
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=402)

        license_key = license_mgr.generate(
            user["user_id"], slug, version=skill["version"],
        )

        import time
        try:
            await db.execute(
                """INSERT INTO hub_purchases (user_id, skill_id, version, price_paid, license_key, payment_method, created_at)
                   VALUES (?, ?, ?, ?, ?, 'credit', ?)""",
                (user["user_id"], skill["skill_id"], skill["version"],
                 skill["price"], license_key, time.time()),
            )
            await db.commit()
        except Exception:
            # UNIQUE 约束冲突 = 并发重复购买，退款
            await credits.add_credits(user["user_id"], skill["price"], reason="refund:duplicate_purchase")
            existing = await db.fetchone(
                "SELECT license_key FROM hub_purchases WHERE user_id = ? AND skill_id = ?",
                (user["user_id"], skill["skill_id"]),
            )
            if existing:
                return web.json_response({
                    "license_key": existing["license_key"],
                    "message": "Already purchased (concurrent)",
                })
            return web.json_response({"error": "Purchase failed"}, status=500)

        return web.json_response({"license_key": license_key, "price_paid": skill["price"]})

    app.router.add_get("/hub/api/skills", search)
    app.router.add_get("/hub/api/skills/{slug}", detail)
    app.router.add_get("/hub/api/skills/{slug}/download", download)
    app.router.add_post("/hub/api/skills", publish)
    app.router.add_put("/hub/api/skills/{slug}/versions", push_version)
    app.router.add_post("/hub/api/skills/{slug}/rate", rate)
    app.router.add_post("/hub/api/skills/{slug}/purchase", purchase)
