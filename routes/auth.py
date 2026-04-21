"""Hub 认证路由 — 注册 / 登录 / 个人信息."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any

from aiohttp import web

from gateway.core.auth import JWTManager, PasswordHasher
from hub.db import HubDB

logger = logging.getLogger(__name__)

_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 300
_RATE_LIMIT_MAX = 10
_MIN_PASSWORD_LEN = 8


def setup_auth_routes(app: web.Application, db: HubDB, jwt: JWTManager) -> None:

    async def register(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        username = (data.get("username") or "").strip()
        email = (data.get("email") or "").strip()
        password = data.get("password") or ""

        if not username or not email or len(password) < _MIN_PASSWORD_LEN:
            return web.json_response(
                {"error": f"username, email, password(>={_MIN_PASSWORD_LEN}) required"}, status=400,
            )

        existing = await db.fetchone(
            "SELECT user_id FROM hub_users WHERE username = ?", (username,),
        )
        if existing:
            return web.json_response({"error": "Username taken"}, status=409)

        password_hash = PasswordHasher.hash_password(password)
        import uuid
        user_id = uuid.uuid4().hex[:16]
        now = time.time()

        await db.execute(
            """INSERT INTO hub_users
               (user_id, username, email, password_hash, role, created_at, last_login)
               VALUES (?, ?, ?, ?, 'user', ?, ?)""",
            (user_id, username, email, password_hash, now, now),
        )
        await db.commit()

        token = jwt.create_token(user_id, role="user")
        return web.json_response({
            "user_id": user_id,
            "username": username,
            "token": token,
        }, status=201)

    async def login(request: web.Request) -> web.Response:
        client_ip = request.remote or "unknown"
        now = time.time()
        _login_attempts[client_ip] = [t for t in _login_attempts[client_ip] if now - t < _RATE_LIMIT_WINDOW]
        if len(_login_attempts[client_ip]) >= _RATE_LIMIT_MAX:
            return web.json_response({"error": "Too many attempts, try later"}, status=429)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        username = (data.get("username") or "").strip()
        password = data.get("password") or ""

        if not username or not password:
            return web.json_response({"error": "username and password required"}, status=400)

        row = await db.fetchone(
            "SELECT * FROM hub_users WHERE username = ? AND active = 1", (username,),
        )
        if not row or not PasswordHasher.verify_password(password, row["password_hash"]):
            _login_attempts[client_ip].append(now)
            logger.warning("Failed login attempt: user=%s ip=%s", username, client_ip)
            return web.json_response({"error": "Invalid credentials"}, status=401)

        await db.execute(
            "UPDATE hub_users SET last_login = ? WHERE user_id = ?",
            (time.time(), row["user_id"]),
        )
        await db.commit()

        token = jwt.create_token(row["user_id"], role=row["role"])
        return web.json_response({
            "user_id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "token": token,
        })

    async def me(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)
        return web.json_response({
            "user_id": user["user_id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "display_name": user["display_name"],
            "balance": user["balance"],
            "public_key": user["public_key"],
        })

    async def update_profile(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response({"error": "Invalid JSON"}, status=400)

        allowed = ("display_name", "email", "public_key")
        updates = {k: v for k, v in data.items() if k in allowed and isinstance(v, str)}
        if not updates:
            return web.json_response({"error": "No valid fields"}, status=400)

        set_parts = []
        values = []
        for k in allowed:
            if k in updates:
                set_parts.append(f"{k} = ?")
                values.append(updates[k])
        values.append(user["user_id"])
        await db.execute(f"UPDATE hub_users SET {', '.join(set_parts)} WHERE user_id = ?", tuple(values))
        await db.commit()
        return web.json_response({"status": "ok"})

    app.router.add_post("/hub/api/auth/register", register)
    app.router.add_post("/hub/api/auth/login", login)
    app.router.add_get("/hub/api/auth/me", me)
    app.router.add_put("/hub/api/auth/me", update_profile)
