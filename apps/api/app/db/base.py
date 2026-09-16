"""
数据库与 Redis 连接管理。

- SQLAlchemy 2.0 async engine + async_sessionmaker
- Redis 用连接池，全局单例
- Redis 不可用时不阻塞启动（降级：限流关闭、验证码走内存 + 日志），避免本地开发被依赖卡死
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger("app.db")

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个会话，退出时确保关闭。"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ---------------- Redis ----------------

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.redis_max_connections,
            health_check_interval=30,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def ping_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用: %s", exc)
        return False


async def ping_db() -> bool:
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("数据库不可用: %s", exc)
        return False


async def raw_asyncpg_connection() -> Any:
    """init-db 用：需要一个能跑多语句 SQL 的裸连接。"""
    import asyncpg

    return await asyncpg.connect(settings.dsn)
