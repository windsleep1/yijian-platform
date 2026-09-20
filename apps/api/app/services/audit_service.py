"""
审计日志服务。

所有写操作都要留痕：谁、什么时候、从哪个 IP、改了什么。
题库/权限/订单相关的操作未来都要走这里。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idgen import next_id, to_inet
from app.db.models import AuditLog
from app.schemas.admin_audit import AuditLogItem

logger = logging.getLogger("app.audit")


async def write_audit(
    db: AsyncSession,
    *,
    actor_id: int | None,
    action: str,
    actor_name: str | None = None,
    module: str = "",
    entity_type: str | None = None,
    entity_id: int | None = None,
    before: Any = None,
    after: Any = None,
    method: str | None = None,
    path: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    success: bool = True,
    error_msg: str | None = None,
) -> None:
    """写一条审计记录。不抛出异常——审计失败不能影响主流程。"""
    try:
        db.add(
            AuditLog(
                id=next_id(),
                actor_id=actor_id,
                actor_name=(actor_name or "")[:64] or None,
                action=action[:64],
                module=module[:48],
                entity_type=entity_type,
                entity_id=entity_id,
                before_data=before,
                after_data=after,
                method=method,
                path=(path or "")[:255] or None,
                ip=to_inet(ip),
                user_agent=(user_agent or "")[:512] or None,
                success=success,
                error_msg=(error_msg or "")[:500] or None,
            )
        )
        await db.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "写审计日志失败 action=%s entity=%s/%s: %s", action, entity_type, entity_id, exc
        )


# ---------------------------------------------------------------- 查询


def _to_item(row: AuditLog) -> AuditLogItem:
    """ORM → 出参。

    注意：`ip` 列是 PostgreSQL `INET`，asyncpg 返回的是 `ipaddress.IPv4Address` **对象**，
    直接塞进 Pydantic 会在序列化阶段炸。这里统一转字符串。
    """
    return AuditLogItem(
        id=row.id,
        actor_id=row.actor_id,
        actor_name=row.actor_name,
        action=row.action,
        module=row.module,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        method=row.method,
        path=row.path,
        ip=str(row.ip) if row.ip is not None else None,
        success=row.success,
        error_msg=row.error_msg,
        before_data=row.before_data,
        after_data=row.after_data,
        created_at=row.created_at,
    )


def _conditions(
    *,
    actor_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    success: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Any]:
    conds: list[Any] = []
    if actor_id is not None:
        conds.append(AuditLog.actor_id == actor_id)
    if action:
        conds.append(AuditLog.action == action)
    if entity_type:
        conds.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        conds.append(AuditLog.entity_id == entity_id)
    if success is not None:
        conds.append(AuditLog.success.is_(success))
    if start is not None:
        conds.append(AuditLog.created_at >= start)
    if end is not None:
        # 半开区间 [start, end)，避免"同一天的 end"边界歧义
        conds.append(AuditLog.created_at < end)
    return conds


async def query_audit_logs(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    actor_id: int | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    success: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    order: str = "desc",
) -> tuple[list[AuditLogItem], int]:
    """按条件分页查询审计日志。

    索引对齐：`idx_audit_actor(actor_id, created_at DESC)` /
    `idx_audit_entity(entity_type, entity_id, created_at DESC)` /
    `idx_audit_action(action, created_at DESC)` 都已存在，走得到。
    **但只按时间范围过滤没有专用索引**（v0.1 接受：后台数据量可控），
    上线前按需补 `BRIN(created_at)`。
    """
    conds = _conditions(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        success=success,
        start=start,
        end=end,
    )

    total = int(
        (await db.execute(select(func.count()).select_from(AuditLog).where(*conds))).scalar_one()
    )

    order_col = AuditLog.created_at.asc() if order == "asc" else AuditLog.created_at.desc()
    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(*conds)
                # 同一毫秒内的顺序不稳定，用 id 兜底保证分页不重不漏
                .order_by(order_col, AuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    return [_to_item(r) for r in rows], total


async def recent_audits_for_user(
    db: AsyncSession, user_id: int, *, limit: int = 10
) -> list[AuditLogItem]:
    """某用户相关的最近 N 条审计：**他操作的** + **他被操作的**。"""
    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(
                    or_(
                        AuditLog.actor_id == user_id,
                        and_(AuditLog.entity_type == "user", AuditLog.entity_id == user_id),
                    )
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [_to_item(r) for r in rows]
