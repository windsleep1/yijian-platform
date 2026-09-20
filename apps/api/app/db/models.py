"""
ORM 模型 —— **只映射认证 / RBAC / 审计这 8 张表**（Batch 2 引入，后续批次未扩充）。

| 已映射（本文件） | 未映射（走原生 SQL） |
|---|---|
| `users` `user_profiles` `user_sessions` `roles` `permissions` `role_permissions` `user_roles` `audit_logs` | `questions` `chapters` `knowledge_points` `import_batches` `content_change_logs` `paper_rules` `exams` … |

Batch 4 起的题库 / 导入服务**一律用 `text()` 原生 SQL**（`question_service.py` / `import_service.py`），
不是漏写了 ORM —— 那些查询带大量动态筛选、窗口函数与批量 upsert，ORM 表达反而更长更难读。
所以查表结构请以 `db/schema.sql` 为唯一真相，**不要**以为"models.py 里没有 = 库里没有"。

表结构以 db/schema.sql 为准，本文件是它的 ORM 映射。
字段名与列名一一对应，不做重命名，避免出现两套真相。

涉及表：
    users, user_profiles, user_sessions,
    roles, permissions, role_permissions, user_roles,
    audit_logs
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# =====================================================================
# 用户
# =====================================================================


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64))
    password_hash: Mapped[str | None] = mapped_column(String(128))
    nickname: Mapped[str] = mapped_column(String(64), default="")
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    real_name: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    register_source: Mapped[str] = mapped_column(String(32), default="h5")
    register_ip: Mapped[Any | None] = mapped_column(INET)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[Any | None] = mapped_column(INET)
    login_count: Mapped[int] = mapped_column(Integer, default=0)
    remark: Mapped[str | None] = mapped_column(String(255))
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} phone={self.phone}>"


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    gender: Mapped[str | None] = mapped_column(String(8))
    province: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(32))
    exam_level: Mapped[str] = mapped_column(String(16), default="yijian")
    professional: Mapped[str | None] = mapped_column(String(24))
    exam_year: Mapped[int | None] = mapped_column(SmallInteger)
    target_subjects: Mapped[Any] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    target_score: Mapped[int | None] = mapped_column(SmallInteger)
    daily_goal_min: Mapped[int] = mapped_column(SmallInteger, default=30)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    refresh_token: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[str | None] = mapped_column(String(128))
    device_name: Mapped[str | None] = mapped_column(String(128))
    platform: Mapped[str | None] = mapped_column(String(24))
    app_version: Mapped[str | None] = mapped_column(String(24))
    ip: Mapped[Any | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# =====================================================================
# RBAC
# =====================================================================


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    sort_no: Mapped[int] = mapped_column(Integer, default=0)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(96))
    name: Mapped[str] = mapped_column(String(96))
    type: Mapped[str] = mapped_column(String(16), default="api")
    module: Mapped[str] = mapped_column(String(48), default="")
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    sort_no: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("permissions.id"), primary_key=True
    )


class UserRole(Base):
    """用户角色，带数据范围（scope），支持有效期。"""

    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"))
    scope_type: Mapped[str] = mapped_column(String(24), default="global")
    scope_id: Mapped[int | None] = mapped_column(BigInteger)
    granted_by: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# =====================================================================
# 审计
# =====================================================================


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_name: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    module: Mapped[str] = mapped_column(String(48), default="")
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    before_data: Mapped[Any | None] = mapped_column(JSONB)
    after_data: Mapped[Any | None] = mapped_column(JSONB)
    method: Mapped[str | None] = mapped_column(String(8))
    path: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[Any | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    error_msg: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Base",
    "User",
    "UserProfile",
    "UserSession",
    "Role",
    "Permission",
    "RolePermission",
    "UserRole",
    "AuditLog",
]
