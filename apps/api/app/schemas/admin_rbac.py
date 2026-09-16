"""管理端 · 角色与权限的响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.types import BigIntStr


class RoleItem(BaseModel):
    id: BigIntStr
    code: str
    name: str
    description: str | None = None
    is_system: bool = False
    sort_no: int = 0
    is_assignable: bool = Field(
        True,
        description=(
            "是否可由后台分配。`super_admin` 为 false（只能由 CLI 授予），"
            "前端应直接置灰并说明原因，而不是等提交后被 40003 拒绝。"
        ),
    )
    permissions: list[str] = Field(
        default_factory=list,
        description="该角色包含的权限码。`include_permissions=false` 时为空数组",
    )


class PermissionNode(BaseModel):
    key: str = Field(..., description="树节点唯一键（`m:<module>` / `p:<id>`），前端做展开状态用")
    code: str = Field(..., description="module 节点用模块名，权限节点用权限码")
    name: str
    type: str = Field("api", description="module / menu / page / button / api / data")
    module: str = ""
    sort_no: int = 0
    children: list[PermissionNode] = Field(default_factory=list)


class PermissionTreeOut(BaseModel):
    total: int = Field(..., description="权限条目总数（不含 module 聚合节点）")
    tree: list[PermissionNode] = Field(default_factory=list)


PermissionNode.model_rebuild()

__all__ = ["RoleItem", "PermissionNode", "PermissionTreeOut"]
