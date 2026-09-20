"""认证接口。

POST /auth/sms/send        发送验证码
POST /auth/register        注册（注册即登录）
POST /auth/login/password  密码登录
POST /auth/login/sms       验证码登录（未注册自动注册）
POST /auth/refresh         刷新令牌（Rotation）
POST /auth/logout          登出
GET  /auth/me              当前用户 + 角色 + 权限
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.core.deps import CurrentUserDep, DbSession, RedisClient, client_ip, rate_limit
from app.core.response import Envelope, ok
from app.schemas.auth import (
    LogoutOut,
    MeOut,
    PasswordLoginIn,
    RefreshIn,
    RegisterIn,
    SmsLoginIn,
    SmsSendIn,
    SmsSendOut,
    TokenPairOut,
)
from app.services import auth_service, sms_service

router = APIRouter(prefix="/auth", tags=["认证"])

_USER_AGENT_MAX = 512


def _ua(request: Request) -> str | None:
    ua = request.headers.get("User-Agent", "")
    return ua[:_USER_AGENT_MAX] or None


# ---------------------------------------------------------------------
# 验证码
# ---------------------------------------------------------------------


@router.post(
    "/sms/send",
    response_model=Envelope[SmsSendOut],
    summary="发送短信验证码",
    dependencies=[Depends(rate_limit("sms", limit=20, window_seconds=60, by="ip"))],
)
async def send_sms_code(payload: SmsSendIn, request: Request, redis: RedisClient) -> dict:
    expires_in, dev_code = await sms_service.send_code(
        redis, phone=payload.phone, scene=payload.scene, ip=client_ip(request)
    )
    return ok(SmsSendOut(expires_in=expires_in, dev_code=dev_code))


# ---------------------------------------------------------------------
# 注册 / 登录
# ---------------------------------------------------------------------


@router.post(
    "/register",
    response_model=Envelope[TokenPairOut],
    summary="注册（注册即登录）",
    dependencies=[Depends(rate_limit("register", limit=10, window_seconds=60, by="ip"))],
)
async def register(
    payload: RegisterIn, request: Request, db: DbSession, redis: RedisClient
) -> dict:
    tokens = await auth_service.register(
        db, redis, payload, ip=client_ip(request), user_agent=_ua(request)
    )
    return ok(tokens, message="注册成功")


@router.post(
    "/login/password",
    response_model=Envelope[TokenPairOut],
    summary="密码登录",
    description="连续 5 次密码错误将锁定账号 15 分钟。",
    dependencies=[Depends(rate_limit("login_pwd", limit=20, window_seconds=60, by="ip"))],
)
async def login_password(
    payload: PasswordLoginIn, request: Request, db: DbSession, redis: RedisClient
) -> dict:
    tokens = await auth_service.login_by_password(
        db, redis, payload, ip=client_ip(request), user_agent=_ua(request)
    )
    return ok(tokens, message="登录成功")


@router.post(
    "/login/sms",
    response_model=Envelope[TokenPairOut],
    summary="验证码登录",
    description="手机号未注册时自动创建账号并授予 student 角色。",
    dependencies=[Depends(rate_limit("login_sms", limit=20, window_seconds=60, by="ip"))],
)
async def login_sms(
    payload: SmsLoginIn, request: Request, db: DbSession, redis: RedisClient
) -> dict:
    tokens = await auth_service.login_by_sms(
        db, redis, payload, ip=client_ip(request), user_agent=_ua(request)
    )
    return ok(tokens, message="登录成功")


# ---------------------------------------------------------------------
# 令牌
# ---------------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=Envelope[TokenPairOut],
    summary="刷新令牌",
    description="Refresh Token Rotation：每次刷新都作废旧凭证并签发新凭证。",
    dependencies=[Depends(rate_limit("refresh", limit=30, window_seconds=60, by="ip"))],
)
async def refresh(payload: RefreshIn, request: Request, db: DbSession, redis: RedisClient) -> dict:
    tokens = await auth_service.refresh_tokens(
        db, redis, payload.refresh_token, ip=client_ip(request), user_agent=_ua(request)
    )
    return ok(tokens)


@router.post(
    "/logout",
    response_model=Envelope[LogoutOut],
    summary="登出",
    description="`all_devices=true` 时撤销该用户全部设备的登录态。",
)
async def logout(
    request: Request,
    db: DbSession,
    redis: RedisClient,
    me: CurrentUserDep,
    all_devices: bool = False,
) -> dict:
    revoked = await auth_service.logout(
        db,
        user_id=me.id,
        session_id=me.session_id,
        redis=redis,
        ip=client_ip(request),
        user_agent=_ua(request),
        actor_name=me.display_name,
        all_devices=all_devices,
    )
    return ok(LogoutOut(revoked_sessions=revoked), message="已退出登录")


# ---------------------------------------------------------------------
# 当前用户
# ---------------------------------------------------------------------


@router.get(
    "/me",
    response_model=Envelope[MeOut],
    summary="当前用户信息",
    description="返回账号、学习档案、角色、权限码列表与数据范围。前端据此渲染菜单与按钮权限。",
)
async def get_me(db: DbSession, redis: RedisClient, me: CurrentUserDep, response: Response) -> dict:
    # 权限随时可能被后台调整，这里禁用前端强缓存
    response.headers["Cache-Control"] = "no-store"
    data = await auth_service.build_me(db, me.user, redis)
    return ok(data)
