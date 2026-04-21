"""技能签名与校验 — ed25519 密钥管理 + 内容签名 + 验证.

不依赖 PyNaCl 时回退到 HMAC-SHA256 签名（降级模式）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_KEYPAIR_FILE = "hub_keypair.json"

try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError
    _HAS_NACL = True
except ImportError:
    _HAS_NACL = False


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SkillSigner:
    """技能签名器."""

    def __init__(self, home_dir: Optional[Path] = None) -> None:
        if home_dir is None:
            from agent.core.config import get_home
            home_dir = get_home()
        self._home = home_dir
        self._keypair_path = self._home / _KEYPAIR_FILE
        self._signing_key: Optional[bytes] = None
        self._public_key: Optional[bytes] = None
        self._hmac_secret: str = ""
        self._load_keys()

    def _load_keys(self) -> None:
        if not self._keypair_path.exists():
            return
        try:
            data = json.loads(self._keypair_path.read_text(encoding="utf-8"))
            if _HAS_NACL and data.get("signing_key"):
                self._signing_key = base64.b64decode(data["signing_key"])
                self._public_key = base64.b64decode(data["public_key"])
            self._hmac_secret = data.get("hmac_secret", "")
        except (json.JSONDecodeError, KeyError, Exception) as e:
            logger.debug("Failed to load keypair: %s", e)

    def generate_keys(self) -> str:
        """生成新密钥对，返回 base64 编码的公钥."""
        data: dict = {}

        if _HAS_NACL:
            sk = SigningKey.generate()
            self._signing_key = bytes(sk)
            self._public_key = bytes(sk.verify_key)
            data["signing_key"] = base64.b64encode(self._signing_key).decode()
            data["public_key"] = base64.b64encode(self._public_key).decode()

        self._hmac_secret = secrets.token_hex(32)
        data["hmac_secret"] = self._hmac_secret

        self._keypair_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )
        try:
            os.chmod(str(self._keypair_path), 0o600)
        except OSError:
            pass

        if self._public_key:
            return base64.b64encode(self._public_key).decode()
        return f"hmac:{self._hmac_secret[:16]}..."

    def get_public_key(self) -> str:
        if self._public_key:
            return base64.b64encode(self._public_key).decode()
        return ""

    def sign(self, content: str) -> str:
        """签名内容，返回 base64 编码的签名."""
        digest = content_hash(content)

        if _HAS_NACL and self._signing_key:
            sk = SigningKey(self._signing_key)
            signed = sk.sign(digest.encode())
            return base64.b64encode(signed.signature).decode()

        if self._hmac_secret:
            sig = hmac.new(
                self._hmac_secret.encode(), digest.encode(), hashlib.sha256,
            ).digest()
            return "hmac:" + base64.b64encode(sig).decode()

        raise RuntimeError("No signing key available. Run generate_keys() first.")

    @staticmethod
    def verify(content: str, signature: str, public_key: str) -> bool:
        """验证签名."""
        digest = content_hash(content)

        if signature.startswith("hmac:"):
            logger.warning("HMAC signatures cannot be verified client-side, rejecting")
            return False

        if not _HAS_NACL:
            logger.warning("PyNaCl not installed, cannot verify ed25519 signature — rejecting")
            return False

        try:
            vk = VerifyKey(base64.b64decode(public_key))
            sig_bytes = base64.b64decode(signature)
            vk.verify(digest.encode(), sig_bytes)
            return True
        except (BadSignatureError, Exception) as e:
            logger.warning("Signature verification failed: %s", e)
            return False

    @staticmethod
    def verify_hash(content: str, expected_hash: str) -> bool:
        """验证内容哈希."""
        return content_hash(content) == expected_hash
