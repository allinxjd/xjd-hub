"""Hub 管理后台 API."""

from __future__ import annotations

import time
from aiohttp import web
from hub.db import HubDB


def _require_admin(request):
    user = request.get("hub_user")
    if not user or user.get("role") != "admin":
        return None, web.json_response({"error": "Forbidden"}, status=403)
    return user, None


def _paginate(request):
    page = max(1, int(request.query.get("page", 1)))
    size = min(100, max(1, int(request.query.get("page_size", 20))))
    return page, size, (page - 1) * size


def setup_admin_routes(app: web.Application, db: HubDB) -> None:

    async def dashboard(request: web.Request) -> web.Response:
        _, err = _require_admin(request)
        if err:
            return err
        total_users = (await db.fetchone("SELECT COUNT(*) as c FROM hub_users"))["c"]
        total_recharge = (await db.fetchone(
            "SELECT COALESCE(SUM(amount_yuan),0) as c FROM hub_recharge_orders WHERE status='paid'"
        ))["c"]
        total_consumed = (await db.fetchone(
            "SELECT COALESCE(SUM(CASE WHEN amount<0 THEN ABS(amount) ELSE 0 END),0) as c FROM hub_transactions"
        ))["c"]
        total_skills = (await db.fetchone("SELECT COUNT(*) as c FROM hub_skills"))["c"]
        total_credits = (await db.fetchone(
            "SELECT COALESCE(SUM(CASE WHEN type='recharge' THEN amount ELSE 0 END),0) as c FROM hub_transactions"
        ))["c"]
        recent = await db.fetchall(
            "SELECT o.*, u.username FROM hub_recharge_orders o "
            "LEFT JOIN hub_users u ON o.user_id=u.user_id "
            "ORDER BY o.created_at DESC LIMIT 10"
        )
        return web.json_response({
            "total_users": total_users,
            "total_recharge_yuan": total_recharge,
            "total_consumed_credits": total_consumed,
            "total_credits_issued": total_credits,
            "total_skills": total_skills,
            "recent_orders": [dict(r) for r in recent],
        })

    # ── Users ──

    async def users_list(request: web.Request) -> web.Response:
        _, err = _require_admin(request)
        if err:
            return err
        page, size, offset = _paginate(request)
        q = request.query.get("q", "").strip()
        where, params = "", []
        if q:
            where = "WHERE username LIKE ?"
            params.append(f"%{q}%")
        total = (await db.fetchone(f"SELECT COUNT(*) as c FROM hub_users {where}", params))["c"]
        rows = await db.fetchall(
            f"SELECT user_id, username, email, role, balance, active, created_at, last_login "
            f"FROM hub_users {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        )
        return web.json_response({"total": total, "page": page, "page_size": size, "users": [dict(r) for r in rows]})

    async def user_detail(request: web.Request) -> web.Response:
        _, err = _require_admin(request)
        if err:
            return err
        uid = request.match_info["user_id"]
        user = await db.fetchone("SELECT * FROM hub_users WHERE user_id=?", (uid,))
        if not user:
            return web.json_response({"error": "User not found"}, status=404)
        txns = await db.fetchall(
            "SELECT * FROM hub_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (uid,)
        )
        u = dict(user)
        u.pop("password_hash", None)
        return web.json_response({"user": u, "transactions": [dict(t) for t in txns]})

    async def user_update(request: web.Request) -> web.Response:
        _, err = _require_admin(request)
        if err:
            return err
        uid = request.match_info["user_id"]
        user = await db.fetchone("SELECT * FROM hub_users WHERE user_id=?", (uid,))
        if not user:
            return web.json_response({"error": "User not found"}, status=404)
        body = await request.json()
        updates, params = [], []
        if "role" in body and body["role"] in ("user", "admin", "reviewer"):
            updates.append("role=?")
            params.append(body["role"])
        if "active" in body:
            updates.append("active=?")
            params.append(1 if body["active"] else 0)
        if updates:
            params.append(uid)
            await db.execute(f"UPDATE hub_users SET {','.join(updates)} WHERE user_id=?", params)
        adj = body.get("balance_adjustment")
        if adj and float(adj) != 0:
            adj = float(adj)
            new_bal = await db.fetchone("SELECT balance FROM hub_users WHERE user_id=?", (uid,))
            new_balance = new_bal["balance"] + adj
            await db.execute("UPDATE hub_users SET balance=? WHERE user_id=?", (new_balance, uid))
            await db.execute(
                "INSERT INTO hub_transactions (user_id,type,amount,balance_after,description,created_at) VALUES (?,?,?,?,?,?)",
                (uid, "admin_adjust", adj, new_balance, body.get("reason", "Admin adjustment"), time.time()),
            )
        return web.json_response({"ok": True})

    # ── Orders ──

    async def orders_list(request: web.Request) -> web.Response:
        _, err = _require_admin(request)
        if err:
            return err
        page, size, offset = _paginate(request)
        status = request.query.get("status", "")
        where, params = "", []
        if status:
            where = "WHERE o.status=?"
            params.append(status)
        total = (await db.fetchone(
            f"SELECT COUNT(*) as c FROM hub_recharge_orders o {where}", params
        ))["c"]
        rows = await db.fetchall(
            f"SELECT o.*, u.username FROM hub_recharge_orders o "
            f"LEFT JOIN hub_users u ON o.user_id=u.user_id "
            f"{where} ORDER BY o.created_at DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        )
        return web.json_response({"total": total, "page": page, "page_size": size, "orders": [dict(r) for r in rows]})

    # ── Transactions ──

    async def transactions_list(request: web.Request) -> web.Response:
        _, err = _require_admin(request)
        if err:
            return err
        page, size, offset = _paginate(request)
        tx_type = request.query.get("type", "")
        where, params = "", []
        if tx_type:
            where = "WHERE t.type=?"
            params.append(tx_type)
        total = (await db.fetchone(
            f"SELECT COUNT(*) as c FROM hub_transactions t {where}", params
        ))["c"]
        rows = await db.fetchall(
            f"SELECT t.*, u.username FROM hub_transactions t "
            f"LEFT JOIN hub_users u ON t.user_id=u.user_id "
            f"{where} ORDER BY t.created_at DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        )
        return web.json_response({"total": total, "page": page, "page_size": size, "transactions": [dict(r) for r in rows]})

    # ── Skills ──

    async def skills_list(request: web.Request) -> web.Response:
        _, err = _require_admin(request)
        if err:
            return err
        page, size, offset = _paginate(request)
        status = request.query.get("status", "")
        where, params = "", []
        if status:
            where = "WHERE s.status=?"
            params.append(status)
        total = (await db.fetchone(
            f"SELECT COUNT(*) as c FROM hub_skills s {where}", params
        ))["c"]
        rows = await db.fetchall(
            f"SELECT s.*, u.username as author_name FROM hub_skills s "
            f"LEFT JOIN hub_users u ON s.author_id=u.user_id "
            f"{where} ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        )
        return web.json_response({"total": total, "page": page, "page_size": size, "skills": [dict(r) for r in rows]})

    # ── Register routes ──

    app.router.add_get("/hub/api/admin/dashboard", dashboard)
    app.router.add_get("/hub/api/admin/users", users_list)
    app.router.add_get("/hub/api/admin/users/{user_id}", user_detail)
    app.router.add_put("/hub/api/admin/users/{user_id}", user_update)
    app.router.add_get("/hub/api/admin/orders", orders_list)
    app.router.add_get("/hub/api/admin/transactions", transactions_list)
    app.router.add_get("/hub/api/admin/skills", skills_list)
