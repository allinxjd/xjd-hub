"""XjdHub 服务端 — aiohttp 应用，可独立运行或挂载到 web server."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from aiohttp import web

from hub.auth_utils import JWTManager
from hub.db import HubDB
from hub.routes.admin import setup_admin_routes
from hub.routes.auth import setup_auth_routes
from hub.routes.reviews import setup_review_routes
from hub.routes.recharge import setup_recharge_routes
from hub.routes.skills import setup_skill_routes

logger = logging.getLogger(__name__)


async def _jwt_middleware(app: web.Application, handler):
    jwt_mgr: JWTManager = app["hub_jwt"]
    db: HubDB = app["hub_db"]

    async def middleware(request: web.Request) -> web.Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jwt_mgr.verify_token(token)
            if payload and payload.get("sub"):
                user = await db.fetchone(
                    "SELECT * FROM hub_users WHERE user_id = ? AND active = 1",
                    (payload["sub"],),
                )
                if user:
                    request["hub_user"] = user
        return await handler(request)

    return middleware


async def _security_headers_middleware(app: web.Application, handler):
    async def middleware(request: web.Request) -> web.Response:
        response = await handler(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    return middleware


def create_hub_app(
    db_path: Optional[str] = None,
    jwt_secret: str = "",
) -> web.Application:
    """创建 Hub aiohttp 应用."""
    app = web.Application(middlewares=[_security_headers_middleware, _jwt_middleware])

    db = HubDB(db_path)
    jwt_mgr = JWTManager(secret_key=jwt_secret, expires_hours=168)

    app["hub_db"] = db
    app["hub_jwt"] = jwt_mgr

    setup_auth_routes(app, db, jwt_mgr)
    setup_skill_routes(app, db)
    setup_review_routes(app, db)
    setup_recharge_routes(app, db)
    setup_admin_routes(app, db)

    _static_dir = Path(__file__).parent / "static"
    if _static_dir.is_dir():
        app.router.add_get("/hub/admin/", lambda r: web.FileResponse(_static_dir / "admin.html"))
        app.router.add_get("/hub/admin", lambda r: web.HTTPFound("/hub/admin/"))
        app.router.add_static("/hub/static/", _static_dir)

    async def on_startup(_app: web.Application) -> None:
        await db.connect()
        try:
            from hub.payment.wechat import WeChatPayConfig, WeChatPayClient
            config = WeChatPayConfig.from_env()
            if config.mch_id:
                _app["wechat_pay"] = WeChatPayClient(config)
                logger.info("WeChat Pay initialized: mch_id=%s", config.mch_id)
        except Exception as e:
            logger.debug("WeChat Pay init skipped: %s", e)
        logger.info("XjdHub server started")

    async def on_cleanup(_app: web.Application) -> None:
        wechat = _app.get("wechat_pay")
        if wechat:
            await wechat.close()
        await db.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app


def run_hub_server(
    host: str = "0.0.0.0",
    port: int = 8900,
    db_path: Optional[str] = None,
    jwt_secret: str = "",
) -> None:
    """独立运行 Hub 服务."""
    app = create_hub_app(db_path=db_path, jwt_secret=jwt_secret)
    web.run_app(app, host=host, port=port, print=lambda msg: logger.info(msg))
