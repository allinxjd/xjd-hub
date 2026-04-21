"""Hub 认证工具 — JWT + 密码哈希（自包含，不依赖 xjd-agent）."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional


class JWTManager:
    """JWT Token 管理 (HS256, 不依赖 PyJWT)."""

    def __init__(self, secret_key: str = "", expires_hours: int = 2) -> None:
        self._secret = secret_key or secrets.token_hex(32)
        self._expires_hours = expires_hours

    def create_token(self, user_id: str, role: str = "user", extra: dict | None = None) -> str:
        now = time.time()
        payload = {"sub": user_id, "role": role, "iat": int(now), "exp": int(now + self._expires_hours * 3600)}
        if extra:
            payload.update(extra)
        header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        sig = hmac.new(self._secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        return f"{header}.{body}.{signature}"

    def verify_token(self, token: str) -> Optional[dict]:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            header_b64, body_b64, sig_b64 = parts
            expected_sig = hmac.new(self._secret.encode(), f"{header_b64}.{body_b64}".encode(), hashlib.sha256).digest()
            expected = base64.urlsafe_b64encode(expected_sig).rstrip(b"=").decode()
            if not hmac.compare_digest(sig_b64, expected):
                return None
            padding = 4 - len(body_b64) % 4
            payload = json.loads(base64.urlsafe_b64decode(body_b64 + "=" * padding))
            if payload.get("exp", 0) < time.time():
                return None
            return payload
        except (ValueError, KeyError, TypeError):
            return None


class PasswordHasher:
    """密码哈希 (PBKDF2-SHA256, 600k 迭代)."""

    ITERATIONS = 600_000

    @staticmethod
    def hash_password(password: str, salt: str = "") -> str:
        if not salt:
            salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations=PasswordHasher.ITERATIONS)
        return f"{salt}:{dk.hex()}"

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        try:
            salt, expected_hex = hashed.split(":", 1)
            for iters in (PasswordHasher.ITERATIONS, 100_000):
                dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations=iters)
                if hmac.compare_digest(dk.hex(), expected_hex):
                    return True
            return False
        except (ValueError, KeyError, TypeError):
            return False


DANGEROUS_TOOLS: frozenset[str] = frozenset({
    "run_terminal", "write_file", "edit_file", "execute_code",
    "git_command", "process_manager", "database_query", "apply_patch",
})
MODERATE_TOOLS: frozenset[str] = frozenset({
    "web_fetch", "download_file", "template_render",
})


def assess_tools_risk(tools: list[str]) -> str:
    """评估技能声明的工具风险等级."""
    tool_set = set(tools)
    if tool_set & DANGEROUS_TOOLS:
        return "high"
    if tool_set & MODERATE_TOOLS:
        return "medium"
    return "low"
