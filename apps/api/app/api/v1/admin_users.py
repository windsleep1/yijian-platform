"""管理端 · 用户与角色接口。

GET  /admin/users                      用户列表   —— 需要 user:read
GET  /admin/users/{user_id}             用户详情   —— 需要 user:read（Batch 3 新增）
PUT  /admin/users/{user_id}/roles      分配角色   —— 需要 user:manage

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
