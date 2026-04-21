"""Hub 充值路由 — 微信支付创建订单 / 回调 / 状态查询."""

from __future__ import annotations

import json
import logging
import time

from aiohttp import web

from hub.db import HubDB
from hub.payment.credits import CreditManager
from hub.payment.recharge import RECHARGE_PACKAGES, get_package, yuan_to_fen

logger = logging.getLogger(__name__)


def setup_recharge_routes(app: web.Application, db: HubDB) -> None:

    async def packages(request: web.Request) -> web.Response:
        return web.json_response({"packages": RECHARGE_PACKAGES})

    async def create(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        amount = data.get("amount", 0)
        pay_type = data.get("pay_type", "native")
        if pay_type not in ("native", "h5"):
            return web.json_response({"error": "pay_type must be native or h5"}, status=400)

        pkg = get_package(amount)
        if not pkg:
            valid = [p["amount_yuan"] for p in RECHARGE_PACKAGES]
            return web.json_response({"error": f"Invalid amount, valid: {valid}"}, status=400)

        wechat = app.get("wechat_pay")
        if not wechat:
            return web.json_response({"error": "WeChat Pay not configured"}, status=503)

        from hub.payment.wechat import WeChatPayClient
        order_no = WeChatPayClient.generate_order_no()
        amount_fen = yuan_to_fen(pkg["amount_yuan"])
        now = time.time()

        await db.execute(
            "INSERT INTO hub_recharge_orders "
            "(order_no, user_id, amount_yuan, amount_fen, credits, status, pay_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (order_no, user["user_id"], pkg["amount_yuan"], amount_fen,
             pkg["credits"], pay_type, now),
        )
        await db.commit()

        try:
            desc = f"XjdHub充值{pkg['amount_yuan']}元"
            if pay_type == "native":
                result = await wechat.create_native_order(order_no, desc, amount_fen)
                return web.json_response({"order_no": order_no, "code_url": result["code_url"]})
            else:
                payer_ip = request.remote or "127.0.0.1"
                result = await wechat.create_h5_order(order_no, desc, amount_fen, payer_ip)
                return web.json_response({"order_no": order_no, "h5_url": result["h5_url"]})
        except Exception as e:
            await db.execute(
                "UPDATE hub_recharge_orders SET status = 'failed' WHERE order_no = ?",
                (order_no,),
            )
            await db.commit()
            logger.error("WeChat order creation failed: %s", e)
            return web.json_response({"error": f"支付创建失败: {e}"}, status=500)

    async def notify(request: web.Request) -> web.Response:
        wechat = app.get("wechat_pay")
        if not wechat:
            return web.json_response({"code": "FAIL", "message": "Not configured"}, status=500)

        body = await request.text()
        result = wechat.verify_and_decrypt_notify(body)
        if not result:
            return web.json_response({"code": "FAIL", "message": "Decrypt failed"}, status=400)

        order_no = result.get("out_trade_no", "")
        trade_state = result.get("trade_state", "")
        tx_id = result.get("transaction_id", "")

        if not order_no:
            return web.json_response({"code": "FAIL", "message": "Missing order_no"}, status=400)

        order = await db.fetchone(
            "SELECT * FROM hub_recharge_orders WHERE order_no = ?", (order_no,),
        )
        if not order:
            return web.json_response({"code": "FAIL", "message": "Order not found"}, status=404)

        if order["status"] == "paid":
            return web.json_response({"code": "SUCCESS", "message": "OK"})

        if trade_state == "SUCCESS":
            now = time.time()
            await db.execute(
                "UPDATE hub_recharge_orders SET status='paid', wx_transaction_id=?, paid_at=? "
                "WHERE order_no=? AND status='pending'",
                (tx_id, now, order_no),
            )
            await db.commit()
            # PLACEHOLDER_CREDITS
            credits_mgr = CreditManager(db)
            await credits_mgr.add_credits(
                order["user_id"], order["credits"],
                reason=f"recharge:{order_no}", order_no=order_no,
            )
            logger.info("Recharge success: order=%s user=%s credits=%.0f",
                         order_no, order["user_id"], order["credits"])

        return web.json_response({"code": "SUCCESS", "message": "OK"})

    async def status(request: web.Request) -> web.Response:
        user = request.get("hub_user")
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)

        order_no = request.match_info["order_no"]
        order = await db.fetchone(
            "SELECT order_no, amount_yuan, credits, status, pay_type, created_at, paid_at "
            "FROM hub_recharge_orders WHERE order_no = ? AND user_id = ?",
            (order_no, user["user_id"]),
        )
        if not order:
            return web.json_response({"error": "Not found"}, status=404)
        return web.json_response({"order": order})

    app.router.add_get("/hub/api/recharge/packages", packages)
    app.router.add_post("/hub/api/recharge/create", create)
    app.router.add_post("/hub/api/recharge/notify", notify)
    app.router.add_get("/hub/api/recharge/status/{order_no}", status)
