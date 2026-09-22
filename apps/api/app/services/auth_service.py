"""
认证服务：注册、登录、刷新、登出。

设计要点
--------
1. refresh token 只把 **SHA-256 摘要** 落库（user_sessions.refresh_token），
   数据库泄露也无法直接冒用登录态。
2. 刷新走 Rotation：每次刷新撤销旧会话并新建会话，旧 token 立即失效。
3. 密码登录失败计数存 Redis，连续失败 5 次锁定 15 分钟。
4. 短信登录对未注册手机号自动注册（降低门槛），并自动授予 student 角色。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import bad_request, conflict, forbidden, too_many, unauthorized
from app.core.idgen import next_id, to_inet
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    sha256_hex,
    verify_password,
)
from app.db.models import User, UserProfile, UserSession
from app.schemas.auth import (
    MeOut,
    PasswordLoginIn,
    ProfileOut,
    RegisterIn,
    ScopeOut,
    SmsLoginIn,
    TokenPairOut,
)
from app.services import rbac_service, sms_service
from app.services.audit_service import write_audit

logger = logging.getLogger("app.auth")

FAIL_KEY = "auth:fail:{phone}"
LOCK_KEY = "auth:lock:{phone}"
DEFAULT_ROLE = "student"
MAX_UA_LEN = 512


# =====================================================================
# 内部工具
# =====================================================================


async def get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    """按手机号取**未注销**用户。

    ⚠️ 只查 `is_deleted = false` 是**有意的**：注销即**释放手机号**
    （2026-09-22 明确，见 `docs/04` §2.1「账号状态与注销后手机号释放」）。
    于是已注销的手机号再次短信登录会走"未注册 → 自动建号" —— 这是**设计**，不是漏洞。
    """
    rows = await db.execute(select(User).where(User.phone == phone, User.is_deleted.is_(False)))
    return rows.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    rows = await db.execute(select(User).where(User.id == user_id, User.is_deleted.is_(False)))
    return rows.scalar_one_or_none()


def _assert_usable(user: User) -> None:
    if user.status == "disabled":
        raise forbidden("账号已被禁用，请联系客服", 40305)
    if user.status == "locked":
        raise forbidden("账号已被锁定，请联系客服", 40305)
    if user.status == "deleted":
        raise unauthorized("账号不存在或已注销", 40104)


async def _issue_tokens(
    db: AsyncSession,
    *,
    user: User,
    ip: str | None,
    user_agent: str | None,
    device_id: str | None = None,
    device_name: str | None = None,
    platform: str | None = None,
    app_version: str | None = None,
) -> TokenPairOut:
    """创建会话并签发令牌对。"""
    role_codes = await _user_role_codes(db, user.id)
    session_id = next_id()

    access_token, expires_in = create_access_token(
        user_id=user.id, session_id=session_id, roles=role_codes
    )
    refresh_token, refresh_expires_at = create_refresh_token(user_id=user.id, session_id=session_id)

    db.add(
        UserSession(
            id=session_id,
            user_id=user.id,
            refresh_token=sha256_hex(refresh_token),
            device_id=(device_id or "")[:128] or None,
            device_name=(device_name or "")[:128] or None,
            platform=(platform or "h5")[:24],
            app_version=(app_version or "")[:24] or None,
            ip=to_inet(ip),
            user_agent=(user_agent or "")[:MAX_UA_LEN] or None,
            expires_at=refresh_expires_at,
        )
    )

    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = to_inet(ip)
    user.login_count = (user.login_count or 0) + 1
    await db.flush()

    me = await build_me(db, user, redis=None)
    return TokenPairOut(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        user=me,
    )


async def _user_role_codes(db: AsyncSession, user_id: int) -> list[str]:
    rows = await db.execute(
        text(
            """
            SELECT r.code FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = :uid AND (ur.expires_at IS NULL OR ur.expires_at > now())
            """
        ),
        {"uid": user_id},
    )
    return list(rows.scalars().all())


async def build_me(db: AsyncSession, user: User, redis: aioredis.Redis | None = None) -> MeOut:
    profile = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    ).scalar_one_or_none()
    priv = await rbac_service.get_privileges(db, user.id, redis)

    return MeOut(
        id=user.id,
        phone=user.phone,
        nickname=user.nickname or "",
        avatar_url=user.avatar_url,
        status=user.status,
        roles=priv.role_codes,
        permissions=priv.permissions,
        scopes=[ScopeOut(**s) for s in priv.scopes],
        profile=ProfileOut.model_validate(profile) if profile else None,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


async def _create_user(
    db: AsyncSession,
    *,
    phone: str,
    nickname: str | None,
    password: str | None,
    register_source: str,
    ip: str | None,
) -> User:
    user = User(
        id=next_id(),
        phone=phone,
        nickname=(nickname or "").strip() or f"考友{phone[-4:]}",
        password_hash=hash_password(password) if password else None,
        status="active",
        register_source=register_source,
        register_ip=to_inet(ip),
        is_deleted=False,
    )
    db.add(user)
    await db.flush()

    # 学习档案与账号解耦，注册即建，避免后续到处判空
    db.add(UserProfile(user_id=user.id, exam_level="yijian"))
    await db.flush()

    await rbac_service.ensure_role_assigned(db, user_id=user.id, role_code=DEFAULT_ROLE)
    return user


# =====================================================================
# 对外用例
# =====================================================================


async def register(
    db: AsyncSession,
    redis: aioredis.Redis,
    payload: RegisterIn,
    *,
    ip: str | None,
    user_agent: str | None,
) -> TokenPairOut:
    if await get_user_by_phone(db, payload.phone):
        raise conflict("该手机号已注册，请直接登录", 40901)

    await sms_service.verify_code(redis, phone=payload.phone, scene="register", code=payload.code)

    user = await _create_user(
        db,
        phone=payload.phone,
        nickname=payload.nickname,
        password=payload.password,
        register_source=payload.platform or "h5",
        ip=ip,
    )
    await write_audit(
        db,
        actor_id=user.id,
        actor_name=user.nickname,
        action="auth.register",
        module="auth",
        entity_type="user",
        entity_id=user.id,
        after={"phone": payload.phone, "role": DEFAULT_ROLE},
        method="POST",
        path="/api/v1/auth/register",
        ip=ip,
        user_agent=user_agent,
    )

    tokens = await _issue_tokens(
        db,
        user=user,
        ip=ip,
        user_agent=user_agent,
        device_id=payload.device_id,
        device_name=payload.device_name,
        platform=payload.platform,
        app_version=payload.app_version,
    )
    await db.commit()
    return tokens


async def login_by_password(
    db: AsyncSession,
    redis: aioredis.Redis,
    payload: PasswordLoginIn,
    *,
    ip: str | None,
    user_agent: str | None,
) -> TokenPairOut:
    lock_key = LOCK_KEY.format(phone=payload.phone)
    if await redis.exists(lock_key):
        ttl = await redis.ttl(lock_key)
        raise too_many(f"密码错误次数过多，请 {max(ttl, 1)} 秒后再试", 42903)

    user = await get_user_by_phone(db, payload.phone)
    # 统一提示，避免通过错误文案枚举已注册手机号
    if user is None or not verify_password(payload.password, user.password_hash):
        fail_key = FAIL_KEY.format(phone=payload.phone)
        fails = await redis.incr(fail_key)
        if fails == 1:
            await redis.expire(fail_key, settings.login_lock_seconds)
        if fails >= settings.login_max_failures:
            await redis.set(lock_key, "1", ex=settings.login_lock_seconds)
            await redis.delete(fail_key)
            raise too_many("密码错误次数过多，账号已临时锁定 15 分钟", 42903)
        raise unauthorized("手机号或密码不正确", 40103)

    _assert_usable(user)
    await redis.delete(FAIL_KEY.format(phone=payload.phone))

    tokens = await _issue_tokens(
        db,
        user=user,
        ip=ip,
        user_agent=user_agent,
        device_id=payload.device_id,
        device_name=payload.device_name,
        platform=payload.platform,
        app_version=payload.app_version,
    )
    await db.commit()
    return tokens


async def login_by_sms(
    db: AsyncSession,
    redis: aioredis.Redis,
    payload: SmsLoginIn,
    *,
    ip: str | None,
    user_agent: str | None,
) -> TokenPairOut:
    await sms_service.verify_code(redis, phone=payload.phone, scene="login", code=payload.code)

    user = await get_user_by_phone(db, payload.phone)
    if user is None:
        # 未注册直接建号：一建考生多是在工地扫码进来，多一步注册就多一层流失。
        #
        # ⚠️ 这里同时是「**注销后手机号被释放**」的落点（2026-09-22 明确为有意行为）：
        #    已注销（`is_deleted=true`）的手机号再次短信登录会**建一个新号**，
        #    既不复用旧号也不报错。理由：还没到支付/试用阶段，防白嫖不存在；
        #    释放符合用户预期与合规精神，代码也更简单。
        #    见 `docs/04` §2.1（含「引入试用/权益前重新评估」的待办）。
        user = await _create_user(
            db,
            phone=payload.phone,
            nickname=None,
            password=None,
            register_source=payload.platform or "h5",
            ip=ip,
        )
        await write_audit(
            db,
            actor_id=user.id,
            actor_name=user.nickname,
            action="auth.auto_register",
            module="auth",
            entity_type="user",
            entity_id=user.id,
            after={"phone": payload.phone, "channel": "sms_login"},
            ip=ip,
            user_agent=user_agent,
        )

    _assert_usable(user)
    tokens = await _issue_tokens(
        db,
        user=user,
        ip=ip,
        user_agent=user_agent,
        device_id=payload.device_id,
        device_name=payload.device_name,
        platform=payload.platform,
        app_version=payload.app_version,
    )
    await db.commit()
    return tokens


async def refresh_tokens(
    db: AsyncSession,
    redis: aioredis.Redis,
    refresh_token: str,
    *,
    ip: str | None,
    user_agent: str | None,
) -> TokenPairOut:
    payload = decode_token(refresh_token, expected_type="refresh")
    token_hash = sha256_hex(refresh_token)

    session = (
        await db.execute(
            select(UserSession).where(
                UserSession.refresh_token == token_hash,
                UserSession.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if session is None:
        # 可能是重放攻击：token 合法但已被轮换过，直接拒绝
        raise unauthorized("登录凭证已失效，请重新登录", 40105)
    if session.expires_at <= datetime.now(timezone.utc):
        raise unauthorized("登录凭证已过期，请重新登录", 40101)

    user = await get_user_by_id(db, int(payload["sub"]))
    if user is None:
        raise unauthorized("账号不存在或已注销", 40104)
    _assert_usable(user)

    # 撤销旧会话，签发新会话（Rotation）
    session.revoked_at = datetime.now(timezone.utc)
    await db.flush()

    tokens = await _issue_tokens(
        db,
        user=user,
        ip=ip,
        user_agent=user_agent,
        device_id=session.device_id,
        device_name=session.device_name,
        platform=session.platform,
        app_version=session.app_version,
    )
    await db.commit()
    return tokens


async def logout(
    db: AsyncSession,
    *,
    user_id: int,
    session_id: int | None,
    redis: aioredis.Redis | None,
    ip: str | None,
    user_agent: str | None,
    actor_name: str | None = None,
    all_devices: bool = False,
) -> int:
    """登出。返回被撤销的会话数。"""
    stmt = select(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if not all_devices:
        if session_id is None:
            raise bad_request("缺少会话标识，无法登出", 40001)
        stmt = stmt.where(UserSession.id == session_id)

    sessions = list((await db.execute(stmt)).scalars().all())
    now = datetime.now(timezone.utc)
    for s in sessions:
        s.revoked_at = now
    await write_audit(
        db,
        actor_id=user_id,
        actor_name=actor_name,
        action="auth.logout_all" if all_devices else "auth.logout",
        module="auth",
        entity_type="user",
        entity_id=user_id,
        after={"revoked_sessions": len(sessions)},
        method="POST",
        path="/api/v1/auth/logout",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return len(sessions)
