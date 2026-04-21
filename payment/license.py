"""许可证生成与验证 — JWT 格式，支持离线验证."""

from __future__ import annotations

import logging
import time
from typing import Optional

from hub.auth_utils import JWTManager

logger = logging.getLogger(__name__)


class LicenseManager:
    """许可证管理器."""

    def __init__(self, jwt_manager: JWTManager) -> None:
        self._jwt = jwt_manager

    def generate(
        self,
        user_id: str,
        skill_slug: str,
        version: str = "",
        license_type: str = "perpetual",
        expires_days: int = 0,
    ) -> str:
        """生成许可证密钥."""
        extra = {
            "skill": skill_slug,
            "version": version,
            "type": license_type,
            "iss": "xjdhub",
        }
        if license_type == "subscription" and expires_days > 0:
            jwt_mgr = JWTManager(
                secret_key=self._jwt._secret,
                expires_hours=expires_days * 24,
            )
            return jwt_mgr.create_token(user_id, role="license", extra=extra)

        # 买断制: 100 年有效期
        jwt_mgr = JWTManager(
            secret_key=self._jwt._secret,
            expires_hours=100 * 365 * 24,
        )
        return jwt_mgr.create_token(user_id, role="license", extra=extra)

    def verify(self, license_key: str) -> Optional[dict]:
        """验证许可证，返回 payload 或 None."""
        payload = self._jwt.verify_token(license_key)
        if not payload:
            return None
        if payload.get("role") != "license":
            return None
        exp = payload.get("exp", 0)
        if exp and exp > 0 and time.time() > exp:
            return None
        return payload

    def verify_for_skill(self, license_key: str, skill_slug: str) -> bool:
        """验证许可证是否适用于指定技能."""
        payload = self.verify(license_key)
        if not payload:
            return False
        return payload.get("skill") == skill_slug
