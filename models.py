"""Hub 数据模型 — 用户、技能、审核、购买、评分."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


def _now() -> float:
    return time.time()


def _uuid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class HubUser:
    user_id: str = ""
    username: str = ""
    email: str = ""
    password_hash: str = ""
    role: str = "user"
    display_name: str = ""
    public_key: str = ""
    balance: float = 0.0
    created_at: float = 0.0
    last_login: float = 0.0
    active: bool = True

    def __post_init__(self) -> None:
        if not self.user_id:
            self.user_id = _uuid()
        if not self.created_at:
            self.created_at = _now()


@dataclass
class HubSkill:
    skill_id: str = ""
    name: str = ""
    slug: str = ""
    description: str = ""
    author_id: str = ""
    version: str = "1.0.0"
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    price: float = 0.0
    status: str = "pending_review"
    content: str = ""
    content_hash: str = ""
    signature: str = ""
    downloads: int = 0
    rating_avg: float = 0.0
    rating_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.skill_id:
            self.skill_id = _uuid()
        now = _now()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


@dataclass
class HubSkillVersion:
    id: int = 0
    skill_id: str = ""
    version: str = ""
    content: str = ""
    content_hash: str = ""
    signature: str = ""
    changelog: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()


@dataclass
class HubReview:
    id: int = 0
    skill_id: str = ""
    reviewer_id: str = ""
    status: str = "pending"
    comment: str = ""
    tools_risk: str = "low"
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()


@dataclass
class HubPurchase:
    id: int = 0
    user_id: str = ""
    skill_id: str = ""
    version: str = ""
    price_paid: float = 0.0
    license_key: str = ""
    payment_method: str = "credit"
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()


@dataclass
class HubRating:
    id: int = 0
    user_id: str = ""
    skill_id: str = ""
    score: int = 5
    comment: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()
