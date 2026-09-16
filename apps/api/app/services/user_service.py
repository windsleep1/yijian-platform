"""
用户管理服务（管理端）。
"""

from __future__ import annotations

import redis.asyncio as aioredis
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.db.models import User, UserProfile
from app.schemas.admin import (
    AdminUserDetail,
    AdminUserItem,
    AssignRolesOut,
    RoleBrief,
    UserScopeBrief,
)
from app.schemas.auth import ProfileOut
from app.services import rbac_service
from app.services.audit_service import recent_audits_for_user, write_audit


def mask_phone(phone: str | None) -> str | None:
    """手机号脱敏：138****8888。管理端列表默认不展示完整手机号。"""
    if not phone or len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


async def _roles_map(db: AsyncSession, user_ids: list[int]) -> dict[int, list[str]]:
    if not user_ids:
        return {}
    rows = await db.execute(
        text(
            """
            SELECT ur.user_id, r.code
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = ANY(CAST(:uids AS bigint[]))
              AND (ur.expires_at IS NULL OR ur.expires_at > now())
            ORDER BY r.sort_no
            """
        ),
        {"uids": user_ids},
    )
    result: dict[int, list[str]] = {}
    for uid, code in rows.all():
        result.setdefault(uid, []).append(code)
    return result


async def list_users(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: str | None = None,
) -> tuple[list[AdminUserItem], int]:
    conditions = [User.is_deleted.is_(False)]
    if status:
        conditions.append(User.status == status)
    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(
            or_(User.phone.ilike(kw), User.nickname.ilike(kw), User.username.ilike(kw))
        )

    total = (
        await db.execute(select(func.count()).select_from(User).where(*conditions))
    ).scalar_one()

    rows = (
        await db.execute(
            select(User)
            .where(*conditions)
            .order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    role_map = await _roles_map(db, [u.id for u in rows])
    items = [
        AdminUserItem(
            id=u.id,
            phone=mask_phone(u.phone),
            nickname=u.nickname or "",
            status=u.status,
            roles=role_map.get(u.id, []),
            register_source=u.register_source,
            login_count=u.login_count or 0,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u in rows
    ]
    return items, total


async def assign_user_roles(
    db: AsyncSession,
    *,
    redis: aioredis.Redis | None,
    actor_id: int,
    actor_name: str | None,
    target_user_id: int,
    role_codes: list[str],
    scope_type: str = "global",
    scope_id: int | None = None,
    expires_at=None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AssignRolesOut:
    target = (
        await db.execute(
            select(User).where(User.id == target_user_id, User.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if target is None:
        raise not_found("用户不存在", 40401)

    before = (await rbac_service.get_privileges(db, target_user_id, redis)).roles

    priv = await rbac_service.assign_roles(
        db,
        redis=redis,
        user_id=target_user_id,
        role_codes=role_codes,
        scope_type=scope_type,
        scope_id=scope_id,
        granted_by=actor_id,
        expires_at=expires_at,
    )

    await write_audit(
        db,
        actor_id=actor_id,
        actor_name=actor_name,
        action="user.assign_roles",
        module="user",
        entity_type="user",
        entity_id=target_user_id,
        before={"roles": before},
        after={
            "roles": priv.roles,
            "scope_type": scope_type,
            "scope_id": scope_id,
        },
        method="PUT",
        path=f"/api/v1/admin/users/{target_user_id}/roles",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()

    return AssignRolesOut(
        user_id=target_user_id,
        roles=[RoleBrief(**r) for r in priv.roles],
        granted_permissions=priv.permissions,
    )


async def get_user_detail(
    db: AsyncSession, *, user_id: int, viewer_permissions: set[str]
) -> AdminUserDetail:
    """用户详情（管理端）。

    - 不存在的 id → `40401`（`not_found`），**绝不返回空对象**
      （B 端的坑：返回 `{}` + 200 会让前端画出满屏空白，用户以为系统坏了）
    - 手机号：`phone` **永远脱敏**；`phone_full` 仅当调用者拥有 `user:export` 才有值
    - 附带该用户最近 10 条审计（作为操作人 **或** 被操作对象）

    脱敏必须在服务端完成。前端把字符盖住不算数——`phone_full` 若下发，
    F12 里就是明文。
    """
    user = (
        await db.execute(
            select(User).where(User.id == user_id, User.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if user is None:
        raise not_found("用户不存在", 40401)

    profile = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()

    # 角色 + 数据范围一次查全（比 _roles_map 多带回 scope_type / scope_id）
    scope_rows = (
        await db.execute(
            text(
                """
                SELECT r.code AS role_code, ur.scope_type, ur.scope_id
                FROM user_roles ur
                JOIN roles r ON r.id = ur.role_id
                WHERE ur.user_id = :uid
                  AND (ur.expires_at IS NULL OR ur.expires_at > now())
                ORDER BY r.sort_no
                """
            ),
            {"uid": user_id},
        )
    ).mappings().all()

    can_export = "user:export" in viewer_permissions

    return AdminUserDetail(
        id=user.id,
        phone=mask_phone(user.phone),
        phone_full=user.phone if can_export else None,
        email=user.email,
        username=user.username,
        real_name=user.real_name,
        nickname=user.nickname or "",
        avatar_url=user.avatar_url,
        status=user.status,
        remark=user.remark,
        register_source=user.register_source,
        # INET 列 → 字符串，否则 Pydantic 序列化会炸
        register_ip=str(user.register_ip) if user.register_ip is not None else None,
        last_login_ip=str(user.last_login_ip) if user.last_login_ip is not None else None,
        last_login_at=user.last_login_at,
        login_count=user.login_count or 0,
        created_at=user.created_at,
        profile=ProfileOut.model_validate(profile).model_dump() if profile else None,
        roles=[r["role_code"] for r in scope_rows],
        scopes=[
            UserScopeBrief(
                role_code=r["role_code"], scope_type=r["scope_type"], scope_id=r["scope_id"]
            )
            for r in scope_rows
        ],
        can_manage_roles="user:manage" in viewer_permissions,
        recent_audits=await recent_audits_for_user(db, user_id, limit=10),
    )
