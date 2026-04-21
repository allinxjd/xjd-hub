"""内部积分系统 — 充值 / 扣款 / 余额查询."""

from __future__ import annotations

import logging
import time

from hub.db import HubDB

logger = logging.getLogger(__name__)

PLATFORM_COMMISSION = 0.20


class CreditManager:
    """积分管理器."""

    def __init__(self, db: HubDB) -> None:
        self._db = db

    async def get_balance(self, user_id: str) -> float:
        row = await self._db.fetchone(
            "SELECT balance FROM hub_users WHERE user_id = ?", (user_id,),
        )
        return float(row["balance"]) if row else 0.0

    async def add_credits(self, user_id: str, amount: float, reason: str = "", order_no: str = "") -> float:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        await self._db.execute(
            "UPDATE hub_users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id),
        )
        await self._db.commit()
        new_balance = await self.get_balance(user_id)
        tx_type = "recharge" if "recharge" in reason else "refund" if "refund" in reason else "credit"
        await self._db.execute(
            "INSERT INTO hub_transactions (user_id, type, amount, balance_after, order_no, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, tx_type, amount, new_balance, order_no, reason, time.time()),
        )
        await self._db.commit()
        logger.info("Credits added: user=%s amount=%.2f reason=%s balance=%.2f",
                     user_id, amount, reason, new_balance)
        return new_balance

    async def deduct_credits(self, user_id: str, amount: float, reason: str = "", order_no: str = "") -> float:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        balance = await self.get_balance(user_id)
        if balance < amount:
            raise ValueError(f"Insufficient balance: {balance:.2f} < {amount:.2f}")
        await self._db.execute(
            "UPDATE hub_users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id),
        )
        await self._db.commit()
        new_balance = await self.get_balance(user_id)
        await self._db.execute(
            "INSERT INTO hub_transactions (user_id, type, amount, balance_after, order_no, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, "purchase", -amount, new_balance, order_no, reason, time.time()),
        )
        await self._db.commit()
        logger.info("Credits deducted: user=%s amount=%.2f reason=%s balance=%.2f",
                     user_id, amount, reason, new_balance)
        return new_balance

    async def transfer(self, from_id: str, to_id: str, amount: float, commission: float = PLATFORM_COMMISSION) -> dict:
        """转账（含平台佣金）."""
        author_amount = amount * (1 - commission)
        await self.deduct_credits(from_id, amount, reason=f"purchase→{to_id}")
        await self.add_credits(to_id, author_amount, reason=f"sale←{from_id}")
        return {
            "total": amount,
            "commission": amount * commission,
            "author_received": author_amount,
        }
