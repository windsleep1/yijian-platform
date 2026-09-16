"""
依赖注入：数据库会话、Redis、当前用户、权限校验、限流。

权限校验用法：

    @router.get("/admin/users", dependencies=[Depends(require_permission("user:read"))])
    async def list_users(...): ...

    # 或者需要拿到当前用户对象
    async def list_users(me: CurrentUser = Depends(current_user)):
        ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Callable

import redis.asyncio as aioredis
from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, too_many, unauthorized
from app.core.response import now_ms
from app.core.security import decode_token
from app.db.base import get_db, get_redis
from app.db.models import User
from app.services import rbac_service
from app.services.rbac_service import Privileges

logger = logging.getLogger("app.deps")

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def redis_client() -> aioredis.Redis:
    return get_redis()


RedisClient = Annotated[aioredis.Redis, Depends(redis_client)]


def client_ip(request: Request) -> str | None:
    """取真实客户端 IP。部署在 Nginx 后面时以 X-Forwarded-For 首个地址为准。"""
    from app.core.config import settings

    if settings.trust_proxy_headers:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        real = request.headers.get("X-Real-IP")
        if real:
            return real.strip()
    return request.client.host if request.client else None


@dataclass
class CurrentUser:
    """当前登录用户 + 权限快照。"""

    user: User
    privileges: Privileges
    session_id: int | None = None
    jti: str | None = None

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def roles(self) -> list[str]:
        return self.privileges.role_codes

    @property
    def permissions(self) -> set[str]:
        return self.privileges.permission_set

    @property
    def scopes(self) -> list[dict]:
        """数据范围（`user_roles.scope_type / scope_id`）。

        透出成属性，是为了让 service 层的 `ScopeViewer` 协议能直接读到它 ——
        service 不允许 import `core.deps`，只认结构（见 question_service 的分层注记）。
        """
        return self.privileges.scopes

    @property
    def display_name(self) -> str:
        return self.user.nickname or self.user.phone or str(self.user.id)


async def current_user(
    request: Request,
    db: DbSession,
    redis: RedisClient,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    """解析 Bearer Token，加载用户与权限。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("请先登录", 40100)

    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token, expected_type="access")

    user_id = int(payload["sub"])
    session_id = int(payload["sid"]) if payload.get("sid") else None

    from app.services.auth_service import get_user_by_id

    user = await get_user_by_id(db, user_id)
    if user is None:
        raise unauthorized("账号不存在或已注销", 40104)
    if user.status in ("disabled", "locked"):
        raise unauthorized("账号状态异常，请联系客服", 40305)

    priv = await rbac_service.get_privileges(db, user_id, redis)
    return CurrentUser(
        user=user, privileges=priv, session_id=session_id, jti=payload.get("jti")
    )


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def require_permission(*codes: str, require_all: bool = False) -> Callable:
    """
    权限校验依赖工厂。

    require_permission("user:read")                    任一命中即通过
    require_permission("system:role", require_all=True) 需要全部命中
    """

    async def _checker(me: CurrentUserDep) -> CurrentUser:
        if not codes:
            return me
        ok = me.privileges.has_all(*codes) if require_all else me.privileges.has(*codes)
        if not ok:
            raise forbidden(f"没有操作权限（需要：{'、'.join(codes)}）", 40301)
        return me

    return _checker


def rate_limit(
    name: str,
    *,
    limit: int,
    window_seconds: int = 60,
    by: str = "user",
) -> Callable:
    """
    固定窗口限流依赖。

    by="user" 按登录用户；by="ip" 按客户端 IP。
    Redis 不可用时不阻断请求（降级），只记日志——限流不该成为可用性单点。
    """

    async def _limiter(request: Request, redis: RedisClient) -> None:
        identity: str | None
        if by == "ip":
            identity = client_ip(request)
        else:
            auth = request.headers.get("Authorization", "")
            identity = auth[-24:] if auth else client_ip(request)
        if not identity:
            return

        window = int(now_ms() // (window_seconds * 1000))
        key = f"rl:{name}:{identity}:{window}"
        # 只有 Redis 交互会被降级：任何异常都放行，限流不该成为可用性单点。
        # 注意 too_many() 是构造函数而非异常类，必须放在 try 外面 raise。
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_seconds + 1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("限流检查失败，放行请求: %s", exc)
            return

        if count > limit:
            raise too_many()

    return _limiter


async def pagination(
    page: Annotated[int, Query(ge=1, le=10000, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数，最大 100")] = 20,
) -> tuple[int, int]:
    return page, page_size


Pagination = Annotated[tuple[int, int], Depends(pagination)]
