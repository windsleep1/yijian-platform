"""管理端 · 用户与角色接口。

GET  /admin/users                      用户列表   —— 需要 user:read
GET  /admin/users/{user_id}             用户详情   —— 需要 user:read（Batch 3 新增）
PUT  /admin/users/{user_id}/roles      分配角色   —— 需要 user:manage
PATCH /admin/users/{user_id}/status    停用/启用  —— 需要 user:manage（仅 active ↔ disabled）

这几个接口是验证 RBAC 的样本：
同一份 Token，用 user:read 能过列表接口但过不了分配接口；
用 user:manage 才能过分配接口，且操作会写入 audit_logs。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import (
    CurrentUserDep,
    DbSession,
    RedisClient,
    client_ip,
    pagination,
    require_permission,
)
from app.core.response import Envelope, Page, ok, paginate
from app.schemas.admin import (
    AdminUserDetail,
    AdminUserItem,
    AssignRolesIn,
    AssignRolesOut,
    UserStatusOut,
    UserStatusUpdateIn,
)
from app.services import user_service

router = APIRouter(prefix="/admin/users", tags=["管理端 · 用户"])


@router.get(
    "",
    response_model=Envelope[Page[AdminUserItem]],
    summary="用户列表",
    description="手机号默认脱敏展示。需要权限 `user:read`。",
    dependencies=[Depends(require_permission("user:read"))],
)
async def list_users(
    db: DbSession,
    page_info: Annotated[tuple[int, int], Depends(pagination)],
    keyword: Annotated[str | None, Query(max_length=64, description="按手机号/昵称/用户名模糊搜索")] = None,
    status: Annotated[str | None, Query(description="active / disabled / locked")] = None,
) -> dict:
    page, page_size = page_info
    items, total = await user_service.list_users(
        db, page=page, page_size=page_size, keyword=keyword, status=status
    )
    return ok(
        paginate(
            [i.model_dump() for i in items],
            page=page,
            page_size=page_size,
            total=total,
        )
    )


@router.put(
    "/{user_id}/roles",
    response_model=Envelope[AssignRolesOut],
    summary="分配用户角色",
    description=(
        "整体替换用户角色（非追加）。需要权限 `user:manage`。\n\n"
        "可分配角色：admin / researcher / teacher / operator / student。\n"
        "super_admin 只能通过 CLI 授予，避免后台越权提权。\n"
        "操作会写入 audit_logs，并立即清除该用户的权限缓存。"
    ),
    dependencies=[Depends(require_permission("user:manage"))],
)
async def assign_roles(
    user_id: int,
    payload: AssignRolesIn,
    request: Request,
    db: DbSession,
    redis: RedisClient,
    me: CurrentUserDep,
) -> dict:
    result = await user_service.assign_user_roles(
        db,
        redis=redis,
        actor_id=me.id,
        actor_name=me.display_name,
        target_user_id=user_id,
        role_codes=payload.role_codes,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        expires_at=payload.expires_at,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(result, message="角色已更新")


@router.patch(
    "/{user_id}/status",
    response_model=Envelope[UserStatusOut],
    summary="停用 / 启用账号",
    description=(
        "需要权限 `user:manage`。**只接受 `active` / `disabled` 两项。**\n\n"
        "## 为什么只开这两个值\n"
        "目标是「补齐**管理员该有的**入口」，不是「补齐所有状态入口」：\n"
        "- `locked` —— **系统自动**写（登录失败计数触发），手工锁定会与自动解锁互相打架；\n"
        "- `deleted` —— 注销是**一个流程**（要处理订单 / 权益 / 数据留存），不是一个字段。\n\n"
        "传这两个值会 `40001`，并说明归谁管。\n\n"
        "## 三道守卫\n"
        "1. **不能改自己** —— 手滑停用自己就锁在门外了；\n"
        "2. **不能改 `super_admin`** —— 与「后台不允许提权」同一原则，否则后台能互相停用；\n"
        "3. **不能改 `locked` / `deleted` 的账号** —— 不归本接口管。\n\n"
        "## 停用**即时生效**（不是「等令牌过期」）\n"
        "`deps.current_user` 对**每个受保护请求**都会重新加载 user 并校验 status，\n"
        "所以停用后该用户的**下一个请求就会拿到 HTTP 401 / code 40305**。\n\n"
        "> ⚠️ 这条一开始被写错过：初版注释说「已签发的 access token 在过期前仍有效」，\n"
        "> 依据是「auth_service 只在登录时检查 status」—— 但漏看了 `deps.py` 里的请求级校验。\n"
        "> **同一个判断散落在两处**（正是硬约定 A 说的那种），只看一处就会得出错误结论。\n\n"
        "另外还会：\n"
        "- **注销该用户的全部设备会话**（`user_sessions.revoked_at`）——\n"
        "  语义是「停用即登出所有设备」，否则设备列表里他一直显示为已登录；\n"
        "- **清权限缓存**（不等 TTL）。\n\n"
        "`reason` 可选，会写进 `content_change_logs`（`change_log` 与 `diff.after.reason`）\n"
        "与 `audit_logs`。**已是目标状态时返回 `40901`**（状态变更动作不幂等，\n"
        "与 publish / archive 一致）。"
    ),
    dependencies=[Depends(require_permission("user:manage"))],
)
async def update_user_status(
    user_id: int,
    payload: UserStatusUpdateIn,
    request: Request,
    db: DbSession,
    redis: RedisClient,
    me: CurrentUserDep,
) -> dict:
    out = await user_service.update_user_status(
        db,
        redis=redis,
        actor_id=me.id,
        actor_name=me.display_name,
        target_user_id=user_id,
        status=payload.status,
        reason=payload.reason,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(out.model_dump(), message=out.message)


@router.get(
    "/{user_id}",
    response_model=Envelope[AdminUserDetail],
    summary="用户详情",
    description=(
        "需要权限 `user:read`。\n\n"
        "- **不存在的 id 返回 `40401`**，不返回空对象（返回 `{}` + 200 会让前端画出一片空白，"
        "用户以为系统坏了）。\n"
        "- `phone` 永远返回**脱敏**值；`phone_full` **仅当调用者拥有 `user:export`** 时才有值，"
        "否则为 null。脱敏由后端决定——前端隐藏不算数。\n"
        "- 附带该用户最近 10 条审计记录（作为操作人 **或** 被操作对象）。\n"
        "- `can_manage_roles` 告诉前端「分配角色」按钮该不该可用。"
    ),
    dependencies=[Depends(require_permission("user:read"))],
)
async def get_user(
    user_id: int,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    detail = await user_service.get_user_detail(
        db, user_id=user_id, viewer_permissions=me.permissions
    )
    return ok(detail.model_dump())
