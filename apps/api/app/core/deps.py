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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import admin_session_required, forbidden, too_many, unauthorized
from app.core.response import now_ms
from app.core.security import decode_token
from app.db.base import get_db, get_redis
from app.db.models import User, UserSession
from app.services import rbac_service
from app.services.rbac_service import PLATFORM_H5, PLATFORM_PC, Privileges

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
    #: 会话通道（`user_sessions.platform`）：`'pc'` = 管理端、`'h5'` = C 端（默认）。
    #: ⚠️ 在 `current_user` 里**查一次就带出来** —— 让 `/admin/*` 的会话墙直接读，
    #:    而不是自己再查一遍（**两处查询 = 两份真相**，一处改了另一处容易忘）。
    platform: str | None = None

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

    # ---- ★ 会话墙（BL-13 / `docs/22` §6.5.4）：**token 有效 ≠ 会话有效** ----
    # 在此之前 `current_user` **从不读 `user_sessions` 行** ⇒ 登出 / 撤销会话之后，
    # access token 仍然有效到自然过期（最长一整个 access TTL）。
    # ⚠️ 放在 `current_user` 里、而不是只加在 `/admin/*`：**一处实现、两个诉求** ——
    #    C 端从一开始就有"登出即时生效"，不用等将来"改 C 端登录接口 + 迁移已有会话"。
    # ⚠️ 成本：每个需登录请求多一次**按主键**的 SELECT（代价可控；
    #    将来若真成热点，正解是**加缓存**，而不是去掉这道校验）。
    #
    # ★ **无 `sid` 的令牌仍然放行**（有意为之，不是漏判）：
    #   `create_access_token` 的签名**强制** `session_id: int` ⇒ 服务端签发的令牌必然带 `sid`；
    #   能造出"无 sid 令牌"的只有**测试**（拿不到 `JWT_SECRET` 的人签不出来）。
    #   ⇒ 容忍它**不削弱**这道墙，却保住了 `tests/test_auth_session.py` 里那条
    #     "无 sid 令牌"边界用例的既有语义。
    #   ⚠️ **触发条件**：哪天出现"服务端签发的、合法但无 sid 的令牌类型"（如服务间令牌）
    #     ⇒ 那时必须改成**拒绝**，否则那类令牌会绕过整道墙。
    platform: str | None = None
    if session_id is not None:
        sess = await db.scalar(
            select(UserSession).where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > func.now(),
            )
        )
        if sess is None:
            raise unauthorized("登录已失效，请重新登录", 40105)
        platform = sess.platform

    priv = await rbac_service.get_privileges(db, user_id, redis)
    return CurrentUser(
        user=user,
        privileges=priv,
        session_id=session_id,
        jti=payload.get("jti"),
        platform=platform,
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


def require_admin_session(me: CurrentUserDep) -> CurrentUser:
    """`/admin/*` 的门：**在会话墙之上再加一层"这是管理端会话"**（`docs/22` §6.5）。

    ★ 为什么必须有它：**权限码是"能力"，`/admin/*` 要的是"身份"** ——
      "这是管理端会话"不能由"你恰好有某个权限码"推出来。
      判据（保留）：*把一个纯 C 端账号授上 `question:read`，它还能不能读后台？*
      在这道墙之前，答案是"**能**"。

    ★ 通道是 `user_sessions.platform`（`'pc'`），**不是** Redis、也**不是**额外 header：
      会话本来就在 PG 里，token 里也已带了它的主键（`sid`）—— 再存一份 = **两份真相**
      （还要同步 TTL）；多带一个 header 只是多一个"两边不一致"的故障模式。

    ⚠️ **这里判的不是权限码**：`platform` 是**客户端申报**的，所以"申请"那一步已经在登录时
      用**角色白名单**审过（`rbac_service.ADMIN_PLATFORM_ROLES`）；
      到这里只需要看这个会话**是不是**管理端会话。

    ⚠️ 挂载方式：`api/router.py` 里以 **router 级依赖**挂给 9 个 `admin_*.router` ——
      **一处挂**（不会漏），而不是在几十个路由上各写一遍。
    """
    if (me.platform or PLATFORM_H5) != PLATFORM_PC:
        raise admin_session_required()
    return me


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
