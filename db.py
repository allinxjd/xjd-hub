"""Hub 数据库 — aiosqlite + WAL 模式."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hub_users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    display_name TEXT DEFAULT '',
    public_key TEXT DEFAULT '',
    balance REAL DEFAULT 0.0,
    created_at REAL,
    last_login REAL,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS hub_skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    description TEXT DEFAULT '',
    author_id TEXT REFERENCES hub_users(user_id),
    version TEXT DEFAULT '1.0.0',
    category TEXT DEFAULT 'general',
    tags TEXT DEFAULT '[]',
    tools TEXT DEFAULT '[]',
    price REAL DEFAULT 0.0,
    status TEXT DEFAULT 'pending_review',
    content TEXT DEFAULT '',
    content_hash TEXT DEFAULT '',
    signature TEXT DEFAULT '',
    downloads INTEGER DEFAULT 0,
    rating_avg REAL DEFAULT 0.0,
    rating_count INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS hub_skill_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT REFERENCES hub_skills(skill_id),
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT DEFAULT '',
    signature TEXT DEFAULT '',
    changelog TEXT DEFAULT '',
    created_at REAL
);

CREATE TABLE IF NOT EXISTS hub_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT REFERENCES hub_skills(skill_id),
    reviewer_id TEXT REFERENCES hub_users(user_id),
    status TEXT DEFAULT 'pending',
    comment TEXT DEFAULT '',
    tools_risk TEXT DEFAULT 'low',
    created_at REAL
);

CREATE TABLE IF NOT EXISTS hub_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT REFERENCES hub_users(user_id),
    skill_id TEXT REFERENCES hub_skills(skill_id),
    version TEXT DEFAULT '',
    price_paid REAL DEFAULT 0.0,
    license_key TEXT UNIQUE,
    payment_method TEXT DEFAULT 'credit',
    created_at REAL,
    UNIQUE(user_id, skill_id)
);

CREATE TABLE IF NOT EXISTS hub_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT REFERENCES hub_users(user_id),
    skill_id TEXT REFERENCES hub_skills(skill_id),
    score INTEGER CHECK(score BETWEEN 1 AND 5),
    comment TEXT DEFAULT '',
    created_at REAL,
    UNIQUE(user_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_skills_slug ON hub_skills(slug);
CREATE INDEX IF NOT EXISTS idx_skills_category ON hub_skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_status ON hub_skills(status);
CREATE INDEX IF NOT EXISTS idx_skills_author ON hub_skills(author_id);
CREATE INDEX IF NOT EXISTS idx_versions_skill ON hub_skill_versions(skill_id);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON hub_purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_skill ON hub_purchases(skill_id);
"""


class HubDB:
    """Hub 数据库管理器."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            from agent.core.config import get_home
            db_path = str(get_home() / "hub.db")
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()
        logger.info("Hub DB initialized: %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if not self._db:
            raise RuntimeError("HubDB not connected")
        return self._db

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        return await self.db.execute(sql, params)

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cursor = await self.db.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self.db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def commit(self) -> None:
        await self.db.commit()
