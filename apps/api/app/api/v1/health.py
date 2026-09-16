"""健康检查与元信息。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.core.response import Envelope, ok
from app.db.base import ping_db, ping_redis

router = APIRouter(tags=["系统"])


class HealthOut(Envelope[dict]):
    pass


@router.get(
    "/health",
    response_model=Envelope[dict],
    summary="健康检查",
    description="探活应用、PostgreSQL、Redis。任一依赖不可用时 status 为 degraded，便于编排系统判断。",
)
async def health() -> dict:
    db_ok = await ping_db()
    redis_ok = await ping_redis()
    healthy = db_ok and redis_ok
    return ok(
        {
            "status": "ok" if healthy else "degraded",
            "app": settings.app_name,
            "version": settings.app_version,
            "env": settings.app_env,
            "dependencies": {
                "postgres": "up" if db_ok else "down",
                "redis": "up" if redis_ok else "down",
            },
            "sms_provider": settings.sms_provider,
        }
    )
