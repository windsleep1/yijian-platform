"""C 端 · 科目（引导页的"选专业"数据源）。

    GET /subjects

⚠️ **需要登录**（`current_user`）—— 它是 C 端接口，走 P0 的会话墙。
   公开科目列表对"营销页"可能有用，但那是**另一个需求**：现在公开它就等于
   在会话墙之外开一个口子，而第一个消费方（引导页）本来就在登录之后。
   将来真要做营销页，**那时**再决定它是公开还是独立接口。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.core.deps import CurrentUserDep, DbSession
from app.core.response import Envelope, ok
from app.schemas.c_end import SubjectOut
from app.services import subject_service

router = APIRouter(prefix="/subjects", tags=["C 端 · 科目"])


@router.get(
    "",
    response_model=Envelope[list[SubjectOut]],
    summary="科目列表",
    description=(
        "需要登录。默认返回一级建造师的**全部启用科目**（公共课 + 专业课），按 `sort_no` 排序。\n\n"
        "- `category=professional` → 只返回专业课（**引导页的「选专业」用这个**）；\n"
        "  `category=public` → 只返回公共课。\n"
        "- `professional` 字段是专业课的所属专业码（`jz`/`sz`…），公共课为 `null`。\n"
        "- 只返回 `status='on'` 的科目（下线科目选了也没题可做）。"
    ),
)
async def list_subjects(
    db: DbSession,
    _me: CurrentUserDep,
    exam_level: Annotated[str, Query(description="考试级别：yijian / erjian")] = "yijian",
    category: Annotated[
        Literal["public", "professional"] | None,
        Query(description="科目类别；不传 = 全部"),
    ] = None,
) -> dict:
    items = await subject_service.list_subjects(db, exam_level=exam_level, category=category)
    return ok([s.model_dump() for s in items])
