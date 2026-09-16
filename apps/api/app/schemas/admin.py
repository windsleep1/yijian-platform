"""管理端请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.admin_audit import AuditLogItem
from app.schemas.types import BigIntStr, BigIntStrOpt

ScopeType = Literal["global", "subject", "professional", "course"]


class AdminUserItem(BaseModel):
    id: BigIntStr
    phone: str | None = Field(None, description="已脱敏，如 138****8888")
    nickname: str = ""
    status: str = "active"
    roles: list[str] = Field(default_factory=list, description="角色 code 列表")
    register_source: str | None = None
    login_count: int = 0
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class AssignRolesIn(BaseModel):
    role_codes: list[str] = Field(
        ...,
        description="目标角色 code 列表，会整体替换用户现有角色。传空列表表示清空角色。",
        examples=[["researcher"]],
    )
    scope_type: ScopeType = Field("global", description="数据范围类型")
    scope_id: int | None = Field(None, description="scope_type 非 global 时必填，如专业/科目 ID")
    expires_at: datetime | None = Field(None, description="角色有效期，留空表示永久")

    @classmethod
    def model_validator_hint(cls) -> str:  # pragma: no cover - 仅用于文档说明
        return "scope_type != 'global' 时必须传 scope_id"


class RoleBrief(BaseModel):
    id: BigIntStr
    code: str
    name: str
    scope_type: str = "global"
    scope_id: BigIntStrOpt = None

    model_config = {"from_attributes": True}


class AssignRolesOut(BaseModel):
    user_id: BigIntStr
    roles: list[RoleBrief] = Field(default_factory=list)
    granted_permissions: list[str] = Field(default_factory=list)


class UserScopeBrief(BaseModel):
    role_code: str
    scope_type: str
    scope_id: BigIntStrOpt = None


class AdminUserDetail(BaseModel):
    """用户详情。

    手机号策略（B 端要点）：
      - `phone`      永远返回**脱敏**值；
      - `phone_full` **仅当调用者拥有 `user:export`** 时才有值，否则为 null。

    脱敏必须由后端决定。前端"隐藏"不等于防泄露——值仍然躺在响应体里，F12 可见。
    """

    id: BigIntStr
    phone: str | None = Field(None, description="脱敏手机号，如 138****8888")
    phone_full: str | None = Field(
        None, description="明文手机号。仅当调用者拥有 user:export 权限时返回，否则为 null"
    )
    email: str | None = None
    username: str | None = None
    real_name: str | None = None
    nickname: str = ""
    avatar_url: str | None = None
    status: str = "active"
    remark: str | None = None
    register_source: str | None = None
    register_ip: str | None = Field(None, description="INET 已转字符串")
    last_login_ip: str | None = Field(None, description="INET 已转字符串")
    last_login_at: datetime | None = None
    login_count: int = 0
    created_at: datetime | None = None
    profile: dict[str, Any] | None = None
    roles: list[str] = Field(default_factory=list)
    scopes: list[UserScopeBrief] = Field(default_factory=list)
    can_manage_roles: bool = Field(
        False, description="调用者是否拥有 user:manage。前端据此决定「分配角色」按钮可用性"
    )
    recent_audits: list[AuditLogItem] = Field(
        default_factory=list,
        description="该用户最近 10 条审计记录（无论作为操作人还是被操作对象）",
    )


__all__ = [
    "ScopeType",
    "AdminUserItem",
    "AssignRolesIn",
    "RoleBrief",
    "AssignRolesOut",
    "UserScopeBrief",
    "AdminUserDetail",
]
