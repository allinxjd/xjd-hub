"""Hub 审核路由 — 审核队列 / 批准 / 拒绝."""

from __future__ import annotations

import json
import logging
import time

from aiohttp import web

from hub.db import HubDB

logger = logging.getLogger(__name__)


def setup_review_routes(app: web.Application, db: HubDB) -> None:

    async def _require_reviewer(request: web.Request) -> dict | None:
        user = request.get("hub_user")
        if not user or user["role"] not in ("reviewer", "admin"):
            return None
        return user

    async def list_pending(request: web.Request) -> web.Response:
        user = await _require_reviewer(request)
        if not user:
            return web.json_response({"error": "Reviewer role required"}, status=403)

        rows = await db.fetchall(
            """SELECT s.skill_id, s.name, s.slug, s.description, s.author_id,
                      s.version, s.tools, s.price, s.created_at,
                      r.id as review_id, r.tools_risk
               FROM hub_skills s
               LEFT JOIN hub_reviews r ON s.skill_id = r.skill_id AND r.status = 'pending'
               WHERE s.status = 'pending_review'
               ORDER BY s.created_at ASC""",
        )
        for r in rows:
            if isinstance(r.get("tools"), str):
                try:
                    r["tools"] = json.loads(r["tools"])
                except (json.JSONDecodeError, TypeError):
                    r["tools"] = []
        return web.json_response({"reviews": rows})

    async def review_action(request: web.Request) -> web.Response:
        user = await _require_reviewer(request)
        if not user:
            return web.json_response({"error": "Reviewer role required"}, status=403)

        slug = request.match_info["slug"]
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        action = data.get("action", "").strip()
        if action not in ("approve", "reject"):
            return web.json_response({"error": "action must be approve or reject"}, status=400)

        skill = await db.fetchone(
            "SELECT skill_id FROM hub_skills WHERE slug = ? AND status = 'pending_review'",
            (slug,),
        )
        if not skill:
            return web.json_response({"error": "No pending skill found"}, status=404)

        new_status = "approved" if action == "approve" else "rejected"
        now = time.time()

        await db.execute(
            "UPDATE hub_skills SET status = ?, updated_at = ? WHERE skill_id = ?",
            (new_status, now, skill["skill_id"]),
        )
        await db.execute(
            """UPDATE hub_reviews SET status = ?, reviewer_id = ?, comment = ?, created_at = ?
               WHERE skill_id = ? AND status = 'pending'""",
            (action + "d", user["user_id"], data.get("comment", ""), now, skill["skill_id"]),
        )
        await db.commit()

        logger.info("Skill %s %s by %s", slug, new_status, user["username"])
        return web.json_response({"status": new_status, "slug": slug})

    app.router.add_get("/hub/api/reviews", list_pending)
    app.router.add_post("/hub/api/skills/{slug}/review", review_action)
