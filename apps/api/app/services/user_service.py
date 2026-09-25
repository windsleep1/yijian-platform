"""
用户管理服务（管理端）。
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, not_found
from app.db.models import User, UserProfile
from app.schemas.admin import (
    AdminUserDetail,
    AdminUserItem,
    AssignRolesOut,
    RoleBrief,
    UserScopeBrief,
    UserStatusOut,
)
from app.schemas.auth import ProfileOut
from app.services import content_log_service, rbac_service
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
        (
            await db.execute(
                select(User)
                .where(*conditions)
                .order_by(User.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

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
        await db.execute(select(User).where(User.id == target_user_id, User.is_deleted.is_(False)))
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


def _json(value: Any) -> str:
    """JSONB 参数序列化。与 `exam_service._json` 同一口径（`ensure_ascii=False` + `default=str`）。"""
    return json.dumps(value, ensure_ascii=False, default=str)


async def update_user_status(
    db: AsyncSession,
    *,
    redis: aioredis.Redis | None,
    actor_id: int,
    actor_name: str | None,
    target_user_id: int,
    status: str,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> UserStatusOut:
    """启用 / 停用账号。**只开放 `active` ↔ `disabled`。**

    ## 边界：为什么 locked / deleted 不开管理员入口

    用户的要求是"**补齐管理员该有的入口**"，不是"补齐所有状态入口"：
    - `locked` —— **系统自动**（登录失败计数触发）。手工锁定会与自动解锁逻辑打架：
      管理员锁了，用户下次登录成功又被自动解开，或者反过来永远锁死。
    - `deleted`（注销）—— 要处理订单 / 权益 / 数据留存，是**一个流程**，不是一个字段。

    这两类传进来会 `40001`，并说明"归谁管"。

    ## 三道守卫（都是真实会出事的）

    1. **不能改自己** —— 管理员手滑停用自己，就把自己锁在门外了。
    2. **不能改 `super_admin`** —— 与「后台不允许提权」（`super_admin` 只能 CLI 授予）
       同一个原则：超管的生命周期由运维命令行管，不然后台可以互相停用。
    3. **不能改处于 `locked` / `deleted` 的账号** —— 见上，那两种状态不归本接口管。

    ## 停用是**即时生效**的（这一点容易写错，见下方更正说明）

    `deps.current_user` 对**每一个受保护请求**都会重新加载 user 并校验
    `status in ("disabled", "locked")`（`deps.py` → `unauthorized("账号状态异常", 40305)`）。
    所以停用后该用户的**下一个请求就会拿到 HTTP 401 / code 40305**，
    **不需要等 access token 过期**。

    > ⚠️ **更正**：本函数初版注释里写的是"已签发的 access token 在过期前仍然有效"，
    > **那是错的** —— 依据是"`auth_service` 只在登录时检查 status"，
    > 但漏看了 `deps.py` 里的请求级校验（两个地方各有一份 status 判断，
    > 正好是硬约定 A 说的"同一判断散落多处"）。
    > 写状态相关的能力说明前，**先把 status 的所有读路径 grep 一遍**。

    另外做两件事（都不再是"为了阻断访问"，而是语义与兜底）：

    - **吊销全部 refresh 会话**（`user_sessions.revoked_at = now()`）——
      语义是"**停用即注销该用户的所有设备**"，否则设备列表/审计里他一直显示为已登录；
      同时为将来把 status 校验挪到缓存留一道兜底。
    - **清权限缓存**（不等 TTL）。

    ## 幂等：**不幂等**

    已是目标状态 → `40901`。与 `publish` / `archive` 一致：
    **状态变更动作**重复执行说明调用方状态认知有问题，报错比静默吞掉好。
    """
    if status not in ("active", "disabled"):
        raise bad_request(
            "只允许设置 active / disabled。"
            "locked 由系统按登录失败自动写，deleted 走注销流程 —— 这两个不由管理员接口改。",
            40001,
        )

    target = (
        await db.execute(select(User).where(User.id == target_user_id, User.is_deleted.is_(False)))
    ).scalar_one_or_none()
    if target is None:
        raise not_found("用户不存在", 40401)

    # ---- 守卫 1：不能改自己 ----
    if int(target_user_id) == int(actor_id):
        raise bad_request(
            "不能修改自己的账号状态 —— 停用后你会立刻失去后台访问权限，且无法自行恢复。"
            "如确实需要，请让另一位管理员操作。",
            40001,
        )

    # ---- 守卫 2：不能改 super_admin ----
    actor_roles = await rbac_service.get_privileges(db, target_user_id, redis)
    if any(r["code"] == "super_admin" for r in actor_roles.roles):
        raise bad_request(
            "不能通过后台修改超级管理员的状态。"
            "super_admin 只能由运维命令行（seed-admin）授予与回收，"
            "否则后台可以互相停用、乃至无人能登录。",
            40001,
        )

    # ---- 守卫 3：locked / deleted 不归本接口管 ----
    if target.status in ("locked", "deleted"):
        raise conflict(
            f"该账号当前是「{target.status}」，不通过本接口变更。"
            + (
                "锁定由登录失败计数自动触发，登录成功会自动解除。"
                if target.status == "locked"
                else "注销要走注销流程（涉及订单与权益处理）。"
            ),
            40901,
        )

    if target.status == status:
        raise conflict("该账号已经是目标状态，无需重复操作。", 40901)

    before_status = target.status
    revoke_sessions = status == "disabled"

    await db.execute(
        text("UPDATE users SET status = :st, updated_at = now() WHERE id = :uid"),
        {"st": status, "uid": target_user_id},
    )

    revoked = 0
    if revoke_sessions:
        # 停用 → 吊销全部未过期且未吊销的 refresh 会话
        res = await db.execute(
            text(
                "UPDATE user_sessions SET revoked_at = now() "
                "WHERE user_id = :uid AND revoked_at IS NULL AND expires_at > now()"
            ),
            {"uid": target_user_id},
        )
        revoked = res.rowcount or 0
        await rbac_service.invalidate_privileges(redis, target_user_id)

    change_log = f"{'停用' if status == 'disabled' else '启用'}用户"
    if reason:
        change_log += f"：{reason}"

    # ---- 留痕 1：content_change_logs（用户明确要求，reason 记在这里）----
    # 2026-09-25 收口：原先这里是内联 SQL，与题库 / 试卷那两份是同一段逐字复制。
    await content_log_service.record_change(
        db,
        entity_type="user",
        entity_id=target_user_id,
        action="update",
        before={"status": before_status},
        after={"status": status, "reason": reason},
        change_log=change_log,
        operator_id=actor_id,
        ip=ip,
    )

    # ---- 留痕 2：audit_logs（与 assign_roles 一致，审计页能看到）----
    await write_audit(
        db,
        actor_id=actor_id,
        actor_name=actor_name,
        action="user.update_status",
        module="user",
        entity_type="user",
        entity_id=target_user_id,
        before={"status": before_status},
        after={"status": status, "reason": reason, "revoked_sessions": revoked},
        method="PATCH",
        path=f"/api/v1/admin/users/{target_user_id}/status",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()

    msg = f"已{'停用' if status == 'disabled' else '启用'}该账号。"
    if revoked:
        msg += f"同时注销了 {revoked} 个设备会话。"
    if status == "disabled":
        msg += "该用户的**下一个请求**即会被拒绝（HTTP 401 / 40305），无需等待令牌过期。"
    return UserStatusOut(
        user_id=target_user_id,
        status=status,
        previous_status=before_status,
        reason=reason,
        message=msg,
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
        await db.execute(select(User).where(User.id == user_id, User.is_deleted.is_(False)))
    ).scalar_one_or_none()
    if user is None:
        raise not_found("用户不存在", 40401)

    profile = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()

    # 角色 + 数据范围一次查全（比 _roles_map 多带回 scope_type / scope_id）
    scope_rows = (
        (
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
        )
        .mappings()
        .all()
    )

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
