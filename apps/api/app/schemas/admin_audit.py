"""管理端 · 审计日志响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.types import BigIntStr, BigIntStrOpt


class AuditLogItem(BaseModel):
    id: BigIntStr
    actor_id: BigIntStrOpt = Field(None, description="操作人用户 ID")
    actor_name: str | None = None
    action: str = Field(..., description="动作码，如 user.assign_roles")
    module: str = ""
    entity_type: str | None = Field(None, description="被操作对象类型，如 user")
    entity_id: BigIntStrOpt = None
    method: str | None = None
    path: str | None = None
    ip: str | None = Field(None, description="已由 INET 转成字符串")
    success: bool = True
    error_msg: str | None = None
    before_data: Any | None = Field(None, description="变更前快照（JSONB）")
    after_data: Any | None = Field(None, description="变更后快照（JSONB）")
    created_at: datetime | None = None


__all__ = ["AuditLogItem"]
