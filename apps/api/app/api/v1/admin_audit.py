"""管理端 · 审计日志接口。

GET /admin/audit-logs   审计日志查询 + 筛选 + 分页   —— 需要 system:audit
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, pagination, require_permission
from app.core.errors import bad_request
from app.core.response import Envelope, Page, ok, paginate
from app.schemas.admin_audit import AuditLogItem
from app.services import audit_service

router = APIRouter(prefix="/admin/audit-logs", tags=["管理端 · 审计"])


@router.get(
    "",
    response_model=Envelope[Page[AuditLogItem]],
    summary="审计日志列表",
    description=(
        "按 操作人 / 动作 / 对象 / 结果 / 时间范围 筛选 + 分页。需要权限 `system:audit`。\n\n"
        "- 时间范围是**半开区间** `[start, end)`，避免「同一天的 end」这类边界歧义。\n"
        "- 只允许按 `created_at` 排序——审计日志不该被任意重排。\n"
        "- 列表页只展示 action / entity / actor / 时间；`before_data` / `after_data` "
        "供详情抽屉做 diff 视图。\n\n"
        "**查询本身不写审计**（否则自己审计自己，日志指数膨胀）。只审计写操作。"
    ),
    dependencies=[Depends(require_permission("system:audit"))],
)
async def list_audit_logs(
    db: DbSession,
    page_info: Annotated[tuple[int, int], Depends(pagination)],
    actor_id: Annotated[int | None, Query(description="操作人用户 ID")] = None,
    action: Annotated[
        str | None, Query(max_length=64, description="动作码，精确匹配，如 user.assign_roles")
    ] = None,
    entity_type: Annotated[
        str | None, Query(max_length=32, description="对象类型，如 user")
    ] = None,
    entity_id: Annotated[int | None, Query(description="对象 ID")] = None,
    success: Annotated[bool | None, Query(description="true 只看成功 / false 只看失败")] = None,
    start: Annotated[datetime | None, Query(description="起始时间（含），ISO8601")] = None,
    end: Annotated[datetime | None, Query(description="结束时间（不含），ISO8601")] = None,
    order: Annotated[Literal["asc", "desc"], Query(description="按时间排序方向")] = "desc",
) -> dict:
    page, page_size = page_info

    if start is not None and end is not None and start > end:
        raise bad_request("start 不能晚于 end", 40001)

    items, total = await audit_service.query_audit_logs(
        db,
        page=page,
        page_size=page_size,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        success=success,
        start=start,
        end=end,
        order=order,
    )
    return ok(
        paginate(
            [i.model_dump() for i in items],
            page=page,
            page_size=page_size,
            total=total,
        )
    )
