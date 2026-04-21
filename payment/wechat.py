"""微信支付 V3 客户端 — 移植自 xjd wechatPay.ts，纯 Python 实现."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_WX_API_BASE = "https://api.mch.weixin.qq.com"


@dataclass
class WeChatPayConfig:
    mch_id: str = ""
    app_id: str = ""
    api_key_v3: str = ""
    serial_no: str = ""
    private_key: str = ""
    notify_url: str = ""

    @classmethod
    def from_env(cls) -> "WeChatPayConfig":
        pk = ""
        pk_path = os.environ.get("WECHAT_PRIVATE_KEY_PATH", "")
        if pk_path:
            try:
                with open(pk_path, "r") as f:
                    pk = f.read()
            except OSError as e:
                logger.warning("Failed to read private key: %s", e)
        return cls(
            mch_id=os.environ.get("WECHAT_MCH_ID", ""),
            app_id=os.environ.get("WECHAT_APP_ID", ""),
            api_key_v3=os.environ.get("WECHAT_API_KEY_V3", ""),
            serial_no=os.environ.get("WECHAT_SERIAL_NO", ""),
            private_key=pk,
            notify_url=os.environ.get("WECHAT_NOTIFY_URL", ""),
        )


class WeChatPayClient:
    """微信支付 V3 API 客户端."""

    def __init__(self, config: WeChatPayConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(timeout=15.0)
        self._pk = serialization.load_pem_private_key(
            config.private_key.encode(), password=None,
        ) if config.private_key else None

    def _sign(self, message: str) -> str:
        if not self._pk:
            raise RuntimeError("Private key not loaded")
        sig = self._pk.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()

    def _build_auth_header(self, method: str, url_path: str, body: str) -> str:
        ts = str(int(time.time()))
        nc = secrets.token_hex(16)
        message = f"{method}\n{url_path}\n{ts}\n{nc}\n{body}\n"
        signature = self._sign(message)
        c = self._config
        return (
            f'WECHATPAY2-SHA256-RSA2048 mchid="{c.mch_id}",'
            f'nonce_str="{nc}",timestamp="{ts}",'
            f'serial_no="{c.serial_no}",signature="{signature}"'
        )
    # PLACEHOLDER_CONTINUE

    @staticmethod
    def generate_order_no() -> str:
        now = datetime.now()
        prefix = now.strftime("%Y%m%d%H%M%S")
        rand = secrets.token_hex(4)
        return f"HUB{prefix}{rand}"

    async def _post(self, api_path: str, body: str) -> dict:
        auth = self._build_auth_header("POST", api_path, body)
        resp = await self._client.post(
            f"{_WX_API_BASE}{api_path}",
            content=body,
            headers={
                "Authorization": auth,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "XjdHub/1.0",
            },
        )
        data = resp.json()
        if resp.status_code >= 400:
            raise RuntimeError(f"WeChat API error ({resp.status_code}): {json.dumps(data)}")
        return data

    async def create_native_order(
        self, order_no: str, description: str, amount_fen: int,
    ) -> dict:
        body = json.dumps({
            "appid": self._config.app_id,
            "mchid": self._config.mch_id,
            "description": description,
            "out_trade_no": order_no,
            "notify_url": self._config.notify_url,
            "amount": {"total": amount_fen, "currency": "CNY"},
        })
        data = await self._post("/v3/pay/transactions/native", body)
        return {"code_url": data.get("code_url", "")}

    async def create_h5_order(
        self, order_no: str, description: str, amount_fen: int, payer_ip: str,
    ) -> dict:
        body = json.dumps({
            "appid": self._config.app_id,
            "mchid": self._config.mch_id,
            "description": description,
            "out_trade_no": order_no,
            "notify_url": self._config.notify_url,
            "amount": {"total": amount_fen, "currency": "CNY"},
            "scene_info": {
                "payer_client_ip": payer_ip,
                "h5_info": {"type": "Wap"},
            },
        })
        data = await self._post("/v3/pay/transactions/h5", body)
        return {"h5_url": data.get("h5_url", "")}
    def verify_and_decrypt_notify(self, body: str) -> Optional[dict]:
        """解密微信支付回调通知，返回解密后的 JSON 或 None."""
        try:
            parsed = json.loads(body)
            resource = parsed.get("resource", {})
            ciphertext_b64 = resource.get("ciphertext", "")
            associated_data = resource.get("associated_data", "")
            nonce = resource.get("nonce", "")

            key = self._config.api_key_v3.encode("utf-8")
            ciphertext = base64.b64decode(ciphertext_b64)
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(
                nonce.encode("utf-8"), ciphertext, associated_data.encode("utf-8"),
            )
            return json.loads(plaintext.decode("utf-8"))
        except Exception as e:
            logger.warning("Notify decrypt failed: %s", e)
            return None

    async def close(self) -> None:
        await self._client.aclose()
