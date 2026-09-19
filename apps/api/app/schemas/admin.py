"""管理端请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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


class UserStatusUpdateIn(BaseModel):
    """改用户状态。**只开放 `active` ↔ `disabled` 两个值。**

    ## 为什么只开这两个（而不是把 4 个状态全开）

    用户的目标是"**补齐管理员该有的入口**"，不是"补齐所有状态入口"。
    `users.status` 的四个值里，只有两个属于管理员的日常操作：

    | 值 | 谁写 | 为什么 |
    |---|---|---|
    | `active` / `disabled` | **管理员**（本接口） | 停用/恢复一个账号是日常运维动作 |
    | `locked` | **系统自动** | 登录失败计数触发，管理员手工锁定会与自动解锁逻辑打架 |
    | `deleted` | **注销流程** | 注销要处理订单/权益/数据留存，不能是"改个字段" |

    传 `locked` / `deleted` 会 `40001` 拒绝并说明原因 ——
    **不是"忘了实现"，是刻意的边界**。
    """

    status: Literal["active", "disabled"] = Field(..., description="只接受 active / disabled")
    reason: str | None = Field(
        None,
        max_length=200,
        description="停用原因（可选，写入 content_change_logs 与 audit_logs）",
    )

    @model_validator(mode="before")
    @classmethod
    def _explain_system_only_states(cls, data: Any) -> Any:
        """`locked` / `deleted` 要**说清归谁管**，而不是让 Pydantic 甩一句枚举不匹配。

        ⚠️ 不加这一条会怎样：`Literal` 校验先命中，调用方只看到
        「Input should be 'active' or 'disabled'」——**看不出这是刻意的边界**，
        于是很容易被当成"忘了实现"，下一个人顺手把 `locked` 加进枚举就出事了。

        这与 `ExamUpdateIn._reject_sections` 是同一个套路：
        **被拒绝的输入，要把"为什么不给"和"该找谁"讲出来。**
        """
        if isinstance(data, dict):
            v = data.get("status")
            if v == "locked":
                raise ValueError(
                    "只接受 active / disabled。"
                    "locked 不能由管理员设置：它由系统按登录失败次数自动写入，"
                    "人工锁定会与自动解锁逻辑冲突。"
                )
            if v == "deleted":
                raise ValueError(
                    "只接受 active / disabled。"
                    "deleted（注销）不走这个接口：注销涉及订单、权益与数据留存处理，"
                    "是一个流程而不是改一个字段。"
                )
        return data


class UserStatusOut(BaseModel):
    user_id: BigIntStr
    status: str
    previous_status: str
    reason: str | None = None
    message: str


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
    "UserStatusUpdateIn",
    "UserStatusOut",
]
