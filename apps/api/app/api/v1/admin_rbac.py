"""管理端 · 角色与权限接口。

GET /admin/roles        角色列表（下拉 / 分配角色弹窗用）   —— 需要 user:read
GET /admin/permissions  权限树（按 module 分组）             —— 需要 user:read

**权限为什么用 `user:read` 而不是 `system:role`**
    这两份数据本质是「用户管理页」的参考数据。而按种子数据，`operator` 有 `user:read`
    但**没有** `system:role`；若收紧成 `system:role`，operator 能进用户页却拉不到角色下拉，
    页面直接残废（这正是 §4.3 说的"页面能不能用"与"权限收得紧不紧"的取舍）。

**若未来权限明细变敏感，应拆成两个接口**：
    GET /admin/roles/brief                 只返回 id / code / name（下拉够用，保持 user:read）
    GET /admin/roles/{role_id}/permissions  单独取某角色的权限明细（收紧为 system:role）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, require_permission
from app.core.response import Envelope, ok
from app.schemas.admin_rbac import PermissionTreeOut, RoleItem
from app.services import rbac_service

router = APIRouter(prefix="/admin", tags=["管理端 · 角色与权限"])


@router.get(
    "/roles",
    response_model=Envelope[list[RoleItem]],
    summary="角色列表",
    description=(
        "角色下拉与「分配角色」弹窗的数据源。需要权限 `user:read`。\n\n"
        "- `is_assignable=false` 的角色（`super_admin`）**前端应直接置灰并说明原因**，"
        "而不是让用户点了提交才吃 `40003`。\n"
        "- `permissions` 是该角色包含的权限码，用于弹窗里展示「这个角色能干什么」。"
        "不需要时传 `include_permissions=false`，可少一次 JOIN。"
    ),
    dependencies=[Depends(require_permission("user:read"))],
)
async def list_roles(
    db: DbSession,
    include_permissions: Annotated[
        bool, Query(description="是否带上每个角色的权限码")
    ] = True,
) -> dict:
    items = await rbac_service.list_roles_with_permissions(
        db, include_permissions=include_permissions
    )
    return ok([i.model_dump() for i in items])


@router.get(
    "/permissions",
    response_model=Envelope[PermissionTreeOut],
    summary="权限树",
    description=(
        "按模块分组的权限树。需要权限 `user:read`。\n\n"
        "**实现说明**：`permissions` 表有 `parent_id`，但当前种子数据 `parent_id` 全为 NULL，"
        "24 条权限是扁平的，层级靠 `module` + `code` 的 `module:action` 约定表达。\n"
        "所以这里的策略是「优先按 `parent_id` 递归，缺失时按 `module` 兜底」——"
        "将来真接了菜单树（`type='menu'` 的节点填上 `parent_id`），本接口不用改。"
    ),
    dependencies=[Depends(require_permission("user:read"))],
)
async def permission_tree(
    db: DbSession,
    module: Annotated[str | None, Query(max_length=48, description="只取某个模块，如 user")] = None,
) -> dict:
    tree, total = await rbac_service.build_permission_tree(db, module=module)
    return ok(PermissionTreeOut(total=total, tree=tree).model_dump())
