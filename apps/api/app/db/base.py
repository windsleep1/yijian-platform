"""
数据库与 Redis 连接管理。

- SQLAlchemy 2.0 async engine + async_sessionmaker
- Redis 用连接池，全局单例

★★ **Redis 各用途的降级矩阵**（2026-10-01 实测核对，**不要凭印象**）：

    | 用途                     | Redis 挂掉时                      | 出处 |
    |--------------------------|-----------------------------------|------|
    | 限流                     | **放行**（只记日志，不阻断请求）   | `core/deps.py::_limiter` |
    | RBAC 权限缓存            | **回源数据库**                    | `services/rbac_service.py` |
    | **短信验证码**（注册/登录）| **503 `unavailable`** —— 用不了   | `services/sms_service.py` |
    | **登录锁定**              | 抛异常 ⇒ 登录失败                 | `services/auth_service.py` |

⚠️ **这里以前写的是"Redis 不可用时不阻塞启动（降级：限流关闭、**验证码走内存 + 日志**）"
—— 那句里的"验证码走内存"是**假的**：全仓 grep 不到任何内存实现，
`_send_code` / `verify_code` 只对 Redis 读写，异常时直接抛 `unavailable`。
（本地之所以"看起来能降级"，是因为本地跑的是 `tools/local-verify/serve_fake_redis.py`
—— 那是一个**真的 Redis 服务**，不是应用内的兜底。）

⇒ 准确的结论是：**应用能启动**（`docker-entrypoint.sh` 把 wait-redis 做成非致命、
`/api/v1/health` 报 `degraded`），**但登录/注册必须依赖 Redis**。
所以托管部署**必须配一个 Redis**（免费档可用 Upstash），见 `docs/27`。

（教训同族：「文档声称 ≠ 已验证」—— 承诺写在注释里，而没人测过它是否成立。）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger("app.db")


def asyncpg_connect_args() -> dict[str, Any]:
    """给**裸 asyncpg** 的连接参数（SQLAlchemy 那侧走 `connect_args=`，见下面的 `engine`）。

    ★ SSL 为什么必须在这里显式传：`core/config.py::normalize_database_url` 已经把 URL 上的
      `sslmode` **摘掉**了（那是 libpq 的参数，asyncpg 不认）。也就是说"要不要加密"
      这个信息**只能从连接参数回来** —— 摘掉却不传，托管 PG（Neon/Supabase）连不上。

    ⚠️ 用 `True`（= require）而**不是**字符串 `"require"`：`True / False / None / SSLContext`
      是 asyncpg 明确的取值；字符串形式能不能被接受，**本机没有托管 PG 就验不了**，
      所以不赌（判据：不依赖一个我无法证伪的默认行为）。
      代价：**只保证加密，不校验证书链**。对"Render → Neon"这个部署形态是合适的取舍；
      需要校验证书就把这里换成 `ssl.create_default_context(cafile=...)`。
    """
    return {"ssl": True} if settings.db_ssl_require else {}


engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args=asyncpg_connect_args(),
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
    """init-db 用：需要一个能跑多语句 SQL 的裸连接。

    ⚠️ **必须带上 `asyncpg_connect_args()`**（里面有 `ssl`）：`dsn` 已经被
    `normalize_database_url` 摘掉了 `sslmode`，不带的话托管 PG 连不上。
    """
    import asyncpg

    return await asyncpg.connect(settings.dsn, **asyncpg_connect_args())
